"""A throwaway machine for the tests to act on.

The tests go through the two seams real callers use, and nothing else:

- as **Claude**: the real `rails-board` command (`board.claude(...)`), which is all a Claude Code
  session ever touches;
- as **the user at the page**: the HTTP calls the page makes when the user writes on a card, takes
  a message back, drags a card or hands decisions over (`board.user_...`).

Nothing inside the package is imported or mocked. Each machine has its own home folder, data
folder and port, so the tests never see or touch the real boards in ~/.rails-board.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "bin" / "rails-board"

# A small review, as a reviewer might return it. F1 has everything a finding can carry.
REVIEW = {
    "title": "Cache spec review",
    "source": "codex",
    "target": "docs/cache.md",
    "findings": [
        {"title": "Cache key ignores the passage", "severity": "critical",
         "body": "Section 4.2 caches a verdict under the claim text alone, so an edited passage serves a stale verdict.",
         "recommendation": "Key the cache on the claim and the passage together.",
         "file": "docs/cache.md", "line_start": 88, "line_end": 104},
        {"title": "Retries can double the spend", "severity": "high",
         "body": "A call that timed out is retried without checking whether the first attempt completed.",
         "recommendation": "Make the call idempotent before retrying it."},
        {"title": "Metric is misnamed", "severity": "low",
         "body": "Section 9 calls the metric accuracy but the formula it gives is precision."},
    ],
}


# A plain to-do list: no reviewer, no verdicts, nothing for Claude to answer.
TASKS = [
    {"title": "Read the dictionary's shape", "body": "`redaction/dictionary.py`: the dataclass and its two lookups."},
    {"title": "Follow where it is built", "body": "`pipeline/build.py`, from `load_terms` to the cache write."},
    {"title": "Check the feature flag at startup"},
]


class Ran(NamedTuple):
    out: str
    err: str
    code: int


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Machine:
    """One computer as the tool sees it: a home folder, a data folder and a port."""

    def __init__(self, root: Path, launcher: Path = LAUNCHER):
        self.launcher = launcher
        self.home = root / "home"
        self.data = root / "rails-board-data"
        self.home.mkdir(parents=True)
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.env = {**os.environ, "HOME": str(self.home), "RAILS_BOARD_HOME": str(self.data),
                    "RAILS_BOARD_PORT": str(self.port),
                    # how long a delivery may go unconfirmed before it is queued again (15 s in real use)
                    "RAILS_BOARD_LEASE": "1"}
        self.env.pop("RAILS_BOARD", None)

    def run(self, *args, cwd, stdin=None, env=None) -> Ran:
        p = subprocess.run([str(self.launcher), *map(str, args)], cwd=cwd, env={**self.env, **(env or {})},
                           input=stdin, capture_output=True, text=True, timeout=60)
        return Ran(p.stdout, p.stderr, p.returncode)

    def claude(self, *args, cwd, stdin=None, env=None) -> str:
        """Run a command that should work; its output."""
        ran = self.run(*args, cwd=cwd, stdin=stdin, env=env)
        assert ran.code == 0, f"rails-board {' '.join(map(str, args))} failed ({ran.code}): {ran.err or ran.out}"
        return ran.out

    def claude_is_refused(self, *args, cwd, stdin=None, env=None) -> str:
        """Run a command that should be refused; what it said on stderr."""
        ran = self.run(*args, cwd=cwd, stdin=stdin, env=env)
        assert ran.code != 0, f"rails-board {' '.join(map(str, args))} was expected to be refused, but printed: {ran.out}"
        return ran.err

    def start(self, *args, cwd) -> subprocess.Popen:
        """Leave a command running, as Claude does with `wait` in the background."""
        return subprocess.Popen([str(self.launcher), *map(str, args)], cwd=cwd, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def request(self, method, path, body=None, headers=None):
        """One HTTP request, as a browser would send it. Returns (status, parsed JSON or text)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            data = None if body is None else json.dumps(body).encode()
            head = {"Content-Type": "application/json"} if data is not None else {}
            conn.request(method, path, body=data, headers={**head, **(headers or {})})
            res = conn.getresponse()
            raw = res.read().decode(errors="replace")
            try:
                return res.status, json.loads(raw)
            except ValueError:
                return res.status, raw
        finally:
            conn.close()

    def shut_down(self):
        self.run("stop", cwd=self.home)


