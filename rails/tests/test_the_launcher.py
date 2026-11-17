"""The `rails-board` command finds its own code wherever it is run from."""
import os

from conftest import LAUNCHER

NOT_RUNNING = "rails-board: no server running (any command that needs one starts it)\n"


def test_the_command_works_through_a_link_on_the_path(fresh_machine, tmp_path):
    link = tmp_path / "bin" / "rails-board"
    link.parent.mkdir()
    link.symlink_to(LAUNCHER)
    fresh_machine.launcher = link
    assert fresh_machine.claude("status", cwd=tmp_path) == NOT_RUNNING


def test_the_command_works_through_a_relative_link(fresh_machine, tmp_path):
    link = tmp_path / "bin" / "rails-board"
    link.parent.mkdir()
    os.symlink(os.path.relpath(LAUNCHER, link.parent), link)
    fresh_machine.launcher = link
    assert fresh_machine.claude("status", cwd=tmp_path) == NOT_RUNNING


def test_the_command_works_through_a_link_to_a_link(fresh_machine, tmp_path):
    first = tmp_path / "opt" / "rails-board"
    first.parent.mkdir()
    first.symlink_to(LAUNCHER)
    link = tmp_path / "bin" / "rb"
    link.parent.mkdir()
    link.symlink_to(first)
    fresh_machine.launcher = link
    assert fresh_machine.claude("status", cwd=tmp_path) == NOT_RUNNING
