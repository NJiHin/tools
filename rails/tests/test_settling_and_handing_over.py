"""The user settles each card, then hands the decisions to Claude with the instruction for each fix."""

INSTRUCTION = "In docs/cache.md section 4.2, key the cache on sha256(claim + passage)."


def claude_suggests_a_fix(board, key="F1", instruction=INSTRUCTION):
    board.claude("triage", key, "It holds.", "--verdict", "valid", "--propose", "fix", "--note", instruction)


def a_listener_that_dies(board) -> dict:
    """A `wait` that takes what is queued and is killed before it can confirm it got it."""
    status, taken = board.machine.request("GET", f"/api/wait?board={board.id}&timeout=5&ack=1")
    assert status == 200, taken
    return taken


def handed_over(board) -> str:
    board.user_hands_over()
    return board.claude_waits()


def test_a_fix_is_handed_over_with_claudes_own_instruction(board):
    claude_suggests_a_fix(board)
    board.user_settles("F1", "fix")
    assert ("  [F1] Fix — Cache key ignores the passage  (docs/cache.md:88-104)\n"
            "      Your instruction:\n"
            f"        {INSTRUCTION}\n") in handed_over(board)


def test_the_instruction_handed_over_is_the_latest_one_claude_gave(board):
    claude_suggests_a_fix(board)
    board.user_writes("F1", "Hash the passage with BLAKE3, we already depend on it.")
    board.claude("reply", "F1", "Agreed.", "--propose", "fix",
                 "--note", "In docs/cache.md section 4.2, key the cache on blake3(claim + passage).")
    board.user_settles("F1", "fix")
    decisions = handed_over(board)
    assert "key the cache on blake3(claim + passage)" in decisions
    assert "sha256" not in decisions


def test_the_users_own_instruction_is_handed_over_and_takes_priority(board):
    claude_suggests_a_fix(board)
    board.user_settles("F1", "fix")
    board.user_writes_instruction("F1", "Also bump the cache version so old entries are dropped.")
    decisions = handed_over(board)
    assert ("      The user's own instruction (it takes priority over yours where they differ):\n"
            "        Also bump the cache version so old entries are dropped.\n") in decisions
    assert INSTRUCTION in decisions


def test_a_fix_claude_did_not_suggest_falls_back_to_the_reviewers_recommendation(board):
    board.claude("triage", "F1", "Real, but cheap to live with.", "--verdict", "partial",
                 "--propose", "wontfix", "--note", "Entries expire after a day anyway.")
    board.user_settles("F1", "fix")
    decisions = handed_over(board)
    assert "No instruction was recorded for this outcome: follow the reviewer's recommendation." in decisions
    assert "Entries expire after a day anyway." not in decisions


def test_claudes_reason_for_another_outcome_is_not_passed_off_as_a_fix_instruction(board):
    board.claude("triage", "F1", "Real, but cheap to live with.", "--verdict", "partial",
                 "--propose", "wontfix", "--note", "Entries expire after a day anyway.")
    board.user_settles("F1", "fix")
    board.user_writes_instruction("F1", "Fix it anyway: key on claim and passage.")
    decisions = handed_over(board)
    assert "Your instruction:" not in decisions
    assert "Fix it anyway: key on claim and passage." in decisions


def test_decisions_that_need_no_change_are_listed_by_outcome(board):
    board.user_settles("F2", "defer")
    board.user_settles("F3", "invalid")
    decisions = handed_over(board)
    assert "  [F2] Defer — Retries can double the spend\n" in decisions
    assert "  [F3] Not an issue — Metric is misnamed\n" in decisions


def test_the_hand_over_tells_claude_not_to_reread_the_threads(board):
    claude_suggests_a_fix(board)
    board.user_settles("F1", "fix")
    assert "there is no need to read the threads again" in handed_over(board)


def test_settling_alone_hands_nothing_to_claude(board):
    claude_suggests_a_fix(board)
    board.user_settles("F1", "fix")
    assert board.nothing_reaches_claude()


def test_only_settled_cards_are_handed_over(board):
    board.user_settles("F2", "defer")
    decisions = handed_over(board)
    assert "[F2] Defer" in decisions
    assert "[F1]" not in decisions and "[F3]" not in decisions


def test_a_decision_is_handed_over_once(board):
    board.user_settles("F2", "defer")
    handed_over(board)
    status, answer = board.page("POST", "/handoff", {})
    assert (status, answer["error"]) == (400, "nothing new to hand over: settle an issue first")


def test_a_later_hand_over_carries_only_the_new_decisions(board):
    board.user_settles("F2", "defer")
    handed_over(board)
    board.user_settles("F3", "wontfix")
    decisions = handed_over(board)
    assert "[F3] Won't fix" in decisions and "[F2]" not in decisions


def test_a_changed_decision_is_handed_over_again(board):
    board.user_settles("F2", "defer")
    handed_over(board)
    board.user_settles("F2", "fix")
    assert "[F2] Fix — Retries can double the spend" in handed_over(board)


