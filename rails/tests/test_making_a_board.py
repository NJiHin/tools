"""Putting a reviewer's findings on a board."""
import re


def ids_and_titles(listing: str) -> list:
    return re.findall(r"^\s+(F\d+)\s+\w+\s+(.+)$", listing, re.M)


def test_each_finding_gets_an_id_in_the_reviewers_order(board):
    assert ids_and_titles(board.made) == [
        ("F1", "Cache key ignores the passage"),
        ("F2", "Retries can double the spend"),
        ("F3", "Metric is misnamed"),
    ]


def test_new_prints_the_address_of_the_board(board, machine):
    assert board.made.split()[-1] == f"{machine.url}/#{board.id}"


def test_the_card_holds_the_reviewers_words_unchanged(board):
    card = board.claude("show", "F1")
    assert "Section 4.2 caches a verdict under the claim text alone, so an edited passage serves a stale verdict." in card
    assert "Key the cache on the claim and the passage together." in card


def test_the_card_says_where_the_finding_points(board):
    assert "docs/cache.md:88-104" in board.claude("show", "F1")


def test_every_finding_starts_in_to_review(board):
    assert board.column("todo") == ["F1", "F2", "F3"]


def test_a_title_is_all_a_finding_needs(make_board):
    board = make_board([{"title": "Only a title"}])
    assert ids_and_titles(board.made) == [("F1", "Only a title")]
    assert board.card("F1")["severity"] == "medium"


def test_a_finding_without_a_title_is_refused(machine, project):
    (project / "findings.json").write_text('{"findings": [{"body": "No heading on this one."}]}')
    assert "every finding needs a title" in machine.claude_is_refused("new", "findings.json", cwd=project)
    assert "no board was made from this folder" in machine.claude_is_refused("show", cwd=project)


def test_the_reviewers_own_severity_words_are_understood(make_board):
    board = make_board([{"title": "a", "severity": "blocker"}, {"title": "b", "severity": "major"},
                        {"title": "c", "severity": "minor"}, {"title": "d", "severity": "nit"}])
    assert [board.card(k)["severity"] for k in ("F1", "F2", "F3", "F4")] == ["critical", "high", "low", "low"]


def test_a_severity_that_means_nothing_is_refused(machine, project):
    (project / "findings.json").write_text('[{"title": "a", "severity": "spicy"}]')
    assert "severity must be one of critical, high, medium, low" in machine.claude_is_refused("new", "findings.json", cwd=project)


def test_a_file_that_is_not_json_is_refused(machine, project):
    (project / "findings.json").write_text("1. The cache key is wrong")
    assert "that isn't valid JSON" in machine.claude_is_refused("new", "findings.json", cwd=project)


# -- checking the findings against what the reviewer actually wrote (`--raw`) --

RAW = """# Review

## 1. Cache key ignores the passage (critical)

Section 4.2 caches a verdict under the claim text alone,
so an edited passage serves a stale verdict.

**Fix:** Key the cache on the claim and the passage together.

## 2. Retries can double the spend (high)

A call that timed out is retried without checking whether the first attempt completed.

Make the call idempotent before retrying it.
"""

QUOTED = [
    {"title": "Cache key ignores the passage",
     "body": "Section 4.2 caches a verdict under the claim text alone, so an edited passage serves a stale verdict.",
     "recommendation": "Key the cache on the claim and the passage together."},
    {"title": "Retries can double the spend",
     "body": "A call that timed out is retried without checking whether the first attempt completed.",
     "recommendation": "Make the call idempotent before retrying it."},
]


def test_findings_that_quote_the_reviewer_word_for_word_are_accepted(make_board, project):
    (project / "raw.md").write_text(RAW)
    board = make_board(QUOTED, "--raw", "raw.md")
    assert ids_and_titles(board.made) == [("F1", "Cache key ignores the passage"), ("F2", "Retries can double the spend")]


def test_a_reworded_finding_is_refused_and_named_by_its_position(machine, project):
    import json
    (project / "raw.md").write_text(RAW)
    reworded = [QUOTED[0], {**QUOTED[1], "body": "Timed-out calls get retried blindly, which can double the bill."}]
    (project / "findings.json").write_text(json.dumps(reworded))
    refusal = machine.claude_is_refused("new", "findings.json", "--raw", "raw.md", cwd=project)
    assert "the text of finding 2 is not word for word in raw.md" in refusal


def test_no_board_is_made_from_reworded_findings(machine, project):
    import json
    (project / "raw.md").write_text(RAW)
    (project / "findings.json").write_text(json.dumps([{**QUOTED[0], "body": "The cache key is wrong and stale verdicts get served."}]))
    machine.claude_is_refused("new", "findings.json", "--raw", "raw.md", cwd=project)
    assert "no board was made from this folder" in machine.claude_is_refused("show", cwd=project)


def test_a_short_invented_finding_is_refused(machine, project):
    (project / "raw.md").write_text(RAW)
    (project / "findings.json").write_text('[{"title": "Made up", "body": "Invented claim."}]')
    refusal = machine.claude_is_refused("new", "findings.json", "--raw", "raw.md", cwd=project)
    assert "the text of finding 1 is not word for word in raw.md" in refusal


def test_short_paragraphs_the_reviewer_did_write_are_accepted(make_board, project):
    (project / "raw.md").write_text("## Cache\n\nToo slow.\n\nFix: Use a cache.\n")
    board = make_board([{"title": "Cache", "body": "Too slow.", "recommendation": "Use a cache."}], "--raw", "raw.md")
    assert ids_and_titles(board.made) == [("F1", "Cache")]



def test_a_finding_that_leaves_out_a_paragraph_is_refused(machine, project):
    import json
    (project / "raw.md").write_text("## Cache\n\nIt is slow.\n\nIt is also wrong.\n\nIt leaks memory.\n")
    (project / "findings.json").write_text(json.dumps([{"title": "Cache", "body": "It is slow.\n\nIt leaks memory."}]))
    refusal = machine.claude_is_refused("new", "findings.json", "--raw", "raw.md", cwd=project)
    assert "the text of finding 1 is not word for word in raw.md" in refusal

# -- later changes to the board --

def test_a_second_round_of_findings_continues_the_numbering(board):
    (board.project / "more.json").write_text('[{"title": "Batch size exceeds the provider limit"}]')
    assert board.claude("add", "more.json") == f"Added F4 to {board.id}\n"
    assert "F4" in board.column("todo")


def test_a_refused_second_round_adds_none_of_its_findings(board):
    (board.project / "more.json").write_text('[{"title": "Batch size exceeds the provider limit"}, {"body": "No heading."}]')
    board.claude_is_refused("add", "more.json")
    assert board.column("todo") == ["F1", "F2", "F3"]


def test_a_refused_second_round_does_not_use_up_numbers(board):
    (board.project / "more.json").write_text('[{"title": "Batch size exceeds the provider limit"}, {"body": "No heading."}]')
    board.claude_is_refused("add", "more.json")
    (board.project / "more.json").write_text('[{"title": "Batch size exceeds the provider limit"}]')
    assert board.claude("add", "more.json") == f"Added F4 to {board.id}\n"


def test_a_removed_board_is_gone(board):
    board.claude("remove")
    assert f"no board '{board.id}'" in board.claude_is_refused("show")
