# rails

A local board for settling review findings with Claude Code. (Nothing to do with Ruby on Rails.)

A reviewer returns a list of findings: Codex, another model, a person's code review, an audit. Instead of reading them as one long chat
message, each finding becomes a card on a kanban board in the browser:

- **Three columns:** To review, Discussing, Settled. Drag cards between them. Dragging onto
  Settled shows four drop targets, one per outcome: Fix, Defer, Won't fix, Not an issue.
- **Each card opens as a popup over the board:** the reviewer's finding word for word, Claude's verdict on
  it, and a thread. Write in the thread and the message goes to the Claude Code session that is
  listening; its answer appears in the same thread.
- **Unread replies are marked on the card.** A card Claude has answered since you last had it
  open says "Claude replied" beside a blue dot. Cards also say "Claude is replying" while it
  works on them.
- **Each fix carries an instruction.** Whenever Claude suggests Fix, at triage or in a reply, it
  states exactly what it will change, and keeps that current as the discussion moves. Settle a
  card as Fix and the card shows it ("Claude will: …"). You can write your own instruction on
  the card; Claude gets both, and yours takes priority where they differ.
- **Hand decisions to Claude** sends the settled outcomes and those instructions back to the
  session, which carries them out. It does not re-read the threads.

A board is made for one review and is not kept: the page shows that review only, and a board
nobody has touched for a week is deleted.

Everything is local: one small Python server on `127.0.0.1`, boards saved as JSON in
`~/.rails-board/boards/`. There are no dependencies beyond Python 3.9 or later (macOS ships it; on Windows install it from python.org), and nothing to install.
It runs on macOS, Linux and Windows; on Windows, Claude Code runs it through Git Bash.

The page is set in JetBrains Mono, the face Ghostty draws with by default, so it reads like the
terminal beside it. The font files ship in `src/rails_board/static/fonts/` (SIL Open Font
License, see `OFL.txt` there) because it is not a system font. Everything else is under the MIT
licence (`LICENSE`).

## Install

It is a Claude Code plugin: the skill, the `rails-board` command and the page come together.

```sh
claude plugin marketplace add NJiHin/tools
claude plugin install rails@tools
```

To update later: `claude plugin marketplace update tools`, then `claude plugin update rails@tools`.

Working on the plugin itself, add the marketplace from a local clone instead
(`claude plugin marketplace add /path/to/tools`). Claude Code then runs the plugin from that folder,
so a change to the code or the skill applies to every session started afterwards. Before pushing a
change, raise `version` in `.claude-plugin/plugin.json` (and `pyproject.toml`), because installed
copies only update when it changes.

The command is `rails-board`, not `rails`, because macOS ships a `/usr/bin/rails` stub for Ruby
on Rails that many shells find first. Claude Code puts the plugin's `bin/` on the PATH of its own
shell only. To use the command in your own terminal, run `bin/rails-board` from this folder or
link it into a folder on your PATH.

## Use

In Claude Code, after a review has come back, type `/rails:rails`. Claude puts the findings on
a board, opens it, triages each finding, and listens for your messages. The skill is in
`skills/rails/SKILL.md`.

Under the hood:

```sh
rails-board codex > raw.md                      # Codex's last response for this folder, exactly as written
rails-board new findings.json --raw raw.md --open
rails-board triage F1 - --verdict valid --propose fix --note "what will be changed" < triage.md
rails-board wait --board ID                     # run in the background; exits when you write on the board
rails-board reply F3 - --board ID < reply.md    # answer in the thread
rails-board decisions --board ID                # the settled outcomes as markdown
```

`rails-board new` reads this JSON (only `title` is required; field names follow the Codex plugin's review schema):

```json
{
  "title": "Auth spec, adversarial review",
  "source": "codex",
  "target": "docs/specs/auth.md",
  "findings": [
    {"title": "…", "severity": "critical|high|medium|low", "body": "the finding, word for word",
     "recommendation": "…", "file": "src/x.py", "line_start": 10, "line_end": 20, "confidence": 0.8}
  ]
}
```

With `--raw FILE` (the reviewer's full output), `rails-board new` refuses to make the board if any
finding's text is not found word for word in that file.

Other commands: `add`, `show [KEY]`, `awaiting`, `settle`, `move`, `boards`, `open`, `remove`,
`status`, `stop`, `serve`. Run `rails-board <command> -h` for options.

## A plain list

`/rails:list` puts any list on the same board as cards: a to-do list, a checklist, the steps of a
plan. The columns are To do, In progress and Done, the cards carry the list's own numbers
(1, 2, 3…), and the box in a card's popup saves your own notes on it. Nothing on a list goes to
Claude: there are no verdicts, no outcomes and no hand-over, and `rails-board wait` is refused.

```sh
rails-board list items.json --title "Manual review order" --open
rails-board move 3 done --board ID        # also: doing, todo
rails-board show --board ID               # what is left
```

`items.json` is a list of lines of text, or of `{"title": "…", "body": "details"}`.

## Tests

```sh
uvx pytest          # about a minute; needs nothing installed
```

The tests go through the two ways the tool is really used and nothing else: they run the real
`rails-board` command, as Claude does, and make the HTTP calls the page makes, as the user does.
Nothing inside the package is imported or mocked, so they keep passing when the code is
rearranged and fail when what Claude or the user sees changes. Each run uses its own throwaway
home folder, data folder and port; your boards in `~/.rails-board` are never touched. The page's
own JavaScript (dragging, Enter and Esc, the "Claude replied" dot) is not covered.

## How it behaves

- **The server starts itself.** Any command that needs it starts it in the background (log in
  `~/.rails-board/server.log`). It stops after two hours with no browser tab, no listener and
  no requests. After the code changes, the next command restarts it and open tabs reload.
- **One page per review.** Each board has its own URL, and each Claude session listens on its
  own board, so reviews running in two projects do not take each other's messages.
- **Deliveries are short.** Findings are `F1`, `F2`, …; the messages in a thread are `F3#1`,
  `F3#2`, … (shown beside each message on the page). `rails-board wait` hands Claude only the new
  message under its ID, not the finding and thread again: the session already has those in its
  own conversation under the same IDs. `rails-board show F3` prints an issue in full for a session
  that has lost them (compacted history, or a new session), and `rails-board wait --full` restores
  the long form.
- **Enter sends, Esc takes it back.** There is no Send button. Enter sends the message and
  Shift+Enter adds a line. Esc works as it does in Claude Code: the last message you sent that
  Claude has not answered leaves the thread and returns to the box for editing, and a card the
  message had moved goes back where it was. If Claude had already picked the message up, the
  reply it was writing is refused when it tries to post it.
- **Messages queue.** A message written while no session is listening is delivered by the next
  `rails-board wait` on that board. The header says whether Claude is listening.
- **A delivery is confirmed.** `rails-board wait` tells the server once it has printed what it was
  given. If the command dies before that, the same messages go to the next `wait` about 15
  seconds later, so a killed listener cannot swallow a message or a hand-over.
- **A decision changed after hand-over waits for the next one.** If you reopen or re-settle a
  card after pressing the button but before Claude has collected it, that card is left out and
  shows as not handed over.
- **One server per data folder.** Commands that start at the same moment queue for a lock, so they
  share one server instead of each starting their own.
- **Only this machine can reach it.** The server binds `127.0.0.1` and refuses requests whose
  Host or Origin is not its own, so other web pages cannot read or post to it.
