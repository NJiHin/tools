"""`rails-board codex` prints the Codex plugin's last response for this project, exactly as written."""
import json

RESPONSE = "# Adversarial review\n\n1. **Cache key ignores the passage.**  Section 4.2 …\n\n   Keep this spacing.\n"


def codex_job(machine, job_id, workspace, raw=RESPONSE, status="completed", at="2026-11-12T10:00:00Z", summary=""):
    """A job file as the Codex plugin leaves it in its own data folder."""
    jobs = machine.home / ".claude" / "plugins" / "data" / "codex-openai-codex" / "state" / "ws1" / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / f"{job_id}.json").write_text(json.dumps({
        "id": job_id, "status": status, "workspaceRoot": str(workspace), "completedAt": at,
        "summary": summary, "result": {"rawOutput": raw}}))


def test_it_prints_the_response_exactly_as_codex_wrote_it(machine, project):
    codex_job(machine, "job-a", project)
    assert machine.claude("codex", cwd=project) == RESPONSE


def test_it_prints_the_newest_of_several_responses(machine, project):
    codex_job(machine, "job-old", project, raw="The first review.\n", at="2026-11-11T09:00:00Z")
    codex_job(machine, "job-new", project, raw="The second review.\n", at="2026-11-12T09:00:00Z")
    assert machine.claude("codex", cwd=project) == "The second review.\n"


def test_an_earlier_response_can_be_picked_by_its_job_id(machine, project):
    codex_job(machine, "job-old", project, raw="The first review.\n", at="2026-11-11T09:00:00Z")
    codex_job(machine, "job-new", project, raw="The second review.\n", at="2026-11-12T09:00:00Z")
    assert machine.claude("codex", "--job", "job-old", cwd=project) == "The first review.\n"


def test_the_list_shows_recent_jobs_newest_first(machine, project):
    codex_job(machine, "job-old", project, at="2026-11-11T09:00:00Z", summary="Review of the cache spec")
    codex_job(machine, "job-new", project, at="2026-11-12T09:30:00Z", summary="Second pass")
    first, second = machine.claude("codex", "--list", cwd=project).splitlines()
    assert first.startswith("job-new  2026-11-12 09:30") and first.endswith("Second pass")
    assert second.startswith("job-old  2026-11-11 09:00")


def test_it_works_from_a_subfolder_of_the_project(machine, project):
    codex_job(machine, "job-a", project)
    deeper = project / "docs"
    deeper.mkdir()
    assert machine.claude("codex", cwd=deeper) == RESPONSE


def test_a_response_for_another_project_is_not_used(machine, project, tmp_path):
    codex_job(machine, "job-a", tmp_path / "some-other-project")
    refusal = machine.claude_is_refused("codex", cwd=project)
    assert "no finished Codex job found for this folder" in refusal


def test_a_job_that_has_not_finished_is_not_used(machine, project):
    codex_job(machine, "job-a", project, status="running")
    assert "no finished Codex job found for this folder" in machine.claude_is_refused("codex", cwd=project)
