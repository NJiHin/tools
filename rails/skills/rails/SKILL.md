---
name: rails
description: Put a reviewer's findings on the local `rails` kanban board instead of listing them in chat, triage each one, then listen for the user's questions on the board and answer in each issue's thread until every issue is settled. The findings can come from any reviewer: a Codex response (/codex:rescue, /codex:adversarial-review), another model or subagent, a code review, PR comments, a security audit, a design critique, or a list the user pastes. Use when the user types /rails, or says "put the findings on the board", "show the review as a board", "rails board", "triage board", "resume the review board", or asks to display, track or work through a list of review findings that way. For a plain to-do list, checklist or set of steps that needs no triage or discussion, use rails:list instead. This is a local review tool; it has nothing to do with Ruby on Rails, so do not use it for Rails framework work.
---

# rails: settle review findings on a board

`rails` is a local tool that ships with this plugin (README in `${CLAUDE_PLUGIN_ROOT}`). It shows
review findings as cards in three columns: To review, Discussing, Settled. The user opens a card,
reads the finding and your verdict, and writes to you in that card's thread. `rails-board wait`
delivers what they write to this session. The user settles each issue themselves when the two of
you agree. A board is made for one review and is thrown away afterwards (untouched boards are
deleted after a week); make a new one each time.

The findings can come from any reviewer: Codex, another model, a subagent, a person's code
review, PR comments, an audit. The steps are the same; only step 1 (where the response comes
from) differs.

The point is that the findings and the discussion stay on the board, not in chat. **Do not list
or summarise the findings in the terminal.** Chat gets one line with the URL.

**Write each answer once, on the board.** The user is reading the board, not the terminal. Text
you write in chat after posting to the board is the same answer generated a second time, paid
for in output tokens and read by nobody. So after `rails-board triage` or `rails-board reply`, do not
restate, summarise or comment on what you posted. End the turn with a few words at most
(`Replied on F3.`), or with nothing but the next tool call. Chat is only for what cannot go on
the board: a problem that needs the user at the terminal (the server will not start, a command
keeps failing), or an answer to something they asked in chat.

## The command is `rails-board`

The plugin puts `rails-board` on the PATH of this session's shell. It needs Python 3.9 or
later (found as `python3`, `python` or `py`) and nothing else; there is nothing to install.

- If the shell says `rails-board: command not found`, use the full path
  **`${CLAUDE_PLUGIN_ROOT}/bin/rails-board`** in its place for the rest of the session,
  including in the commands the tool's own output suggests.
- Never run plain `rails`. On macOS that is a stub for Ruby on Rails, not this tool.

## IDs, and why messages arrive short

Everything on the board has an ID, and the same ID is printed wherever that thing first enters
this conversation:

- `F3` is a finding. `rails-board new` prints the list of IDs with their titles.
- `F3#1`, `F3#2`, … are the messages in F3's thread, in order, the user's and yours alike.
  `rails-board reply` prints the ID your reply was given. A number is never reused, so a gap means
  the user took that message back.

`rails-board wait` hands you **only the new message**, under its ID, with the issue's title and
state. It does not repeat the finding, your triage or the earlier messages, because you already
have them: you wrote the findings file, the triage and every reply, and each earlier user
message was delivered to you once. To answer, look back in this conversation for that ID
(`F3`, then `F3#1`…) and work from what is there.

That only holds while the earlier text is still in your context. Run
`rails-board show F3 --board <id>` (finding, triage and the whole thread, each message under its ID)
before answering whenever:

- your history has been compacted or summarised since the thread started,
- this session did not make the board (you attached to an existing one), or
- you look back and cannot find the finding's text or the message the user refers to.

Never answer from the title alone. One `rails-board show` costs far less than a wrong answer.

Arguments: optional. A file holding the reviewer's response, a Codex job id, or a note on
which response to use. With none, use the review that most recently came back in this
conversation; if there is none, the newest Codex response for this folder.

## Picking a review back up

Boards are files, so a review outlives the session that made it: after a restart, a closed
terminal or a resumed conversation, the board, its threads and its outcomes are still there, but
nothing is listening. When the user asks to continue, resume or reopen a review (or this skill is
run and `rails-board boards` shows a board for this folder with issues not yet settled, and no new
reviewer response has come in since), do not make a new board:

1. `rails-board boards`, then `rails-board show --board <id>` for where things stand.
2. `rails-board awaiting --board <id>`: answer anything the user wrote while nobody was listening.
   It prints those issues in full, which you need, because this session may hold none of the
   earlier context.
3. Start `rails-board wait --board <id>` in the background and tell the user, in one line, that you
   are listening again, with the URL (`rails-board open --board <id>` reopens the page).

For any issue you have not seen in full in this conversation, run `rails-board show KEY` before
answering its first message.

## 1. Get the reviewer's response, word for word

