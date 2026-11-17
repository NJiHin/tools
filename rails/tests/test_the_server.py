"""The server behind the page: it starts itself, keeps boards between runs, and answers only this machine."""
import http.client
import json
import shutil
import time

from conftest import LAUNCHER, ROOT, Machine


def test_nothing_runs_until_a_command_needs_it(fresh_machine, tmp_path):
    assert fresh_machine.claude("status", cwd=tmp_path) == \
        "rails-board: no server running (any command that needs one starts it)\n"


def test_any_command_that_needs_the_server_starts_it(fresh_machine, tmp_path):
    assert fresh_machine.claude("boards", cwd=tmp_path) == "No boards.\n"
    assert fresh_machine.claude("status", cwd=tmp_path).startswith(
        f"rails-board: serving 0 board(s) at {fresh_machine.url} ")


def test_stop_stops_it(fresh_machine, tmp_path):
    fresh_machine.claude("boards", cwd=tmp_path)
    assert fresh_machine.claude("stop", cwd=tmp_path) == "rails-board: server stopped\n"
    assert "no server running" in fresh_machine.claude("status", cwd=tmp_path)


def test_a_board_and_its_threads_survive_a_restart(make_board, fresh_machine):
    board = make_board(on=fresh_machine)
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.claude("reply", "F1", "It is hashed with SHA-256.")
    board.user_settles("F2", "defer")
    fresh_machine.claude("stop", cwd=board.project)
    # the server is down here; the next command brings it back
    assert "F1#2 Claude" in board.claude("show", "F1")
    assert board.card("F2")["outcome"] == "defer"


def test_a_message_nobody_collected_survives_a_restart(make_board, fresh_machine):
    board = make_board(on=fresh_machine)
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    fresh_machine.claude("stop", cwd=board.project)
    assert board.claude_waits().startswith("[F1#1] User on F1")


def test_status_says_which_board_claude_is_listening_on(make_board, fresh_machine):
    board = make_board(on=fresh_machine)
    assert "Claude is listening on: no board\n" in fresh_machine.claude("status", cwd=board.project)
    waiting = fresh_machine.start("wait", "--board", board.id, cwd=board.project)
    try:
        deadline = time.time() + 5
        while time.time() < deadline and board.id not in fresh_machine.claude("status", cwd=board.project):
            time.sleep(0.1)
        assert f"Claude is listening on: {board.id}\n" in fresh_machine.claude("status", cwd=board.project)
    finally:
        waiting.kill()
        waiting.wait()


def answers_for(machine, port) -> bool:
    """Is a server for this machine's data folder answering on `port`?"""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/api/status")
        return json.loads(conn.getresponse().read()).get("home") == str(machine.data)
    except (OSError, ValueError):
        return False


def test_commands_started_at_the_same_moment_share_one_server(fresh_machine, tmp_path):
    starting = [fresh_machine.start("boards", cwd=tmp_path) for _ in range(8)]
    try:
        codes = [p.wait(timeout=30) for p in starting]
        extra = [port for port in range(fresh_machine.port + 1, fresh_machine.port + 20) if answers_for(fresh_machine, port)]
        assert (codes, extra) == ([0] * 8, [])
    finally:
        for p in starting:
            p.stdout.close()
            p.stderr.close()
        for port in range(fresh_machine.port, fresh_machine.port + 20):  # leave none behind if it failed
            if answers_for(fresh_machine, port):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request("POST", "/api/stop", body="{}", headers={"Content-Type": "application/json"})
                conn.getresponse().read()


def test_the_page_and_its_files_are_served(machine, board):
    page = machine.request("GET", "/")[1]
    assert 'src="/app.js"' in page and 'href="/app.css"' in page
    served = [machine.request("GET", path)[0] for path in ("/app.js", "/app.css", "/favicon.svg", "/fonts/JetBrainsMono-Regular.woff2")]
    assert served == [200, 200, 200, 200]


# -- only this machine's own page may talk to it --