def test_a_new_instruction_on_a_handed_over_card_is_handed_over_again(board):
    board.user_settles("F2", "fix")
    handed_over(board)
    board.user_writes_instruction("F2", "Use the provider's idempotency key header.")
    assert "Use the provider's idempotency key header." in handed_over(board)


# -- a decision changed between pressing the button and Claude collecting it --

def test_a_card_reopened_before_claude_collected_the_hand_over_is_not_delivered(board):
    board.user_settles("F2", "fix")
    board.user_hands_over()
    board.user_drags("F2", "todo")
    assert board.nothing_reaches_claude()


def test_a_decision_changed_before_claude_collected_it_waits_for_a_new_hand_over(board):
    board.user_settles("F2", "defer")
    board.user_hands_over()
    board.user_settles("F2", "fix")
    assert board.nothing_reaches_claude()


def test_the_changed_decision_then_goes_out_once_with_the_new_hand_over(board):
    board.user_settles("F2", "defer")
    board.user_hands_over()
    board.user_settles("F2", "fix")
    assert "[F2] Fix — Retries can double the spend" in handed_over(board)
    assert board.nothing_reaches_claude(timeout=2)


def test_the_rest_of_a_hand_over_still_arrives_when_one_card_is_reopened(board):
    board.user_settles("F2", "defer")
    board.user_settles("F3", "wontfix")
    board.user_hands_over()
    board.user_drags("F3", "todo")
    decisions = board.claude_waits()
    assert "[F2] Defer" in decisions and "[F3]" not in decisions


def test_a_hand_over_taken_by_a_listener_that_died_is_delivered_again(board):
    board.user_settles("F2", "defer")
    board.user_hands_over()
    lost = a_listener_that_dies(board)
    assert [h["issues"][0]["key"] for h in lost["handoffs"]] == ["F2"]
    assert "[F2] Defer" in board.claude_waits()


def test_a_refused_settle_leaves_the_card_where_it_was(board):
    status, _ = board.page("POST", "/issues/F2", {"status": "settled", "outcome": "fix", "verdict": "nonsense"})
    card = board.card("F2")
    assert (status, card["status"], card["outcome"]) == (400, "todo", None)


def test_settling_needs_an_outcome(board):
    status, answer = board.page("POST", "/issues/F2", {"status": "settled"})
    assert (status, answer["error"]) == (400, "settling needs an outcome: fix, defer, wontfix, invalid")


def test_a_card_dropped_on_settled_takes_claudes_suggested_outcome(board):
    claude_suggests_a_fix(board)
    board.page("POST", "/issues/F1", {"status": "settled"})
    assert board.card("F1")["outcome"] == "fix"


def test_dragging_a_settled_card_back_unsettles_it(board):
    board.user_settles("F2", "defer")
    board.user_drags("F2", "todo")
    card = board.card("F2")
    assert (card["status"], card["outcome"]) == ("todo", None)


def test_a_card_can_be_dropped_above_another(board):
    board.user_drags("F3", "todo", before="F1")
    assert board.column("todo") == ["F3", "F1", "F2"]


def test_a_card_dropped_on_a_column_goes_to_the_end(board):
    board.user_drags("F1", "discussing")
    board.user_drags("F3", "discussing")
    assert board.column("discussing") == ["F1", "F3"]


# -- Claude's own commands for the same things (used only when the user asks in chat) --

def test_claude_can_settle_a_card_when_told_to(board):
    assert board.claude("settle", "F3", "--outcome", "wontfix") == "F3 settled as Won't fix\n"
    assert board.column("settled") == ["F3"]


def test_claude_can_put_a_settled_card_back(board):
    board.user_settles("F3", "wontfix")
    assert board.claude("move", "F3", "discussing") == "F3 moved to Discussing\n"
    assert board.card("F3")["outcome"] is None


def test_the_record_of_decisions_lists_each_outcome_with_its_instruction(board):
    claude_suggests_a_fix(board)
    board.user_settles("F1", "fix")
    board.user_settles("F2", "defer")
    assert board.claude("decisions") == (
        "# Decisions — Cache spec review\n"
        "\n"
        "2 of 3 settled.\n"
        "\n"
        "## Fix\n"
        "- **F1** Cache key ignores the passage (`docs/cache.md:88-104`)\n"
        f"  - Claude's instruction: {INSTRUCTION}\n"
        "\n"
        "## Defer\n"
        "- **F2** Retries can double the spend\n"
        "\n"
        "## Not settled yet\n"
        "- **F3** Metric is misnamed (to review)\n"
    )


def test_the_board_can_be_read_as_text_column_by_column(board):
    board.claude("triage", "F1", "It holds.", "--verdict", "valid")
    board.user_settles("F2", "defer")
    listing = board.claude("show")
    assert "To review (2)" in listing and "Settled (1)" in listing
    assert "F2   high     not triaged            Retries can double the spend  → Defer" in listing
