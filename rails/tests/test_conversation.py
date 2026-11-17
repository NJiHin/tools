"""The user writes on a card; Claude is handed the message and answers in the same thread."""
import time

F1_BODY = "Section 4.2 caches a verdict under the claim text alone"


def test_a_message_reaches_claude_under_its_id(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    delivery = board.claude_waits()
    assert delivery.startswith('[F1#1] User on F1 "Cache key ignores the passage" (Discussing, not triaged yet):\n'
                               "    Is the passage hashed or stored whole?\n")


def test_claude_is_handed_only_the_new_message_not_the_finding_again(board):
    board.claude("triage", "F1", "It holds: the key omits the passage.", "--verdict", "valid")
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    delivery = board.claude_waits()
    assert "Is the passage hashed or stored whole?" in delivery
    assert F1_BODY not in delivery
    assert "It holds: the key omits the passage." not in delivery


def test_the_delivery_says_how_to_get_the_earlier_context_back(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    assert f"If you cannot see it, run: rails-board show F1 --board {board.id}" in board.claude_waits()


def test_the_delivery_says_how_to_reply(board):
    board.user_writes("F2", "Which call is retried?")
    assert f"Reply: rails-board reply F2 - --board {board.id} < reply.md" in board.claude_waits()


def test_wait_full_hands_over_the_finding_and_marks_what_is_new(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    delivery = board.claude_waits("--full")
    assert F1_BODY in delivery
    assert "NEW → F1#1 User" in delivery


def test_the_delivery_carries_claudes_verdict_on_the_finding(board):
    board.claude("triage", "F1", "It does not hold.", "--verdict", "invalid")
    board.user_writes("F1", "Why not?")
    assert "(Discussing, Claude disagrees):" in board.claude_waits()


def test_writing_on_a_card_moves_it_to_discussing(board):
    board.user_writes("F2", "Which call is retried?")
    assert board.column("discussing") == ["F2"]


def test_writing_on_a_settled_card_reopens_the_discussion(board):
    board.user_settles("F2", "wontfix")
    board.user_writes("F2", "On second thought, how often does this happen?")
    card = board.card("F2")
    assert (card["status"], card["outcome"]) == ("discussing", None)


def test_a_message_on_a_card_settled_since_reads_as_settled(board):
    board.user_writes("F1", "Use the passage hash, not the whole passage.")
    board.user_settles("F1", "fix")
    assert "(Settled as Fix, not triaged yet):" in board.claude_waits()


def test_claudes_reply_takes_the_next_number_in_the_thread(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.claude_waits()
    assert board.claude("reply", "F1", "-", stdin="It is hashed with SHA-256.") == "Replied on F1 as F1#2\n"
    assert board.thread("F1") == [("F1#1", "user", "Is the passage hashed or stored whole?"),
                                  ("F1#2", "claude", "It is hashed with SHA-256.")]


def test_a_follow_up_names_the_reply_it_comes_after(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.claude_waits()
    board.claude("reply", "F1", "It is hashed with SHA-256.")
    board.user_writes("F1", "Then say so in section 4.2.")
    assert board.claude_waits().startswith(
        '[F1#3] User on F1 "Cache key ignores the passage" (Discussing, not triaged yet, after your F1#2):')


def test_a_message_is_handed_to_claude_once(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.claude_waits()
    assert board.nothing_reaches_claude(timeout=3)


def test_a_message_taken_by_a_listener_that_died_is_delivered_again(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    status, lost = board.machine.request("GET", f"/api/wait?board={board.id}&timeout=5&ack=1")
    assert status == 200 and lost["messages"][0]["issue"]["key"] == "F1"  # taken, and never confirmed
    assert board.claude_waits().startswith("[F1#1] User on F1")


def test_a_question_claude_answered_from_awaiting_is_not_delivered_afterwards(board):
    board.user_writes("F2", "Which call is retried?")
    board.claude("awaiting")
    board.claude("reply", "F2", "The verdict call in section 5.")
    assert board.nothing_reaches_claude()


def test_a_message_written_after_that_answer_is_still_delivered(board):
    board.user_writes("F2", "Which call is retried?")
    board.claude("awaiting")
    board.claude("reply", "F2", "The verdict call in section 5.")
    board.user_writes("F2", "And how many times?")
    assert board.claude_waits().startswith("[F2#3] User on F2")


def test_a_follow_up_written_while_claude_was_replying_is_still_delivered(board):
    board.user_writes("F2", "Which call is retried?")
    board.claude_waits()
    board.user_writes("F2", "And how many times?")
    board.claude("reply", "F2", "The verdict call in section 5.")
    assert board.claude_waits().startswith("[F2#2] User on F2")


def test_waiting_ends_quietly_when_the_user_writes_nothing(board):
    ran = board.machine.run("wait", "--timeout", 1, "--board", board.id, cwd=board.project)
    assert (ran.out, ran.code) == ("Nothing arrived before the timeout.\n", 3)


def test_messages_on_several_cards_arrive_together(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.user_writes("F3", "Rename it or change the formula?")
    delivery = board.claude_waits()
    assert "[F1#1] User on F1" in delivery and "[F3#1] User on F3" in delivery


def test_two_messages_on_one_card_both_arrive(board):
    board.user_writes("F1", "Is the passage hashed or stored whole?")
    board.user_writes("F1", "And where is the hash kept?")
    delivery = board.claude_waits()
    assert "[F1#1] User on F1" in delivery and "[F1#2] User on F1" in delivery


def test_a_board_only_hands_over_its_own_messages(make_board, tmp_path):
    mine = make_board()
    other = make_board(folder=tmp_path / "another-project")
    other.user_writes("F1", "A question about the other review.")
    assert mine.nothing_reaches_claude()
    assert "A question about the other review." in other.claude_waits()


def test_a_message_written_while_nobody_listened_is_still_owed_an_answer(board):
    board.user_writes("F2", "Which call is retried?")
    owed = board.claude("awaiting")
    assert "[F2] Retries can double the spend" in owed and "Which call is retried?" in owed


def test_awaiting_prints_the_finding_in_full_for_a_session_new_to_the_board(board):
    board.user_writes("F2", "Which call is retried?")
    assert "A call that timed out is retried without checking whether the first attempt completed." in board.claude("awaiting")


def test_nothing_is_owed_once_claude_has_answered(board):
    board.user_writes("F2", "Which call is retried?")
    board.claude("reply", "F2", "The verdict call in section 5.")
    assert board.claude("awaiting") == "No issue is waiting for a Claude reply.\n"


def test_a_reply_can_change_claudes_verdict(board):
    board.claude("triage", "F1", "It holds.", "--verdict", "valid")
    board.user_writes("F1", "Section 4.3 already includes the passage in the key.")
    board.claude("reply", "F1", "You are right, 4.3 covers it.", "--verdict", "invalid")
    assert board.card("F1")["verdict"] == "invalid"


def test_a_reply_can_replace_claudes_suggestion(board):
    board.claude("triage", "F1", "It holds.", "--verdict", "valid", "--propose", "fix", "--note", "Change the key.")
    said = board.claude("reply", "F1", "Given 4.3, leave it.", "--propose", "wontfix", "--note", "Section 4.3 already covers it.")
    suggestion = board.card("F1")["proposal"]
    assert (suggestion["outcome"], suggestion["note"]) == ("wontfix", "Section 4.3 already covers it.")
    assert said == "Replied on F1 as F1#1; the card's suggestion is now Won't fix with your instruction\n"


def test_a_reply_that_suggests_nothing_keeps_the_earlier_suggestion(board):
    board.claude("triage", "F1", "It holds.", "--verdict", "valid", "--propose", "fix", "--note", "Change the key.")
    board.claude("reply", "F1", "Yes, SHA-256.")
    assert board.card("F1")["proposal"]["note"] == "Change the key."


def test_claudes_reply_does_not_move_the_card(board):
    board.claude("reply", "F1", "A note from Claude before anyone asked.")
    assert board.card("F1")["status"] == "todo"


def test_an_empty_reply_is_refused(board):
    assert "a message needs text" in board.claude_is_refused("reply", "F1", "-", stdin="  \n")


def test_the_page_can_tell_when_claude_is_listening(board, machine):
    def listening():
        return board.id in machine.request("GET", "/api/boards")[1]["listening"]

    assert not listening()
    waiting = machine.start("wait", "--board", board.id, cwd=board.project)
    try:
        deadline = time.time() + 5
        while not listening() and time.time() < deadline:
            time.sleep(0.1)
        assert listening()
    finally:
        waiting.kill()
        waiting.wait()


def test_a_waiting_claude_is_woken_by_the_users_message(board, machine):
    waiting = machine.start("wait", "--board", board.id, cwd=board.project)
    try:
        time.sleep(0.5)
        board.user_writes("F3", "Rename it or change the formula?")
        out, _ = waiting.communicate(timeout=10)
    finally:
        waiting.kill()
    assert out.startswith('[F3#1] User on F3 "Metric is misnamed"')
