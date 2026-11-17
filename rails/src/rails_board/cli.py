"""`rails-board`: put review findings on a local board, and let Claude Code answer the user's questions on it.

    rails-board new findings.json --open      create a board from findings (starts the server if needed)
    rails-board wait --board ID               block until the user writes on the board (run in the background)
    rails-board reply F3 - --board ID < r.md  answer in that issue's thread
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import server
from .store import (
    OUTCOME_LABEL,
    OUTCOMES,
    STATUS_LABEL,
    STATUSES,
    VERDICT_LABEL,
    VERDICTS,
    home,
    status_label,
)


class Fail(Exception):
    pass


# -- talking to the server --
def call(url, data=None, timeout=10):
    req = urllib.request.Request(url, method="POST" if data is not None else "GET")
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, body, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = None
        raise Fail(msg or f"server answered {e.code}") from None


def running():
    """The live server's status, or None."""
    info = server.server_info()
    if not info:
        return None
    try:
        return call(f"{info['url']}/api/status", timeout=2)
    except (urllib.error.URLError, ConnectionError, TimeoutError, Fail, ValueError):
        return None


def start_server():
    home().mkdir(parents=True, exist_ok=True)
    with open(home() / "server.log", "ab") as log:
        subprocess.Popen([sys.executable, "-m", "rails_board", "serve"], stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))  # Windows ignores start_new_session
    for _ in range(50):
        time.sleep(0.1)
        if (st := running()) and st.get("stamp") == server.code_stamp():
            return st
    if st := running():
        raise Fail(f"the server running older code (pid {st['pid']}, {st['url']}) did not stop when asked, so the new "
                   f"code could not start. Stop it (kill {st['pid']}, or Task Manager on Windows) and run the command again.")
    raise Fail(f"the server did not start; see {home() / 'server.log'}")


def ensure_server():
    """The server's URL. Starts one if none is running, and replaces one running older code."""
    st = running()
    if st and st.get("stamp") == server.code_stamp():
        return st["url"]  # the usual case: nothing to start, nothing to lock
    # Starting or replacing is done by one command at a time. Commands that begin together queue here,
    # and all but the first find the server the first one started.
    home().mkdir(parents=True, exist_ok=True)
    with open(home() / "start.lock", "w") as lock:
        server.lock(lock)
        st = running()
        if st and st.get("stamp") != server.code_stamp():
            try:
                call(f"{st['url']}/api/stop", {}, timeout=3)
            except (urllib.error.URLError, ConnectionError, TimeoutError, Fail):
                pass
            for _ in range(30):
                time.sleep(0.1)
                if not running():
                    break
            st = None
        return (st or start_server())["url"]


def find_board(url, args):
    bid = getattr(args, "board", None) or os.environ.get("RAILS_BOARD")
    boards = call(f"{url}/api/boards")["boards"]
    if bid:
        hits = [b for b in boards if b["id"] == bid] or [b for b in boards if b["id"].startswith(bid)]
        if len(hits) != 1:
            raise Fail(f"no board {bid!r}. Boards: " + (", ".join(b["id"] for b in boards) or "none"))
    else:
        cwd = Path.cwd().resolve()
        hits = [b for b in boards if b["cwd"] and (Path(b["cwd"]) == cwd or Path(b["cwd"]) in cwd.parents)]
        if not hits:
            raise Fail("no board was made from this folder; pass --board ID (see `rails-board boards`)")
    args.kind = hits[0].get("kind", "review")  # newest first
    return hits[0]["id"]


def review_only(args, bid):
    """Refuse a command that only makes sense on a review when the board is a plain list."""
    if args.kind == "list":
        raise Fail(f"{bid} is a list, not a review: `{args.cmd}` does not apply. Nothing on a list is sent to "
                   "Claude; use `show`, `add` and `move`.")


def read_text(arg):
    return sys.stdin.read() if arg == "-" else arg


def read_json(arg):
    try:
        return json.loads(sys.stdin.read() if arg in (None, "-") else Path(arg).read_text())
    except ValueError as e:
        raise Fail(f"that isn't valid JSON: {e}") from None
    except OSError as e:
        raise Fail(str(e)) from None


