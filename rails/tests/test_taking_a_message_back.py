"""Esc on the page takes back the user's last message, as it does in Claude Code."""


def test_a_message_taken_back_before_claude_got_it_never_arrives(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.user_takes_back("F1")
    assert board.nothing_reaches_claude()


def test_taking_a_message_back_returns_its_text_for_editing(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    assert board.user_takes_back("F1")["text"] == "Is the passage hashed or stored whole?"


def test_a_message_taken_back_leaves_the_thread(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.user_takes_back("F1")
    assert board.thread("F1") == []


def test_a_card_the_message_had_moved_goes_back_where_it_was(board):
    board.user_settles("F1", "fix")
    board.user_writes("F1", "Actually, one more question.")
    board.user_takes_back("F1")
    card = board.card("F1")
    assert (card["status"], card["outcome"]) == ("settled", "fix")


def test_claudes_reply_to_a_message_taken_back_is_refused(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.claude_waits()
    board.user_takes_back("F1")
    refusal = board.claude_is_refused("reply", "F1", "It is hashed.")
    assert "the user withdrew F1#1 before you replied, so this reply was not posted" in refusal
    assert "run `rails-board wait` again" in refusal
    assert board.thread("F1") == []


def test_only_the_one_reply_is_refused(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.claude_waits()
    board.user_takes_back("F1")
    board.claude_is_refused("reply", "F1", "It is hashed.")
    board.claude("reply", "F1", "Separately: I have re-read section 4.2.")
    assert board.thread("F1") == [("F1#2", "claude", "Separately: I have re-read section 4.2.")]


def test_the_rewritten_message_arrives_under_a_new_number(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.user_takes_back("F1")
    board.user_writes("F1", "Is the passage hashed, and with which function?")
    assert board.claude_waits().startswith("[F1#2] User on F1")


def test_claude_can_answer_the_rewritten_message(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.claude_waits()
    board.user_takes_back("F1")
    board.user_writes("F1", "Is the passage hashed, and with which function?")
    board.claude_waits()
    assert board.claude("reply", "F1", "Hashed, with SHA-256.") == "Replied on F1 as F1#3\n"


def test_a_message_claude_has_answered_cannot_be_taken_back(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.claude_waits()
    board.claude("reply", "F1", "It is hashed.")
    status, answer = board.page("POST", "/issues/F1/withdraw", {})
    assert (status, answer["error"]) == (400, "there is no unanswered message of yours to cancel")
