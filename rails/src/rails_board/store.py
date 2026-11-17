"""Boards of review findings, one JSON file per board in <home>/boards/.

A board is one review (for example one Codex adversarial review), made for that review and not
kept for long: boards nobody has touched for a week are deleted. It holds issues; an issue holds
the reviewer's finding verbatim, Claude's triage of it, a column (todo / discussing / settled), and a
thread of messages between the user and Claude. User messages wait in a queue until `rails-board wait`
takes them.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

STATUSES = ("todo", "discussing", "settled")
OUTCOMES = ("fix", "defer", "wontfix", "invalid")
VERDICTS = ("valid", "partial", "invalid", "unsure")
SEVERITIES = ("critical", "high", "medium", "low")
KEEP_DAYS = 7  # a board is for one review; untouched ones are deleted after this long
# Seconds a delivery may go unconfirmed by the `wait` that took it before it is delivered again.
LEASE = float(os.environ.get("RAILS_BOARD_LEASE") or 15)

OUTCOME_LABEL = {"fix": "Fix", "defer": "Defer", "wontfix": "Won't fix", "invalid": "Not an issue"}
VERDICT_LABEL = {"valid": "agrees", "partial": "partly agrees", "invalid": "disagrees", "unsure": "unsure"}
STATUS_LABEL = {"todo": "To review", "discussing": "Discussing", "settled": "Settled"}

# A board is a review (findings, triage, a thread with Claude, outcomes) or a plain list: the same
# cards and columns for a to-do list, with nothing sent to Claude and a last column that just means done.
KINDS = ("review", "list")
LIST_STATUS_LABEL = {"todo": "To do", "discussing": "In progress", "settled": "Done"}


def status_label(kind, status):
    return (LIST_STATUS_LABEL if kind == "list" else STATUS_LABEL)[status]


def is_list(board):
    return board.get("kind") == "list"


def home() -> Path:
    return Path(os.environ.get("RAILS_BOARD_HOME") or Path.home() / ".rails-board")


def _id(prefix):
    return f"{prefix}_{secrets.token_hex(3)}"


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return "-".join(s.split("-")[:3])[:24].strip("-") or "review"


class Invalid(ValueError):
    """A request the store refuses; its message is shown to whoever made it."""


def _choice(value, allowed, what, default=None):
    if value in (None, ""):
        return default
    v = str(value).strip().lower().replace("'", "").replace(" ", "").replace("_", "").replace("-", "")
    aliases = {"wontdo": "wontfix", "notanissue": "invalid", "dismiss": "invalid", "agree": "valid",
               "agrees": "valid", "partly": "partial", "partlyagrees": "partial", "disagree": "invalid",
               "disagrees": "invalid", "review": "todo", "toreview": "todo", "info": "low", "nit": "low",
               "blocker": "critical", "major": "high", "minor": "low", "moderate": "medium"}
    v = aliases.get(v, v)
    if v not in allowed:
        raise Invalid(f"{what} must be one of {', '.join(allowed)} (got {value!r})")
    return v


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


class Store:
    def __init__(self, root: Path | None = None, on_change=None):
        self.dir = (root or home()) / "boards"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cond = threading.Condition()
        self.on_change = on_change or (lambda board_id: None)
        self.boards = {}
        for p in sorted(self.dir.glob("*.json")):
            try:
                b = json.loads(p.read_text())
                if time.time() - b.get("updated", b["created"]) > KEEP_DAYS * 86400:
                    p.unlink()
                    continue
                for i in b["issues"]:
                    # A note that only repeats Claude's suggestion was copied in by an older version; the
                    # instruction is now read from the suggestion itself, and `note` is the user's alone.
                    if i.get("note") and i["note"] == (i.get("proposal") or {}).get("note"):
                        i["note"] = ""
                    # boards from before messages carried their own number
                    for n, m in enumerate(i["messages"], 1):
                        m.setdefault("n", n)
                    i.setdefault("next_n", max((m["n"] for m in i["messages"]), default=0) + 1)
                b.setdefault("kind", "review")  # boards from before lists existed
                self.boards[b["id"]] = b
            except (OSError, ValueError, KeyError):
                continue  # a half-written or foreign file; leave it alone

    # -- internals (call with self.cond held) --
    def _save(self, b):
        b["updated"] = time.time()
        path = self.dir / f"{b['id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(b, indent=2))
        tmp.replace(path)
        self.cond.notify_all()
        self.on_change(b["id"])

    def _board(self, bid):
        if bid not in self.boards:
            raise KeyError(f"no board {bid!r}")
        return self.boards[bid]

    def _issue(self, b, key):
        k = str(key).upper()
        for i in b["issues"]:
            if i["key"] == k:
                return i
        raise KeyError(f"no issue {key!r} on board {b['id']}")

    def _review_only(self, b, what):
        if is_list(b):
            raise Invalid(f"{b['id']} is a list, not a review: {what}")

    def _new_issue(self, b, f):
        listed = is_list(b)
        if listed and isinstance(f, str):
            f = {"title": f}
        if not isinstance(f, dict) or not str(f.get("title") or "").strip():
            raise Invalid("every item needs a title" if listed else "every finding needs a title")
        n = b["next_key"]
        b["next_key"] = n + 1
        issue = {
            "key": str(n) if listed else f"F{n}",  # a list keeps the list's own numbers
            "title": str(f["title"]).strip(),
            "severity": None if listed else _choice(f.get("severity"), SEVERITIES, "severity", "medium"),
            "body": str(f.get("body") or "").strip(),
            "recommendation": str(f.get("recommendation") or "").strip(),
            "file": str(f.get("file") or "").strip(),
            "line_start": _int(f.get("line_start")),
            "line_end": _int(f.get("line_end")),
            "confidence": f.get("confidence") if isinstance(f.get("confidence"), (int, float)) else None,
            "verdict": _choice(f.get("verdict"), VERDICTS, "verdict"),
            "triage": str(f.get("triage") or "").strip(),
            "proposal": None,
            "status": "todo",
            "outcome": None,
            "note": "",
            "settled_at": None,
            "order": float(n),
            "messages": [],
            "next_n": 1,  # thread messages are F3#1, F3#2, …; a number is never reused
        }
        propose = _choice(f.get("propose"), OUTCOMES, "propose")
        if propose:
            issue["proposal"] = {"outcome": propose, "note": str(f.get("note") or "").strip(), "ts": time.time()}
        b["issues"].append(issue)
        return issue

    @staticmethod
    def _copy(obj):
        return json.loads(json.dumps(obj))

    @staticmethod
    def summary(b):
        counts = {s: 0 for s in STATUSES}
        for i in b["issues"]:
            counts[i["status"]] += 1
        return {"id": b["id"], "kind": b.get("kind", "review"), "title": b["title"], "source": b.get("source"), "target": b.get("target"),
                "project": b.get("project"), "cwd": b.get("cwd"), "created": b["created"],
                "updated": b.get("updated"), "counts": counts, "total": len(b["issues"])}

    # -- boards --
    def list(self):
        with self.cond:
            return sorted((self.summary(b) for b in self.boards.values()), key=lambda s: -s["created"])

    def get(self, bid):
        with self.cond:
            return self._copy(self._board(bid))

    def create(self, data):
        if not isinstance(data, dict):
            raise Invalid("expected a JSON object with a `findings` list")
        kind = data.get("kind") or "review"
        if kind not in KINDS:
            raise Invalid(f"kind must be one of {', '.join(KINDS)} (got {kind!r})")
        findings = data.get("items") if kind == "list" else data.get("findings")
        if kind == "list" and (not isinstance(findings, list) or not findings):
            raise Invalid("a list needs at least one item")
        if not isinstance(findings, list):
            raise Invalid("`findings` must be a list (it may be empty)")
        title = str(data.get("title") or "").strip() or ("List" if kind == "list" else "Review")
        cwd = str(data.get("cwd") or "")
        with self.cond:
            while True:  # never reuse an id: that would overwrite another review's board
                bid = f"{_slug(title)}-{secrets.token_hex(2)}"
                if bid not in self.boards and not (self.dir / f"{bid}.json").exists():
                    break
            b = {"id": bid, "kind": kind, "title": title, "source": str(data.get("source") or ("" if kind == "list" else "codex")),
                 "target": str(data.get("target") or ""), "cwd": cwd,
                 "project": str(data.get("project") or (Path(cwd).name if cwd else "")),
                 "created": time.time(), "next_key": 1, "issues": [], "handoffs": []}
            for f in findings:
                self._new_issue(b, f)
            self.boards[bid] = b
            self._save(b)
            return self._copy(b)

    def add_issues(self, bid, findings):
        if not isinstance(findings, list) or not findings:
            raise Invalid("`findings` must be a non-empty list")
        with self.cond:
            b = self._board(bid)
            # Build them all off to the side first, so a refusal part-way adds none of them.
            draft = {"id": b["id"], "kind": b.get("kind"), "next_key": b["next_key"], "issues": []}
            for f in findings:
                self._new_issue(draft, f)
            b["issues"] += draft["issues"]
            b["next_key"] = draft["next_key"]
            self._save(b)
            return [i["key"] for i in draft["issues"]]

    def remove(self, bid):
        with self.cond:
            self._board(bid)
            del self.boards[bid]
            (self.dir / f"{bid}.json").unlink(missing_ok=True)
            self.cond.notify_all()
            self.on_change(bid)

    # -- issues --
    def update_issue(self, bid, key, patch):
        """Move an issue between columns, settle it, reorder it, or change Claude's triage of it."""
        with self.cond:
            b = self._board(bid)
            i = self._issue(b, key)
            status = _choice(patch.get("status"), STATUSES, "status")
            if is_list(b):
                return self._move_item(b, i, status, patch)
            # Everything that can be refused is checked here, before the card is touched, so a refused
            # request leaves it exactly as it was.
            outcome = _choice(patch.get("outcome"), OUTCOMES, "outcome")
            verdict = _choice(patch.get("verdict"), VERDICTS, "verdict")
            p = patch.get("proposal")
            if p is not None and not isinstance(p, dict):
                raise Invalid("proposal must be an object with an outcome")
            proposed = _choice((p or {}).get("outcome"), OUTCOMES, "propose")
            if outcome and not status:
                status = "settled"
            moved = status is not None or "before" in patch
            if status == "settled":
                outcome = outcome or i["outcome"] or (i["proposal"] or {}).get("outcome")
                if not outcome:
                    raise Invalid("settling needs an outcome: " + ", ".join(OUTCOMES))
                if i["status"] != "settled" or i["outcome"] != outcome:
                    i["settled_at"] = time.time()
                    i["handed"] = None  # a changed decision goes to Claude again
                i["status"], i["outcome"] = "settled", outcome
            elif status:
                i["status"], i["outcome"], i["settled_at"] = status, None, None
                i["handed"] = None  # no longer a decision: a hand-over still queued must not deliver it
            if "note" in patch:
                note = str(patch.get("note") or "").strip()
                if note != i["note"] and i["status"] == "settled":
                    i["handed"] = None
                i["note"] = note
            if "verdict" in patch:
                i["verdict"] = verdict
            if "triage" in patch:
                i["triage"] = str(patch.get("triage") or "").strip()
            if "proposal" in patch:
                i["proposal"] = {"outcome": proposed, "note": str(p.get("note") or "").strip(), "ts": time.time()} if proposed else None
            if moved:
                self._place(b, i, patch.get("before"))
            self._save(b)
            return self._copy(i)

    def _move_item(self, b, i, status, patch):
        """A list card only moves between columns: done is done, with no outcome, verdict or instruction."""
        if any(k in patch for k in ("outcome", "verdict", "triage", "proposal", "note")):
            self._review_only(b, "its cards have no outcome, verdict or instruction")
        if status and status != i["status"]:
            i["status"] = status
            i["settled_at"] = time.time() if status == "settled" else None
        if status is not None or "before" in patch:
            self._place(b, i, patch.get("before"))
        self._save(b)
        return self._copy(i)

    def _place(self, b, issue, before):
        """Put `issue` just above the issue `before` in its column, or at the end of the column."""
        col = sorted((x for x in b["issues"] if x["status"] == issue["status"] and x is not issue),
                     key=lambda x: x["order"])
        idx = next((n for n, x in enumerate(col) if x["key"] == str(before or "").upper()), len(col))
        lo = col[idx - 1]["order"] if idx > 0 else None
        hi = col[idx]["order"] if idx < len(col) else None
        if lo is None and hi is None:
            issue["order"] = 1.0
        elif lo is None:
            issue["order"] = hi - 1
        elif hi is None:
            issue["order"] = lo + 1
        else:
            issue["order"] = (lo + hi) / 2

    def add_message(self, bid, key, author, text, propose=None, note="", verdict=None):
        text = str(text or "").strip()
        if not text:
            raise Invalid("a message needs text")
        if author not in ("user", "claude"):
            raise Invalid("author must be user or claude")
        with self.cond:
            b = self._board(bid)
            i = self._issue(b, key)
            listed = is_list(b)
            if listed and author == "claude":
                self._review_only(b, "its cards hold the user's own notes, and Claude does not reply on them")
            if author == "claude" and i.get("withdrawn"):
                n = i.pop("withdrawn")
                self._save(b)
                raise Invalid(f"the user withdrew {i['key']}#{n} before you replied, so this reply was not posted. "
                              "Drop it, do not post it again, and run `rails-board wait` again: "
                              "a rewritten message may be waiting.")
            m = {"id": _id("m"), "n": i["next_n"], "author": author, "text": text, "ts": time.time(),
                 "to_claude": author == "user" and not listed, "delivered": None}
            i["next_n"] += 1
            if listed:
                pass  # a note on a list card: it stays where it is and goes to nobody
            elif author == "user":
                if i["status"] != "discussing":  # asking about a card opens (or reopens) the discussion
                    m["was"] = {k: i[k] for k in ("status", "outcome", "settled_at", "order")}
                    i["status"], i["outcome"], i["settled_at"] = "discussing", None, None
                    self._place(b, i, None)
            else:
                v = _choice(verdict, VERDICTS, "verdict")
                if v:
                    i["verdict"] = v
                    m["verdict"] = v
                o = _choice(propose, OUTCOMES, "propose")
                if o:
                    i["proposal"] = {"outcome": o, "note": str(note or "").strip(), "ts": m["ts"]}
                    m["propose"] = {"outcome": o, "note": str(note or "").strip()}
            i["messages"].append(m)
            self._save(b)
            return self._copy(i)

    def withdraw(self, bid, key):
        """Take back the user's last message, as Esc does in Claude Code: it leaves the thread and its
        text goes back to the user to edit. If Claude already has it, the reply it is writing is refused."""
        with self.cond:
            b = self._board(bid)
            i = self._issue(b, key)
            if not i["messages"] or i["messages"][-1]["author"] != "user":
                raise Invalid("there is no unanswered message of yours to cancel")
            m = i["messages"].pop()
            if m["delivered"]:
                i["withdrawn"] = m["n"]
            if m.get("was") and i["status"] == "discussing":  # the send moved the card; put it back
                i.update(m["was"])
            self._save(b)
            return {"issue": self._copy(i), "text": m["text"], "delivered": bool(m["delivered"]), "n": m["n"]}

    def handoff(self, bid):
        """Queue the settled decisions Claude hasn't been given yet, for the next `rails-board wait`."""
        with self.cond:
            b = self._board(bid)
            self._review_only(b, "nothing on it is handed to Claude")
            keys = [i["key"] for i in b["issues"] if i["status"] == "settled" and not i.get("handed")]
            if not keys:
                raise Invalid("nothing new to hand over: settle an issue first")
            h = {"id": _id("h"), "ts": time.time(), "delivered": None, "keys": keys}
            for i in b["issues"]:
                if i["key"] in keys:
                    i["handed"] = h["id"]
            b.setdefault("handoffs", []).append(h)
            self._save(b)
            return self._copy(h)

    # -- the queue to Claude --
    @staticmethod
    def _handed(b, h):
        """The decisions a hand-over still stands for: cards settled then, and not reopened or re-settled
        since. A card changed after the button was pressed waits for the user to hand it over again."""
        return [i for i in b["issues"] if i["key"] in h["keys"] and i["status"] == "settled" and i.get("handed") == h["id"]]

    @staticmethod
    def _sent(b):
        """Every message and hand-over on the board: the things that are delivered to Claude."""
        return [m for i in b["issues"] for m in i["messages"]] + b.get("handoffs", [])

    def _requeue_lapsed(self, b):
        """Put back in the queue any delivery a listener took and never confirmed (it died holding it).
        Returns how many seconds until the next unconfirmed one lapses, or None if there is none."""
        now, soonest = time.time(), None
        for x in self._sent(b):
            if not x.get("unconfirmed"):
                continue
            left = x["delivered"] + LEASE - now
            if left <= 0:
                x["delivered"] = None
                del x["unconfirmed"]
            else:
                soonest = left if soonest is None else min(soonest, left)
        return soonest

    def _pending(self, b):
        msgs = [(i, [m for m in i["messages"] if m["to_claude"] and not m["delivered"]]) for i in b["issues"]]
        return ([(i, ms) for i, ms in msgs if ms],
                [h for h in b.get("handoffs", []) if not h["delivered"] and self._handed(b, h)])

    def take_pending(self, bid, timeout, settle=0.8, confirms=False):
        """Block until the user has sent something on this board, then mark it delivered and return it.

        A listener that says it `confirms` gets a delivery id to send back once it has the delivery in
        hand. Until it does, the delivery is only on loan: if no confirmation comes within LEASE
        seconds the listener is taken to have died, and the same messages are delivered again."""
        deadline = time.time() + timeout
        with self.cond:
            self._review_only(self._board(bid), "nothing on it is sent to Claude, so there is nothing to wait for")
            while True:
                b = self._board(bid)
                lapse = self._requeue_lapsed(b)
                msgs, hands = self._pending(b)
                if msgs or hands:
                    break
                left = deadline - time.time()
                if left <= 0:
                    return {"messages": [], "handoffs": []}
                self.cond.wait(left if lapse is None else min(left, lapse + 0.05))
            end = time.time() + settle  # a short settle so a burst arrives as one batch
            while (left := end - time.time()) > 0:
                self.cond.wait(left)
            b = self._board(bid)
            msgs, hands = self._pending(b)
            now = time.time()
            out = {"messages": [], "handoffs": []}
            delivery = _id("d") if confirms else None
            if delivery:
                out["delivery"] = delivery
            for i, ms in msgs:
                i.pop("withdrawn", None)  # Claude is back to waiting, so no reply to a withdrawn message is in flight
                for m in ms:
                    m["delivered"] = now
                out["messages"].append({"issue": self._copy(i), "new": [m["id"] for m in ms]})
            for h in hands:
                h["delivered"] = now
                out["handoffs"].append({"id": h["id"], "issues": [self._copy(i) for i in self._handed(b, h)]})
            if delivery:
                for x in [m for _, ms in msgs for m in ms] + hands:
                    x["unconfirmed"] = delivery
            self._save(b)
            return out

    def confirm(self, bid, delivery):
        """The listener has the delivery in hand, so it will not be delivered again."""
        with self.cond:
            b = self._board(bid)
            for x in self._sent(b):
                if x.get("unconfirmed") == delivery:
                    del x["unconfirmed"]
            self._save(b)
            return {"ok": True}

    def awaiting(self, bid):
        """Issues whose last user message has no Claude reply after it."""
        with self.cond:
            self._review_only(self._board(bid), "nothing on it waits for a Claude reply")
            b = self._board(bid)
            out, now, took = [], time.time(), False
            for i in b["issues"]:
                last = max((n for n, m in enumerate(i["messages"]) if m["author"] == "user"), default=-1)
                if last >= 0 and not any(m["author"] == "claude" for m in i["messages"][last + 1:]):
                    # Claude is being shown these in full, so they count as delivered: the next
                    # `wait` must not hand over the same questions again.
                    for m in i["messages"]:
                        if m["to_claude"] and (not m["delivered"] or m.get("unconfirmed")):
                            m["delivered"] = m["delivered"] or now
                            m.pop("unconfirmed", None)
                            took = True
                    out.append(self._copy(i))
            if took:
                self._save(b)
            return out
