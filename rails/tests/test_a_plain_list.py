"""A list board: the same cards and columns for a plain to-do list. Nothing on it goes to Claude."""
import re

import pytest


def numbers_and_titles(listing: str) -> list:
    return re.findall(r"^\s+(\d+)\s+(.+)$", listing, re.M)


# -- making one --

def test_each_item_becomes_a_card_numbered_as_in_the_list(todo):
    assert numbers_and_titles(todo.made) == [
        ("1", "Read the dictionary's shape"),
        ("2", "Follow where it is built"),
        ("3", "Check the feature flag at startup"),
    ]


def test_an_item_can_be_just_a_line_of_text(make_list):
    board = make_list(["Buy a domain", "Write the README"])
    assert numbers_and_titles(board.made) == [("1", "Buy a domain"), ("2", "Write the README")]


def test_an_item_keeps_its_details(todo):
    assert "`pipeline/build.py`, from `load_terms` to the cache write." in todo.claude("show", "2")


def test_an_item_without_a_title_is_refused(machine, project):
    (project / "items.json").write_text('[{"body": "Details with no heading."}]')
    assert "every item needs a title" in machine.claude_is_refused("list", "items.json", cwd=project)


def test_a_list_with_nothing_on_it_is_refused(machine, project):
    (project / "items.json").write_text("[]")
    assert "a list needs at least one item" in machine.claude_is_refused("list", "items.json", cwd=project)


def test_more_items_can_be_added_later(todo):
    (todo.project / "more.json").write_text('["Run the frontend tests"]')
    assert todo.claude("add", "more.json") == f"Added 4 to {todo.id}\n"
    assert todo.column("todo") == ["1", "2", "3", "4"]


def test_the_page_is_told_the_board_is_a_list(todo):
    assert todo.page("GET")[1]["kind"] == "list"


# -- the three columns: to do, in progress, done --

def test_every_item_starts_in_to_do(todo):
    assert todo.column("todo") == ["1", "2", "3"]


def test_the_columns_are_named_for_a_list(todo):
    listing = todo.claude("show")
    assert "To do (3)" in listing and "In progress (0)" in listing and "Done (0)" in listing


def test_a_card_dragged_to_done_is_done_with_nothing_else_asked(todo):
    todo.user_drags("2", "settled")
    assert todo.column("settled") == ["2"]


def test_done_cards_can_be_put_in_order(todo):
    todo.user_drags("1", "settled")
    todo.user_drags("3", "settled", before="1")
    assert todo.column("settled") == ["3", "1"]


def test_a_done_card_can_be_dragged_back(todo):
    todo.user_drags("2", "settled")
    todo.user_drags("2", "todo", before="1")
    assert todo.column("todo") == ["2", "1", "3"]


def test_claude_can_tick_an_item_off_when_asked_in_chat(todo):
    assert todo.claude("move", "2", "done") == "2 moved to Done\n"
    assert todo.column("settled") == ["2"]


def test_claude_can_mark_an_item_as_started(todo):
    assert todo.claude("move", "3", "doing") == "3 moved to In progress\n"


def test_progress_shows_in_the_list_of_boards(make_list, fresh_machine):
    board = make_list(on=fresh_machine)
    board.user_drags("1", "settled")
    board.user_drags("2", "discussing")
    assert "1/3 done  1 in progress" in fresh_machine.claude("boards", cwd=board.project)


# -- notes on a card --

def test_a_note_written_on_a_card_stays_in_its_thread(todo):
    todo.user_writes("1", "The second lookup is case-sensitive, ask about that.")
    assert todo.thread("1") == [("1#1", "user", "The second lookup is case-sensitive, ask about that.")]


def test_writing_a_note_does_not_move_the_card(todo):
    todo.user_writes("1", "The second lookup is case-sensitive, ask about that.")
    assert todo.column("todo") == ["1", "2", "3"]


def test_a_note_can_be_taken_back(todo):
    todo.user_writes("1", "The second lookup is case-sensitive.")
    assert todo.user_takes_back("1")["text"] == "The second lookup is case-sensitive."
    assert todo.thread("1") == []


def test_a_cards_notes_can_be_read_back(todo):
    todo.user_writes("1", "The second lookup is case-sensitive.")
    card = todo.claude("show", "1")
    assert "[1] Read the dictionary's shape" in card and "The second lookup is case-sensitive." in card


# -- nothing on a list goes to Claude --

def test_nobody_can_listen_on_a_list(todo):
    todo.user_writes("1", "A note to myself.")
    assert f"{todo.id} is a list, not a review" in todo.claude_is_refused("wait", "--timeout", 1)


@pytest.mark.parametrize("command", [
    ("reply", "1", "An answer nobody asked for."),
    ("triage", "1", "It holds.", "--verdict", "valid"),
    ("settle", "1", "--outcome", "fix"),
    ("awaiting",),
    ("decisions",),
])
def test_the_review_commands_are_refused_on_a_list(todo, command):
    assert f"{todo.id} is a list, not a review" in todo.claude_is_refused(*command)


def test_a_list_has_nothing_to_hand_over(todo):
    todo.user_drags("1", "settled")
    status, answer = todo.page("POST", "/handoff", {})
    assert (status, answer["error"]) == (400, f"{todo.id} is a list, not a review: nothing on it is handed to Claude")


def test_a_list_card_takes_no_verdict(todo):
    status, _ = todo.page("POST", "/issues/1", {"verdict": "valid", "triage": "It holds."})
    assert status == 400


# -- and a review is still a review --

def test_a_review_card_cannot_be_marked_done(board):
    refusal = board.claude_is_refused("move", "F1", "done")
    assert "on a review, settle a card with `rails-board settle F1 --outcome …`" in refusal


def test_a_review_board_is_told_apart_from_a_list(board):
    assert board.page("GET")[1]["kind"] == "review"
