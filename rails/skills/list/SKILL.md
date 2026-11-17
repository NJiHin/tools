---
name: list
description: Put a plain list on a local kanban board (To do, In progress, Done) so the user can track it in the browser, one card per item. For to-do lists, checklists, a numbered plan or review order, steps to work through. Nothing on the board is sent to Claude and Claude does not listen on it. Use when the user types /rails:list, or says "make a board of this list", "track these as cards", "to-do board", "checklist board", "put these steps on a board", or asks for a board to keep track of items that are not review findings. For a reviewer's findings that need triage and discussion, use rails:rails instead. Nothing to do with Ruby on Rails.
---

# rails:list: a plain list as cards

This makes a board with three columns, To do, In progress and Done, and one card per item of a
list. The user drags cards between columns and can write their own notes on a card. That is all
it does. It is the simple sibling of the review board (`rails:rails`):

- **Nothing is sent to you.** There are no verdicts, no triage, no outcomes, no hand-over.
- **Do not listen.** Never run `rails-board wait` on a list; it is refused.
- **You make the board and you are done.** The user works on it alone.

The command is `rails-board`, which the plugin puts on this session's PATH. If the shell says
`rails-board: command not found`, use `${CLAUDE_PLUGIN_ROOT}/bin/rails-board` instead. Never run
plain `rails`: on macOS that is a stub for Ruby on Rails.

## 1. Find the list

Use the list the user points at: one from earlier in this conversation, a file, or text they
paste. If they say "the list you made" and there are several, ask which in one line.

## 2. Write the items

Write `items.json` in the scratchpad:

```json
[
  {"title": "The dictionary's shape", "body": "`redaction/dictionary.py`: the dataclass and its two lookups."},
  {"title": "Where it is built"},
  "An item can also be just a line of text"
]
```

- **One card per item, in the list's order.** Cards are numbered 1, 2, 3… in that order, so the
  card's number is the item's number in the list. Do not merge, split, drop or reorder items.
- `title`: the item's heading or first line, **without its number** (the card shows the number).
  Keep it under about 90 characters.
- `body` (optional): the rest of that item as it was written: sub-points, file paths, notes.
  Markdown renders. Leave it out when the item is a single line.
- Do not add items, advice or commentary of your own. If the list had none, the cards have none.

## 3. Make the board

```sh
rails-board list items.json --title "Manual review order" --open
```

Run it from inside the project (the board is tied to this folder). It prints the board id, the
numbered items and the URL, and opens the page. Give the board a short title that says what the
list is.

Then tell the user, in one line: the URL and how many cards. Do not repeat the list in chat, and
do not start anything in the background. You are finished.

## If the user asks later, in chat

The board is theirs to work, but they may ask you about it. Pass `--board <id>` each time.

| They ask | Run |
|---|---|
| "What's left?" / "Where am I?" | `rails-board show --board <id>` |
| "Mark 3 done" / "I've started 4" / "Put 2 back" | `rails-board move 3 done`, `move 4 doing`, `move 2 todo` |
| "Add these to the list" | write the new items as JSON, `rails-board add more.json --board <id>` (numbering continues) |
| "What did I note on 5?" | `rails-board show 5 --board <id>` |
| "Open it again" | `rails-board open --board <id>` |
| "I've lost the board id" | `rails-board boards` |

Only move a card when the user tells you to. Do not tick items off because you did the work
yourself, unless they asked you to keep the board up to date.

A board that nobody touches for a week is deleted. If the user wants the list to outlast that,
say so and offer to save it as a file in the project instead.