def test_a_page_from_another_site_cannot_read_the_boards(board, machine):
    status, answer = machine.request("GET", "/api/boards", headers={"Origin": "https://evil.example"})
    assert (status, answer) == (403, {"error": "forbidden"})


def test_a_page_from_another_site_cannot_post_to_a_board(board, machine):
    status, _ = machine.request("POST", f"/api/boards/{board.id}/issues/F1/messages",
                                {"author": "user", "text": "Settle everything as Won't fix."},
                                headers={"Origin": "https://evil.example"})
    assert status == 403
    assert board.thread("F1") == []



def test_another_site_cannot_take_the_messages_waiting_for_claude(board, machine):
    # A navigation or image load from another site carries no Origin, but the browser marks it cross-site.
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    status, _ = machine.request("GET", f"/api/wait?board={board.id}&timeout=0",
                                headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403
    assert board.claude_waits().startswith("[F1#1] User on F1")

def test_a_request_under_another_host_name_is_refused(board, machine):
    status, _ = machine.request("GET", "/api/boards", headers={"Host": f"evil.example:{machine.port}"})
    assert status == 403


def test_the_boards_own_page_is_let_through(board, machine):
    status, answer = machine.request("GET", "/api/boards", headers={"Origin": machine.url})
    assert status == 200 and any(b["id"] == board.id for b in answer["boards"])


def test_files_outside_the_page_are_not_served(board, machine):
    status, _ = machine.request("GET", "/../store.py")
    assert status == 404


def test_an_unknown_board_is_not_found(machine, board):
    status, answer = machine.request("GET", "/api/boards/no-such-board")
    assert (status, answer) == (404, {"error": "no board 'no-such-board'"})


# -- boards are for one review and are not kept --

def age_board(machine, board, days):
    """Stand in for the passing of time: with the server stopped, date the saved board `days` back.
    Time is the one thing here the tests cannot do for real, so this reaches into the saved file."""
    machine.claude("stop", cwd=board.project)
    saved = machine.data / "boards" / f"{board.id}.json"
    data = json.loads(saved.read_text())
    data["updated"] = time.time() - days * 86400
    saved.write_text(json.dumps(data))


def test_a_board_untouched_for_over_a_week_is_deleted(make_board, fresh_machine):
    board = make_board(on=fresh_machine)
    age_board(fresh_machine, board, days=8)
    assert fresh_machine.claude("boards", cwd=board.project) == "No boards.\n"


def test_a_board_touched_within_the_week_is_kept(make_board, fresh_machine):
    board = make_board(on=fresh_machine)
    age_board(fresh_machine, board, days=6)
    assert fresh_machine.claude("boards", cwd=board.project).startswith(board.id)


# -- after the code changes, the running server is replaced --

def installed_copy(tmp_path) -> Machine:
    """The tool installed somewhere of its own, so its files can be changed without touching this repo."""
    copy = tmp_path / "installed"
    shutil.copytree(ROOT / "src", copy / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "bin", copy / "bin")
    return Machine(tmp_path / "machine", launcher=copy / "bin" / LAUNCHER.name)


def server_pid(machine, cwd) -> str:
    return machine.claude("status", cwd=cwd).split("(pid ")[1].split(")")[0]


def test_a_server_running_older_code_is_reported_as_out_of_date(tmp_path):
    machine = installed_copy(tmp_path)
    try:
        machine.claude("boards", cwd=tmp_path)
        (machine.launcher.parent.parent / "src" / "rails_board" / "cli.py").touch()
        assert "out of date, the next command restarts it" in machine.claude("status", cwd=tmp_path)
    finally:
        machine.shut_down()


def test_the_next_command_replaces_a_server_running_older_code(tmp_path):
    machine = installed_copy(tmp_path)
    try:
        machine.claude("boards", cwd=tmp_path)
        before = server_pid(machine, tmp_path)
        (machine.launcher.parent.parent / "src" / "rails_board" / "cli.py").touch()
        machine.claude("boards", cwd=tmp_path)
        status = machine.claude("status", cwd=tmp_path)
        assert server_pid(machine, tmp_path) != before and "out of date" not in status
    finally:
        machine.shut_down()