class Board:
    """One review board, with Claude's side and the user's side of it."""

    def __init__(self, machine: Machine, project: Path, board_id: str, made: str):
        self.machine, self.project, self.id, self.made = machine, project, board_id, made

    # -- Claude's side: the command --
    def claude(self, *args, stdin=None) -> str:
        return self.machine.claude(*args, "--board", self.id, cwd=self.project, stdin=stdin)

    def claude_is_refused(self, *args, stdin=None) -> str:
        return self.machine.claude_is_refused(*args, "--board", self.id, cwd=self.project, stdin=stdin)

    def claude_waits(self, *flags, timeout=10) -> str:
        """What `wait` hands to Claude."""
        return self.claude("wait", "--timeout", timeout, *flags)

    def nothing_reaches_claude(self, timeout=1) -> bool:
        ran = self.machine.run("wait", "--timeout", timeout, "--board", self.id, cwd=self.project)
        return ran.code == 3 and "Nothing arrived" in ran.out

    # -- the user's side: what the page does --
    def page(self, method, path="", body=None):
        return self.machine.request(method, f"/api/boards/{self.id}{path}", body)

    def _post(self, path, body):
        status, res = self.page("POST", path, body)
        assert status == 200, f"the page's request to {path} was refused ({status}): {res}"
        return res

    def user_writes(self, key, text):
        return self._post(f"/issues/{key}/messages", {"author": "user", "text": text})

    def user_takes_back(self, key):
        return self._post(f"/issues/{key}/withdraw", {})

    def user_settles(self, key, outcome):
        return self._post(f"/issues/{key}", {"status": "settled", "outcome": outcome})

    def user_drags(self, key, column, before=None):
        return self._post(f"/issues/{key}", {"status": column, "before": before})

    def user_writes_instruction(self, key, text):
        return self._post(f"/issues/{key}", {"note": text})

    def user_hands_over(self):
        return self._post("/handoff", {})

    def card(self, key) -> dict:
        """The card as the page shows it."""
        status, board = self.page("GET")
        assert status == 200, board
        return next(i for i in board["issues"] if i["key"] == key)

    def thread(self, key) -> list:
        """The card's thread as (id, who, text)."""
        return [(f"{key}#{m['n']}", m["author"], m["text"]) for m in self.card(key)["messages"]]

    def column(self, column) -> list:
        """The keys in one column, top to bottom."""
        status, board = self.page("GET")
        assert status == 200, board
        cards = sorted((i for i in board["issues"] if i["status"] == column), key=lambda i: i["order"])
        return [i["key"] for i in cards]


@pytest.fixture(scope="session")
def machine(tmp_path_factory):
    """The machine most tests share; each test works on its own project folder and board."""
    m = Machine(tmp_path_factory.mktemp("machine"))
    yield m
    m.shut_down()


@pytest.fixture
def fresh_machine(tmp_path):
    """A machine nothing has run on yet, for tests about the server itself."""
    m = Machine(tmp_path / "machine")
    yield m
    m.shut_down()


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "project"
    p.mkdir()
    return p


@pytest.fixture
def make_board(machine, project):
    """Make a board from a review the way Claude does: write the findings file, run `new`."""
    def make(review=REVIEW, *flags, on=None, folder=None) -> Board:
        on, folder = on or machine, folder or project
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "findings.json").write_text(json.dumps(review))
        made = on.claude("new", "findings.json", *flags, cwd=folder)
        return Board(on, folder, made.split()[1], made)
    return make


@pytest.fixture
def board(make_board) -> Board:
    return make_board()


@pytest.fixture
def make_list(machine, project):
    """Make a list the way Claude does: write the items file, run `list`."""
    def make(items=TASKS, *flags, on=None, folder=None) -> Board:
        on, folder = on or machine, folder or project
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "items.json").write_text(json.dumps(items))
        made = on.claude("list", "items.json", *flags, cwd=folder)
        return Board(on, folder, made.split()[1], made)
    return make


@pytest.fixture
def todo(make_list) -> Board:
    return make_list(TASKS, "--title", "Manual review order")