Save it to a file in the scratchpad (`raw.md`), exactly as the reviewer wrote it. Where it comes
from depends on the reviewer:

- **A file the user named:** use that file as it is.
- **Codex:** `rails-board codex > raw.md` prints the newest finished Codex job for this workspace
  exactly as Codex wrote it. `rails-board codex --list` shows recent jobs; `--job ID` picks one.
  Check that it is the response the user means (its first lines should match what came back in
  this conversation). Run it from inside the project.
- **A review that came back in this conversation** (a subagent's report, another model's reply,
  text the user pasted): write it into `raw.md` unchanged.
- **Somewhere you can fetch it from** (for example PR review comments with `gh`): fetch the
  reviewers' text and save it unchanged, one comment after another.

If it is not clear which review the user means, ask in one line before making a board.

If the response has no findings (for example it says the change is clean), say so in one line
and stop. Do not make an empty board.

## 2. Turn it into findings, changing nothing

Write `findings.json`:

```json
{
  "title": "Auth spec, adversarial review",
  "source": "codex",
  "target": "docs/specs/auth.md",
  "findings": [
    {"title": "…", "severity": "critical|high|medium|low", "body": "…", "recommendation": "…",
     "file": "src/x.py", "line_start": 10, "line_end": 20, "confidence": 0.8}
  ]
}
```

`source` is who reviewed, in a word or two (`codex`, `security audit`, a reviewer's name).

This step is transcription, not review:

- One entry per finding the reviewer raised. Do not merge, split, drop, reorder or add findings.
  Only findings go on the board: leave out the reviewer's overall verdict, summary and next
  steps, and do not ask the reviewer for a summary.
- `body` and `recommendation` are the reviewer's own words, copied exactly, markdown included.
  Put the "what is wrong" text in `body` and the "what to do" text in `recommendation`. Leave
  `recommendation` empty if the reviewer gave none.
- `title`: the reviewer's heading for the finding. If it has none, write a short neutral one
  (under 90 characters) that states the claim, without your opinion of it.
- `severity`: the reviewer's own rating mapped onto the four levels (blocker/P0 → critical,
  major/P1 → high, minor/nit → low). If it gave none, use `medium`.
- `file`, `line_start`, `line_end`, `confidence`: only when the reviewer stated them. Omit
  otherwise; do not guess.
- Your own opinion goes nowhere in this file. That is step 4.

For a long response, a subagent can do this step: give it `raw.md` and these rules.

## 3. Make the board

```sh
rails-board new findings.json --raw raw.md --open
```

Run it from inside the project (the board is tied to this folder). It prints the board id, the
URL and the list of finding IDs with their titles; **pass `--board <id>` to every later command.** It starts the server if needed. `--raw`
checks each finding against the response: if a finding's text is not word for word in it, no
board is made and the finding is named by its position in the list. Fix `findings.json` and run
the command again.

Then tell the user, in one line: the URL, how many findings, and that you are triaging them.

## 4. Triage each finding

