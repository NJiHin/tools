"""Claude records its verdict on each finding, and the fix it would make."""


def test_claudes_verdict_and_reasoning_go_on_the_card(board):
    board.claude("triage", "F1", "-", "--verdict", "partial",
                 stdin="It holds for edited passages, but section 4.3 already expires entries after a day.")
    card = board.card("F1")
    assert card["verdict"] == "partial"
    assert card["triage"] == "It holds for edited passages, but section 4.3 already expires entries after a day."


def test_triage_confirms_the_verdict_in_plain_words(board):
    assert board.claude("triage", "F3", "The formula is precision.", "--verdict", "valid") == "Triaged F3: Claude agrees\n"


def test_a_suggested_fix_carries_its_instruction(board):
    board.claude("triage", "F1", "It holds.", "--verdict", "valid", "--propose", "fix",
                 "--note", "In docs/cache.md section 4.2, key the cache on sha256(claim + passage).")
    suggestion = board.card("F1")["proposal"]
    assert (suggestion["outcome"], suggestion["note"]) == (
        "fix", "In docs/cache.md section 4.2, key the cache on sha256(claim + passage).")


def test_a_long_instruction_can_come_from_a_file(board):
    steps = "1. Change the key in section 4.2.\n2. Add the passage hash to the schema in section 6.\n3. Note the migration in section 11."
    (board.project / "instruction.md").write_text(steps)
    board.claude("triage", "F1", "It holds.", "--verdict", "valid", "--propose", "fix", "--note-file", "instruction.md")
    assert board.card("F1")["proposal"]["note"] == steps


def test_an_instruction_without_a_suggested_outcome_is_refused(board):
    refusal = board.claude_is_refused("triage", "F1", "It holds.", "--verdict", "valid", "--note", "Change the key.")
    assert "--note goes with --propose" in refusal


def test_triage_leaves_the_card_where_it_is(board):
    board.claude("triage", "F1", "It holds.", "--verdict", "valid", "--propose", "fix", "--note", "Change the key.")
    assert board.card("F1")["status"] == "todo"


def test_triaging_a_finding_that_is_not_on_the_board_is_refused(board):
    assert "no issue 'F9'" in board.claude_is_refused("triage", "F9", "It holds.", "--verdict", "valid")


def test_the_whole_card_can_be_read_back(board):
    board.claude("triage", "F1", "It holds: the key omits the passage.", "--verdict", "valid", "--propose", "fix",
                 "--note", "Key the cache on sha256(claim + passage).")
    card = board.claude("show", "F1")
    assert "Claude agrees" in card
    assert "It holds: the key omits the passage." in card
    assert "Claude proposed settling as Fix: Key the cache on sha256(claim + passage)." in card