# -- printing for Claude --
def clock(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def where(i):
    if not i.get("file"):
        return ""
    a, b = i.get("line_start"), i.get("line_end")
    return i["file"] + (f":{a}" + (f"-{b}" if b and b != a else "") if a else "")


def state(i):
    s = STATUS_LABEL[i["status"]]
    if i["status"] == "settled":
        s += f" as {OUTCOME_LABEL[i['outcome']]}"
    return s


def indent(text, pad="    "):
    return "\n".join(pad + line for line in (text or "").splitlines()) or pad + "(empty)"


def format_issue(i, new=()):
    """One issue in full: finding, triage, and the thread with each message under its ID (F3#1, F3#2…)."""
    out = [f"[{i['key']}] {i['title']}"]
    meta = [f"severity {i['severity']}", where(i), state(i),
            f"Claude {VERDICT_LABEL[i['verdict']]}" if i.get("verdict") else "not triaged yet"]
    out.append("  " + " · ".join(m for m in meta if m))
    if i.get("note"):
        out.append(f"  Decision note: {i['note']}")
    out += ["  Finding (verbatim from the reviewer):", indent(i["body"])]
    if i.get("recommendation"):
        out += ["  Reviewer recommends:", indent(i["recommendation"])]
    if i.get("triage"):
        out += ["  Claude's triage:", indent(i["triage"])]
    if p := i.get("proposal"):
        out.append(f"  Claude proposed settling as {OUTCOME_LABEL[p['outcome']]}" + (f": {p['note']}" if p.get("note") else ""))
    if i["messages"]:
        out.append("  Thread:")
        for m in i["messages"]:
            who = "Claude" if m["author"] == "claude" else "User"
            mark = "NEW → " if m["id"] in new else ""
            out.append(f"    {mark}{i['key']}#{m['n']} {who} {clock(m['ts'])}:")
            out.append(indent(m["text"], "      "))
    return "\n".join(out)


def format_item(i):
    """One card of a list in full: its details and the user's notes on it."""
    out = [f"[{i['key']}] {i['title']}", "  " + status_label("list", i["status"])]
    if i.get("body"):
        out += ["  Details:", indent(i["body"])]
    if i["messages"]:
        out.append("  Notes:")
        for m in i["messages"]:
            out.append(f"    {i['key']}#{m['n']} {clock(m['ts'])}:")
            out.append(indent(m["text"], "      "))
    return "\n".join(out)


def instruction(i):
    """What Claude said it would do for the outcome the issue was settled as: the note on its latest
    suggestion, if that suggestion is for this outcome. Empty when the user chose something else."""
    p = i.get("proposal") or {}
    return p.get("note") or "" if i["status"] == "settled" and p.get("outcome") == i["outcome"] else ""


def short(text, n=72):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def print_delivery(res, bid, url, full=False):
    """What `rails-board wait` hands to Claude.

    By default only what is new, each message under its ID. The finding, the triage and the earlier
    messages are not repeated: the session that made the board already has them in its own
    conversation under the same IDs, and `rails-board show KEY` prints them again if it has lost them.
    """
    keys = [item["issue"]["key"] for item in res["messages"]]
    for item in res["messages"]:
        i = item["issue"]
        if full:
            print(format_issue(i, item["new"]) + "\n")
            continue
        verdict = f"Claude {VERDICT_LABEL[i['verdict']]}" if i.get("verdict") else "not triaged yet"
        for at, m in enumerate(i["messages"]):
            if m["id"] not in item["new"]:
                continue
            mine = [x["n"] for x in i["messages"][:at] if x["author"] == "claude"]
            after = f", after your {i['key']}#{mine[-1]}" if mine else ""
            print(f"[{i['key']}#{m['n']}] User on {i['key']} \"{short(i['title'])}\" ({state(i)}, {verdict}{after}):")
            print(indent(m["text"]) + "\n")
    if keys:
        key = keys[0] if len(keys) == 1 else "KEY"
        print(f"Reply: rails-board reply {key} - --board {bid} < reply.md")
        if not full:
            print("Earlier context (finding, your triage, the thread) is in this conversation under the same IDs. "
                  f"If you cannot see it, run: rails-board show {key} --board {bid}")
        print()
    for h in res["handoffs"]:
        print("The user handed over these settled decisions. Carry out each Fix; the rest need no change.")
        missing = []
        for i in h["issues"]:
            print(f"  [{i['key']}] {OUTCOME_LABEL[i['outcome']]} — {short(i['title'])}" + (f"  ({where(i)})" if where(i) else ""))
            if i.get("note"):
                print("      The user's own instruction (it takes priority over yours where they differ):\n" + indent(i["note"], "        "))
            if i["outcome"] != "fix":
                continue
            if instruction(i):
                print("      Your instruction:\n" + indent(instruction(i), "        "))
            elif not i.get("note"):
                missing.append(i["key"])
                print("      No instruction was recorded for this outcome: follow the reviewer's recommendation.")
        print("  The instructions are your own, written as each discussion ended. Work from them as written; "
              "there is no need to read the threads again.")
        if missing:
            print(f"  For {', '.join(missing)}, if you no longer have the reviewer's recommendation in context run "
                  f"`rails-board show KEY --board {bid}`, and ask on the thread if it does not say clearly what to change.")
        print()
    if full:
        print(f"Board: {url}/#{bid}")


def not_verbatim(findings, raw):
    """Which findings (by position) have text that is not word for word in the reviewer's raw output."""
    def squash(t):
        return re.sub(r"\s+", " ", t).strip()
    hay = squash(raw)
    out = []
    for n, f in enumerate(findings, 1):
        # Each field must appear whole and in order; only whitespace may differ.
        chunks = [squash(str(f.get(k) or "")) for k in ("body", "recommendation")]
        if any(c and c not in hay for c in chunks):
            out.append(f"{n}")
    return out


CODEX_JOBS = "~/.claude/plugins/data/codex*/state/*/jobs/*.json"


def codex_jobs():
    """Finished Codex plugin jobs for this folder's workspace, newest first."""
    cwd = Path.cwd().resolve()
    jobs = []
    for f in glob.glob(os.path.expanduser(CODEX_JOBS)):
        try:
            d = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            continue
        root = d.get("workspaceRoot")
        raw = (d.get("result") or {}).get("rawOutput") or d.get("rendered")
        if not root or not raw or d.get("status") != "completed":
            continue
        root = Path(root).resolve()
        if root == cwd or root in cwd.parents:
            jobs.append({"id": d.get("id"), "at": d.get("completedAt") or d.get("createdAt") or "", "raw": raw,
                         "summary": " ".join(str(d.get("summary") or "").split())[:90]})
    return sorted(jobs, key=lambda j: j["at"], reverse=True)


# -- commands --
def cmd_codex(args):
    jobs = codex_jobs()
    if args.job:
        jobs = [j for j in jobs if j["id"] == args.job]
    if not jobs:
        raise Fail("no finished Codex job found for this folder; use the Codex response from the conversation instead")
    if args.list:
        for j in jobs[:15]:
            print(f"{j['id']}  {j['at'][:16].replace('T', ' ')}  {len(j['raw']):6} chars  {j['summary']}")
        return
    sys.stdout.write(jobs[0]["raw"].rstrip("\n") + "\n")


def cmd_serve(args):
    if st := running():
        print(f"rails-board: already serving at {st['url']}")
        if args.open:
            import webbrowser
            webbrowser.open(st["url"])
        return
    server.serve(args.port, args.open, idle_timeout=args.idle_timeout * 60)


def cmd_status(args):
    st = running()
    if not st:
        return print("rails-board: no server running (any command that needs one starts it)")
    stale = "" if st.get("stamp") == server.code_stamp() else "  ·  out of date, the next command restarts it"
    print(f"rails-board: serving {st['boards']} board(s) at {st['url']} (pid {st['pid']}){stale}")
    print("Claude is listening on: " + (", ".join(st["listening"]) or "no board"))


def cmd_stop(args):
    st = running()
    if not st:
        return print("rails-board: no server running")
    call(f"{st['url']}/api/stop", {})
    print("rails-board: server stopped")


def cmd_new(args):
    data = read_json(args.file)
    if isinstance(data, list):
        data = {"findings": data}
    if args.title:
        data["title"] = args.title
    for flag in ("source", "target"):
        if getattr(args, flag):
            data[flag] = getattr(args, flag)
    if not isinstance(data.get("findings"), list):
        raise Fail("the JSON needs a `findings` list")
    if args.raw and (loose := not_verbatim(data["findings"], Path(args.raw).read_text())):
        raise Fail(f"the text of finding {', '.join(loose)} is not word for word in {args.raw}. Findings must quote "
                   "the reviewer exactly; fix the JSON and run this again. No board was made.")
    data.setdefault("cwd", str(Path.cwd().resolve()))
    url = ensure_server()
    b = call(f"{url}/api/boards", data)
    print(f"Board {b['id']} — {len(b['issues'])} issue(s). From here on these IDs name the findings:")
    for i in b["issues"]:
        print(f"  {i['key']:4} {i['severity']:8} {short(i['title'], 90)}")
    print(f"{url}/#{b['id']}")
    if args.open:
        import webbrowser
        webbrowser.open(f"{url}/#{b['id']}")


def cmd_list(args):
    data = read_json(args.file)
    if isinstance(data, list):
        data = {"items": data}
    if not isinstance(data, dict):
        raise Fail("the JSON needs to be a list of items, or an object with an `items` list")
    if args.title:
        data["title"] = args.title
    data["kind"] = "list"
    data.setdefault("cwd", str(Path.cwd().resolve()))
    url = ensure_server()
    b = call(f"{url}/api/boards", data)
    print(f"List {b['id']} — {len(b['issues'])} item(s):")
    for i in b["issues"]:
        print(f"  {i['key']:4} {short(i['title'], 90)}")
    print(f"{url}/#{b['id']}")
    if args.open:
        import webbrowser
        webbrowser.open(f"{url}/#{b['id']}")


def cmd_add(args):
    data = read_json(args.file)
    url = ensure_server()
    bid = find_board(url, args)
    more = data if isinstance(data, list) else data.get("items" if args.kind == "list" else "findings")
    res = call(f"{url}/api/boards/{bid}/issues", {"findings": more})
    print(f"Added {', '.join(res['added'])} to {bid}")


def cmd_open(args):
    import webbrowser
    url = ensure_server()
    try:
        url = f"{url}/#{find_board(url, args)}"
    except Fail:
        pass
    webbrowser.open(url)
    print(url)


def cmd_boards(args):
    url = ensure_server()
    boards = call(f"{url}/api/boards")["boards"]
    if not boards:
        return print("No boards.")
    for b in boards:
        c = b["counts"]
        done, doing = ("done", "in progress") if b.get("kind") == "list" else ("settled", "discussing")
        print(f"{b['id']:34} {c['settled']}/{b['total']} {done}  {c['discussing']} {doing}  "
              f"{time.strftime('%d %b %H:%M', time.localtime(b['created']))}  {b['project'] or '-':16} {b['title']}")


def cmd_show(args):
    url = ensure_server()
    bid = find_board(url, args)
    b = call(f"{url}/api/boards/{bid}")
    if args.key:
        hit = [i for i in b["issues"] if i["key"] == args.key.upper()]
        if not hit:
            raise Fail(f"no issue {args.key} on {bid}")
        return print(format_item(hit[0]) if args.kind == "list" else format_issue(hit[0]))
    print(f"{b['title']}  ({bid})  {url}/#{bid}")
    for s in STATUSES:
        col = sorted((i for i in b["issues"] if i["status"] == s), key=lambda i: i["order"])
        print(f"\n{status_label(args.kind, s)} ({len(col)})")
        for i in col:
            if args.kind == "list":
                print(f"  {i['key']:4} {i['title']}")
                continue
            verdict = f"Claude {VERDICT_LABEL[i['verdict']]}" if i.get("verdict") else "not triaged"
            outcome = f"  → {OUTCOME_LABEL[i['outcome']]}" if i["outcome"] else ""
            print(f"  {i['key']:4} {i['severity']:8} {verdict:22} {i['title']}{outcome}")


def cmd_awaiting(args):
    url = ensure_server()
    bid = find_board(url, args)
    review_only(args, bid)
    issues = call(f"{url}/api/boards/{bid}/awaiting")["issues"]
    if not issues:
        return print("No issue is waiting for a Claude reply.")
    for i in issues:
        print(format_issue(i) + "\n")


def cmd_wait(args):
    deadline = time.time() + args.timeout if args.timeout else None
    warned = False
    url, bid = None, None
    while True:
        left = 25 if deadline is None else min(25, deadline - time.time())
        if left <= 0:
            print("Nothing arrived before the timeout.")
            sys.exit(3)
        try:
            url = ensure_server()
            bid = bid or find_board(url, args)
            review_only(args, bid)
            res = call(f"{url}/api/wait?board={urllib.parse.quote(bid)}&timeout={left:.0f}&ack=1", timeout=left + 15)
            warned = False
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            if not warned:
                print(f"rails-board: server not reachable ({e}); retrying…", file=sys.stderr)
                warned = True
            time.sleep(2)
            continue
        if res["messages"] or res["handoffs"]:
            print_delivery(res, bid, url, args.full)
            sys.stdout.flush()
            # Only now, with the delivery printed, tell the server it arrived. If this command had died
            # before this point the server would hand the same messages to the next `wait`.
            try:
                call(f"{url}/api/boards/{bid}/ack", {"delivery": res.get("delivery")}, timeout=5)
            except (urllib.error.URLError, ConnectionError, TimeoutError, Fail):
                pass
            return


def note_arg(args):
    if args.note_file:
        if not args.propose:
            raise Fail("--note-file goes with --propose")
        return Path(args.note_file).read_text()
    if args.note and not args.propose:
        raise Fail("--note goes with --propose")
    return args.note


def cmd_reply(args):
    url = ensure_server()
    bid = find_board(url, args)
    review_only(args, bid)
    i = call(f"{url}/api/boards/{bid}/issues/{args.key}/messages",
             {"author": "claude", "text": read_text(args.text), "verdict": args.verdict,
              "propose": args.propose, "note": note_arg(args)})
    print(f"Replied on {i['key']} as {i['key']}#{i['messages'][-1]['n']}"
          + (f"; the card's suggestion is now {OUTCOME_LABEL[args.propose]}"
             + (" with your instruction" if note_arg(args) else ", with no instruction") if args.propose else ""))


def cmd_triage(args):
    url = ensure_server()
    bid = find_board(url, args)
    review_only(args, bid)
    note = note_arg(args)  # refuses a note with no --propose: it would have nowhere to go
    patch = {"verdict": args.verdict, "triage": read_text(args.text)}
    if args.propose:
        patch["proposal"] = {"outcome": args.propose, "note": note or ""}
    call(f"{url}/api/boards/{bid}/issues/{args.key}", patch)
    print(f"Triaged {args.key.upper()}: Claude {VERDICT_LABEL[args.verdict]}")


def cmd_settle(args):
    url = ensure_server()
    bid = find_board(url, args)
    review_only(args, bid)
    patch = {"status": "settled", "outcome": args.outcome}
    if args.note is not None:
        patch["note"] = args.note
    i = call(f"{url}/api/boards/{bid}/issues/{args.key}", patch)
    print(f"{i['key']} settled as {OUTCOME_LABEL[i['outcome']]}")


def cmd_move(args):
    url = ensure_server()
    bid = find_board(url, args)
    if args.status == "done" and args.kind != "list":
        raise Fail(f"on a review, settle a card with `rails-board settle {args.key.upper()} --outcome …`")
    status = {"doing": "discussing", "done": "settled"}.get(args.status, args.status)
    i = call(f"{url}/api/boards/{bid}/issues/{args.key}", {"status": status})
    print(f"{i['key']} moved to {status_label(args.kind, i['status'])}")


def cmd_decisions(args):
    url = ensure_server()
    bid = find_board(url, args)
    review_only(args, bid)
    b = call(f"{url}/api/boards/{bid}")
    done = [i for i in b["issues"] if i["status"] == "settled"]
    print(f"# Decisions — {b['title']}\n\n{len(done)} of {len(b['issues'])} settled.")
    for o in OUTCOMES:
        rows = [i for i in done if i["outcome"] == o]
        if rows:
            print(f"\n## {OUTCOME_LABEL[o]}")
            for i in rows:
                print(f"- **{i['key']}** {i['title']}" + (f" (`{where(i)}`)" if where(i) else ""))
                for label, text in (("User's instruction", i.get("note")), ("Claude's instruction", instruction(i))):
                    if text:
                        print(f"  - {label}: " + text.strip().replace("\n", "\n    "))
    rest = [i for i in b["issues"] if i["status"] != "settled"]
    if rest:
        print("\n## Not settled yet")
        for i in rest:
            print(f"- **{i['key']}** {i['title']} ({STATUS_LABEL[i['status']].lower()})")


def cmd_remove(args):
    url = ensure_server()
    bid = find_board(url, args)
    call(f"{url}/api/boards/{bid}/remove", {})
    print(f"{bid} removed")


def main():
    ap = argparse.ArgumentParser(prog="rails-board", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, board=True):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        if board:
            p.add_argument("--board", help="board id (default: $RAILS_BOARD, else the newest board made from this folder)")
        return p

    p = add("new", cmd_new, "create a board from a findings JSON file (or stdin)", board=False)
    p.add_argument("file", nargs="?", default="-", help="JSON: {title, source, target, findings: [...]}")
    p.add_argument("--title")
    p.add_argument("--source", help="who reviewed, e.g. codex")
    p.add_argument("--target", help="what was reviewed, e.g. a spec path or branch")
    p.add_argument("--raw", metavar="FILE", help="the reviewer's full output; refuses findings that do not quote it word for word")
    p.add_argument("--open", action="store_true", help="open the board in the browser")

    p = add("list", cmd_list, "create a plain to-do list board from a JSON list of items (nothing on it goes to Claude)", board=False)
    p.add_argument("file", nargs="?", default="-", help='JSON: ["a line", {"title": "…", "body": "details"}], or {title, items: [...]}')
    p.add_argument("--title")
    p.add_argument("--open", action="store_true", help="open the list in the browser")

    p = add("codex", cmd_codex, "print the newest Codex plugin response for this folder, exactly as Codex wrote it", board=False)
    p.add_argument("--list", action="store_true", help="list recent Codex jobs instead")
    p.add_argument("--job", help="a job id from --list")

    p = add("add", cmd_add, "add more findings to a board")
    p.add_argument("file", nargs="?", default="-")

    p = add("wait", cmd_wait, "block until the user writes on the board or hands over decisions")
    p.add_argument("--timeout", type=float, default=0, help="seconds; 0 waits forever")
    p.add_argument("--full", action="store_true",
                   help="print each issue's finding, triage and whole thread, not only the new message")

    p = add("reply", cmd_reply, "reply in an issue's thread as Claude")
    p.add_argument("key")
    p.add_argument("text", help='reply text (markdown), or "-" to read stdin')
    p.add_argument("--verdict", choices=VERDICTS, help="change Claude's verdict on the finding")
    p.add_argument("--propose", choices=OUTCOMES, help="the outcome you now suggest; it replaces your earlier suggestion on the card")
    p.add_argument("--note", help="with --propose fix: the instruction, exactly what will be changed (as long as it needs to be)")
    p.add_argument("--note-file", metavar="FILE", help="the same, read from a file")

    p = add("triage", cmd_triage, "record Claude's verdict and reasoning on a finding")
    p.add_argument("key")
    p.add_argument("text", help='reasoning (markdown), or "-" to read stdin')
    p.add_argument("--verdict", choices=VERDICTS, required=True)
    p.add_argument("--propose", choices=OUTCOMES, help="the outcome you suggest")
    p.add_argument("--note", help="with --propose fix: the instruction, exactly what will be changed (as long as it needs to be)")
    p.add_argument("--note-file", metavar="FILE", help="the same, read from a file")

    p = add("settle", cmd_settle, "settle an issue (only when the user says so in chat)")
    p.add_argument("key")
    p.add_argument("--outcome", choices=OUTCOMES, required=True)
    p.add_argument("--note")

    p = add("move", cmd_move, "move a card to a column (on a list: todo, doing or done)")
    p.add_argument("key")
    p.add_argument("status", choices=[*STATUSES[:2], "doing", "done"])

    p = add("show", cmd_show, "print a board, or one issue in full")
    p.add_argument("key", nargs="?")

    add("awaiting", cmd_awaiting, "issues still owed a Claude reply, in full")
    add("decisions", cmd_decisions, "the settled outcomes as markdown")
    add("open", cmd_open, "open the board in the browser")

    add("boards", cmd_boards, "list boards (each is deleted a week after it was last touched)", board=False)
    add("remove", cmd_remove, "delete a board now")

    p = add("serve", cmd_serve, "run the server in the foreground (commands start one by themselves)", board=False)
    p.add_argument("--port", type=int)
    p.add_argument("--open", action="store_true")
    p.add_argument("--idle-timeout", type=float, default=120, metavar="MINUTES",
                   help="stop after this long with no viewer, listener or request (0 = never)")

    add("status", cmd_status, "is a server running, and is Claude listening?", board=False)
    add("stop", cmd_stop, "stop the server", board=False)

    args = ap.parse_args()
    try:
        args.fn(args)
    except Fail as e:
        sys.exit(f"rails-board: {e}")
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        sys.exit(f"rails-board: server not reachable ({e})")


if __name__ == "__main__":
    main()
