"""The local server behind the board: JSON API, long-poll for Claude, live events for the viewer.

One server serves every board on this machine (boards are files under <home>/boards/). It binds
127.0.0.1 only, and refuses requests whose Host or Origin is not this server, so another web page
cannot read or post to it.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import re
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .store import Invalid, Store, home

# Windows registry entries can map these to text/plain, which browsers refuse for stylesheets.
# add_type runs after mimetypes' own init, so it overrides the registry.
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("text/javascript", ".js")

try:
    import fcntl

    def lock(f, wait=True):
        """Take an exclusive lock on open file `f`; with wait=False, raise OSError if it is taken."""
        fcntl.flock(f, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
except ImportError:  # Windows
    import msvcrt

    def lock(f, wait=True):
        """Take an exclusive lock on open file `f`; with wait=False, raise OSError if it is taken."""
        while True:
            try:
                return msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                if not wait:
                    raise
                time.sleep(0.1)

STATIC = Path(__file__).parent / "static"
PORT = 43210


def code_stamp():
    """Changes whenever the installed code changes, so a stale server can be noticed and replaced."""
    here = Path(__file__).parent
    h = hashlib.sha1(str(here.resolve()).encode())
    for p in sorted([*here.glob("*.py"), *STATIC.glob("*")]):
        if p.is_file():
            st = p.stat()
            h.update(f"\0{p.name}\0{st.st_size}\0{st.st_mtime_ns}".encode())
    return h.hexdigest()[:12]


def server_file() -> Path:
    return home() / "server.json"


def server_info():
    try:
        return json.loads(server_file().read_text())
    except (OSError, ValueError):
        return None


class Hub:
    """Fan-out of server events to every open viewer (Server-Sent Events)."""

    def __init__(self):
        self.clients = set()
        self.lock = threading.Lock()

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def send(self, event, data=None):
        with self.lock:
            for q in self.clients:
                q.put((event, data))


class App:
    def __init__(self, port):
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self.hub = Hub()
        self.store = Store(on_change=lambda bid: self.hub.send("change", {"board": bid}))
        self.stamp = code_stamp()
        self.lock = threading.Lock()
        self.waiters = {}    # board id -> number of `rails-board wait` connected
        self.last_wait = {}  # board id -> when one last connected or left
        self.last_request = time.time()
        self.idle_timeout = 0
        self.stop = lambda: None

    def listening(self):
        """Boards Claude is listening on. A waiter that just left is usually reconnecting."""
        now = time.time()
        with self.lock:
            return sorted(b for b in set(self.waiters) | set(self.last_wait)
                          if self.waiters.get(b, 0) > 0 or now - self.last_wait.get(b, 0) < 4)

    def presence_loop(self):
        last = None
        while True:
            cur = self.listening()
            if cur != last:
                self.hub.send("presence", {"listening": cur})
                last = cur
            time.sleep(1)

    def idle_loop(self):
        while True:
            time.sleep(30)
            with self.lock:
                busy = any(n > 0 for n in self.waiters.values())
            if self.idle_timeout and not busy and not self.hub.clients \
                    and time.time() - self.last_request > self.idle_timeout:
                print("rails-board: idle, stopping", flush=True)
                threading.Thread(target=self.stop, daemon=True).start()
                return


def make_handler(app: App):
    allowed = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def send_json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_static(self, name):
            path = (STATIC / name).resolve()
            if STATIC.resolve() not in path.parents or not path.is_file():
                return self.send_json({"error": "not found"}, 404)
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, (dict, list)):
                raise Invalid("expected a JSON object")
            return data

        def trusted(self):
            if self.headers.get("Host") not in allowed:
                return False
            # Browsers mark every request with where it came from, navigations and image loads included,
            # which carry no Origin. The CLI sends no such header.
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in {f"http://{h}" for h in allowed}

        def route(self, method):
            if not self.trusted():
                return self.send_json({"error": "forbidden"}, 403)
            url = urlparse(self.path)
            path, q = url.path, parse_qs(url.query)
            try:
                if method == "GET" and not path.startswith("/api/"):
                    return self.send_static("index.html" if path == "/" else path.lstrip("/"))
                if path != "/api/events":
                    app.last_request = time.time()
                out = self.api(method, path, q)
                if out is not None:
                    self.send_json(out)
            except KeyError as e:
                self.send_json({"error": str(e.args[0])}, 404)
            except (Invalid, json.JSONDecodeError) as e:
                self.send_json({"error": str(e)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # answer 500 rather than drop the request  # noqa: BLE001
                self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

        def do_GET(self):  # noqa: N802
            self.route("GET")

        def do_POST(self):  # noqa: N802
            self.route("POST")

        def api(self, method, path, q):
            s = app.store
            if method == "GET":
                if path == "/api/status":
                    return {"ok": True, "url": app.base_url, "pid": os.getpid(), "stamp": app.stamp,
                            "listening": app.listening(), "boards": len(s.boards), "home": str(home())}
                if path == "/api/boards":
                    return {"boards": s.list(), "listening": app.listening()}
                if path == "/api/wait":
                    return self.wait(q["board"][0], float(q.get("timeout", ["25"])[0]), True)  # always leased
                if path == "/api/events":
                    return self.events()
                if m := re.fullmatch(r"/api/boards/([^/]+)", path):
                    return s.get(m[1])
                if m := re.fullmatch(r"/api/boards/([^/]+)/awaiting", path):
                    return {"issues": s.awaiting(m[1])}
            if method == "POST":
                data = self.body()
                if path == "/api/stop":
                    threading.Thread(target=app.stop, daemon=True).start()
                    return {"ok": True}
                if path == "/api/boards":
                    return s.create(data)
                if m := re.fullmatch(r"/api/boards/([^/]+)/remove", path):
                    s.remove(m[1])
                    return {"ok": True}
                if m := re.fullmatch(r"/api/boards/([^/]+)/issues", path):
                    return {"added": s.add_issues(m[1], data.get("findings") or data.get("items"))}
                if m := re.fullmatch(r"/api/boards/([^/]+)/ack", path):
                    return s.confirm(m[1], data.get("delivery"))
                if m := re.fullmatch(r"/api/boards/([^/]+)/handoff", path):
                    return s.handoff(m[1])
                if m := re.fullmatch(r"/api/boards/([^/]+)/issues/([^/]+)", path):
                    return s.update_issue(m[1], m[2], data)
                if m := re.fullmatch(r"/api/boards/([^/]+)/issues/([^/]+)/withdraw", path):
                    return s.withdraw(m[1], m[2])
                if m := re.fullmatch(r"/api/boards/([^/]+)/issues/([^/]+)/messages", path):
                    return s.add_message(m[1], m[2], data.get("author", "user"), data.get("text"),
                                         data.get("propose"), data.get("note"), data.get("verdict"))
            raise KeyError("not found")

        def wait(self, bid, timeout, confirms):
            s = app.store
            s.get(bid)  # 404 for an unknown board rather than waiting on it forever
            with app.lock:
                app.waiters[bid] = app.waiters.get(bid, 0) + 1
                app.last_wait[bid] = time.time()
            try:
                return s.take_pending(bid, timeout, confirms=confirms)
            finally:
                with app.lock:
                    app.waiters[bid] -= 1
                    app.last_wait[bid] = time.time()

        def events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            q = app.hub.subscribe()
            try:
                hello = {"listening": app.listening(), "stamp": app.stamp}
                self.wfile.write(f"event: presence\ndata: {json.dumps(hello)}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        event, data = q.get(timeout=15)
                        self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                app.hub.unsubscribe(q)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A viewer tab closing mid-request is routine; print only real errors.
        if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)


def bind(port=None):
    first = port or int(os.environ.get("RAILS_BOARD_PORT") or PORT)
    for p in [port] if port else range(first, first + 20):
        try:
            return Server(("127.0.0.1", p), BaseHTTPRequestHandler), p
        except OSError:
            continue
    raise SystemExit(f"rails-board: no free port in {first}–{first + 19}")


def only_server():
    """Hold this data folder's server lock for as long as the process lives, so there is never a second
    server over the same boards. Returns the open lock file, or None if another server holds it. Waits
    a moment first, because a server that was just told to stop is still letting go."""
    home().mkdir(parents=True, exist_ok=True)
    f = open(home() / "server.lock", "w")  # noqa: SIM115 - kept open on purpose
    for _ in range(40):
        try:
            lock(f, wait=False)
            return f
        except OSError:
            time.sleep(0.1)
    f.close()
    return None


def serve(port=None, open_browser=False, idle_timeout=0):
    lock = only_server()
    if lock is None:
        print("rails-board: another server already has this data folder; not starting a second one", flush=True)
        return
    srv, port = bind(port)
    app = App(port)
    app.idle_timeout = idle_timeout
    app.stop = srv.shutdown
    srv.RequestHandlerClass = make_handler(app)
    threading.Thread(target=app.presence_loop, daemon=True).start()
    threading.Thread(target=app.idle_loop, daemon=True).start()
    f = server_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"url": app.base_url, "port": port, "pid": os.getpid(), "stamp": app.stamp}, indent=2))
    print(f"rails-board: open {app.base_url}  ·  boards in {app.store.dir}", flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(app.base_url)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # so a kill still removes server.json
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        info = server_info()
        if info and info.get("pid") == os.getpid():
            server_file().unlink(missing_ok=True)