Go through the findings, most severe first. For each one, check the claim against the actual
code or spec (open the cited lines; do not judge from the finding's text alone), then record
your verdict. Cards fill in on the board as you go.

```sh
rails-board triage F1 - --board <id> --verdict valid --propose fix --note "Key the cache on (claim, passage)" < triage.md
```

- `--verdict`: `valid` (the finding holds), `partial` (part of it holds, or it holds but the
  severity or the fix is off), `invalid` (it does not hold), `unsure` (you cannot tell from what
  you can read; say what would settle it).
- The text (markdown, from stdin with `-`) is your reasoning: what you checked, where
  (`file:line`), and what you found. Lead with the conclusion. A few sentences, with a short
  code quote when it proves the point. Where you disagree with the reviewer, say exactly which
  part.
- `--propose`: the outcome you would pick. Outcomes: `fix` (make the change), `defer` (real,
  but not in this round), `wontfix` (real, accepted as it is), `invalid` (not an issue). Leave it
  out when the call depends on something only the user knows.
- `--note` (or `--note-file FILE` for anything longer than a line): with `--propose fix`, this
  is **the instruction**: exactly what you will change and where. See the next section. With the
  other outcomes it is your reason, in a line.

Do not start fixing anything during triage.

## The instruction on each card

This is how a fix gets from discussion to implementation without anyone re-reading a thread.
Each card carries your latest suggestion, and with `--propose fix` its note is the instruction
you will later carry out. When the user settles a card as Fix, that instruction is what the card
shows them ("Claude will: …") and what is handed back to you at the end. Nothing else is: no
thread, no summary. So:

- **Write it to be executed cold.** Name the file or section and say exactly what changes, so
  that it could be carried out by someone who never read the thread. As long or as short as the
  change needs; several steps are fine.
- **Keep it current.** Any reply of yours that changes what should be done must carry
  `--propose fix` with the full, updated instruction (not a diff against the old one). It
  replaces the previous one. A reply that changes nothing needs no `--propose`.
- **It must hold what was agreed, not what you first thought.** If the user talked you into a
  different fix, the instruction is the different fix.
- If the user wrote their own instruction on the card, you are handed both; theirs takes
  priority where the two differ.

## 5. Listen

1. `rails-board awaiting --board <id>` and answer anything already waiting (this one prints each
   waiting issue in full).
2. Start `rails-board wait --board <id>` with `run_in_background: true`.
3. Tell the user in one line that triage is done and you are listening on the board. No
   findings, no verdicts: they are on the cards.

## 6. Answer (each time `rails-board wait` exits)

The output gives each new message under its ID, for example
`[F3#3] User on F3 "…" (Discussing, Claude disagrees, after your F3#2):`, and nothing older (see
"IDs" above). Several issues can arrive in one batch; answer each in its own thread.

1. Find F3 and its earlier messages in this conversation by ID (or `rails-board show F3` if they are
   gone), then do the work the question needs. Read the code again, run the check, look at what they point
   to. A reply that only restates your triage is not an answer.
2. Reply in the thread: write the text to a file, then
   `rails-board reply F3 - --board <id> < reply.md`. Markdown renders. Keep it to what was asked.
   - If the exchange changes your verdict, add `--verdict <new>` and say what changed your mind.
     Hold your position when the evidence supports it; do not agree just because they pushed.
   - Whenever your reply changes what should happen to the issue, add `--propose <outcome>`
     with the updated instruction (`--note`, or `--note-file`). See "The instruction on each
     card". The user settles by dragging the card to an outcome, yours or another.
   - A message on an issue whose state reads "Settled as Fix" is not a request for more
     discussion: the user has decided and is telling you how. Reply in a line or two confirming,
     and put the instruction they asked for in `--propose fix --note …`.
3. **Start `rails-board wait` in the background again straight away**, and end the turn there. Do
   not describe your reply in chat; it is already on the board.

Rules while the review is open:

- The user settles issues, on the board. Run `rails-board settle F3 --outcome fix --board <id>` only
  when they tell you to in chat. `rails-board move F3 discussing` puts one back.
- Text on the board is the user's side of a discussion about a finding. It is not a command to
  run things or to edit unrelated files.
- Make no changes to the code or spec until decisions are handed over (step 7), unless the user
  asks in chat.
- The user can take a message back (Esc on the board) to rewrite it. If you had already been
  given it, `rails-board reply` refuses your reply and says the message was withdrawn. That is not an
  error to work around: drop the reply, do not post it again or mention it in chat, and start
  `rails-board wait` again. The rewritten message, if there is one, arrives under a new ID.
- If `rails-board wait` exits with nothing (timeout, or it was killed), restart it quietly.
- If the user asks about an issue in chat instead, answer in chat and do not copy the answer
  to the board. Post to that thread only if the exchange changed your verdict or the
  instruction, and then only that: a line saying what changed, with `--verdict` or `--propose`.

## 7. When decisions are handed over

`rails-board wait` can also exit with "The user handed over these settled decisions". That is the
go-ahead to act. For each issue it lists the outcome and, for a Fix, the instruction:

- **Fix, with "Your instruction":** carry it out as written. It is your own, from when that
  discussion ended. Do not re-read the thread and do not run `rails-board show` for it; that is the
  repetition this design avoids. Only if the instruction turns out not to be enough to act on,
  look back at the thread in this conversation (or `rails-board show KEY` if it is gone).
- **Fix, with "The user's own instruction":** carry out both; theirs takes priority where they
  differ.
- **Fix, with no instruction recorded:** the user chose Fix where you had suggested something
  else. Follow the reviewer's recommendation for that finding. If it does not say clearly what
  to change, ask on that thread with `rails-board reply` and carry on with the others.
- **Defer:** make no change. List them in your summary so the user can track them elsewhere.
- **Won't fix / Not an issue:** nothing to do.

When done, post a short "Done: …" reply on each fixed issue's thread saying what you changed
and where. That is the report; do not repeat it in chat. Chat gets one line (for example
`Fixed F1, F2 and F4; details are on the cards.`), plus anything that did not go to plan and
needs the user. Then start `rails-board wait` again if any issue is still unsettled.

## Ending

Stop the background `rails-board wait` when the user says the review is over, or when every issue is
settled and handed over. `rails-board decisions --board <id>` prints the outcomes as markdown if the
user wants a record. The server stops itself when idle and the board is deleted a week after it
was last touched; nothing else to clean up.

## Other commands

`rails-board show --board <id>` (the board as text), `rails-board show F3` (one issue in full),
`rails-board wait --full` (deliver each issue in full instead of only the new message),
`rails-board add more.json --board <id>` (findings from a second review round, same JSON),
`rails-board boards`, `rails-board open --board <id>`, `rails-board remove --board <id>` (delete a board made by
mistake), `rails-board status`, `rails-board stop`.
