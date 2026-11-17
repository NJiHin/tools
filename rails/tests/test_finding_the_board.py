"""Which board a command acts on when Claude does not name one."""


def test_a_command_run_in_the_project_finds_the_projects_board(make_board, machine, project):
    make_board()
    assert machine.claude("show", cwd=project).startswith("Cache spec review  (")


def test_a_command_run_in_a_subfolder_finds_it_too(make_board, machine, project):
    board = make_board()
    deeper = project / "src" / "cache"
    deeper.mkdir(parents=True)
    assert f"({board.id})" in machine.claude("show", cwd=deeper)


def test_the_newest_board_made_from_the_folder_wins(make_board, machine, project):
    make_board({"title": "First round", "findings": [{"title": "a"}]})
    make_board({"title": "Second round", "findings": [{"title": "b"}]})
    assert machine.claude("show", cwd=project).startswith("Second round  (")


def test_a_folder_with_no_board_says_so(make_board, machine, tmp_path):
    make_board()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    refusal = machine.claude_is_refused("show", cwd=elsewhere)
    assert "no board was made from this folder; pass --board ID (see `rails-board boards`)" in refusal


def test_a_board_can_be_named_from_anywhere(board, machine, tmp_path):
    assert f"({board.id})" in machine.claude("show", "--board", board.id, cwd=tmp_path)


def test_the_start_of_a_board_id_is_enough(make_board, fresh_machine, tmp_path):
    board = make_board(on=fresh_machine)
    assert f"({board.id})" in fresh_machine.claude("show", "--board", "cache-spec", cwd=tmp_path)


def test_a_start_that_fits_two_boards_is_refused(make_board, fresh_machine, tmp_path):
    make_board(on=fresh_machine)
    make_board(on=fresh_machine)
    assert "no board 'cache-spec'" in fresh_machine.claude_is_refused("show", "--board", "cache-spec", cwd=tmp_path)


def test_the_environment_can_name_the_board(board, machine, tmp_path):
    assert f"({board.id})" in machine.claude("show", cwd=tmp_path, env={"RAILS_BOARD": board.id})


def test_an_unknown_board_is_refused_with_the_boards_that_exist(make_board, fresh_machine, project):
    board = make_board(on=fresh_machine)
    refusal = fresh_machine.claude_is_refused("show", "--board", "auth-review-0000", cwd=project)
    assert f"no board 'auth-review-0000'. Boards: {board.id}" in refusal


def test_boards_lists_each_review_with_its_progress(make_board, fresh_machine):
    board = make_board(on=fresh_machine)
    board.user_settles("F1", "fix")
    board.user_writes("F2", "Which call is retried?")
    listing = fresh_machine.claude("boards", cwd=board.project)
    assert listing.startswith(f"{board.id}")
    assert "1/3 settled  1 discussing" in listing and listing.rstrip().endswith("Cache spec review")
