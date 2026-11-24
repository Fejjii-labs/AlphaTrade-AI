"""Development scope cannot replace the explicit complete release acceptance gate."""

import importlib.util
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "backend_ci_scope", ROOT / "scripts/run_backend_focused.py"
)
assert SPEC is not None and SPEC.loader is not None
scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scope)


def test_changed_sfp_receipts_include_rolling_reuse_refusals_and_logging():
    selected = scope.select_tests(
        ["backend/src/app/persistence/public_market_observations.py"], ROOT / "backend"
    )
    assert set(scope.SFP_TESTS).issubset(selected)
    assert set(scope.BASE_TESTS).issubset(selected)
    assert len(selected) < 20


def test_changed_tests_and_direct_module_regressions_are_selected():
    selected = scope.select_tests(
        ["backend/tests/test_knowledge_file_import.py", "backend/src/app/core/config.py"],
        ROOT / "backend",
    )
    # Use an existing unrelated test, not only the current SFP regression pack.
    assert "tests/test_knowledge_file_import.py" in selected
    assert "tests/test_config.py" in selected


def test_schema_changes_select_migration_and_alembic_tests():
    backend = ROOT / "backend"
    expected = scope.migration_tests(backend)
    assert "tests/test_alembic_heads_helper.py" in expected
    assert any("migration" in Path(name).name for name in expected)
    for changed in (
        ["backend/src/app/db/models.py"],
        ["backend/src/app/db/migrations/versions/a22papertradeday001_paper_trade_slot_day.py"],
    ):
        selected = scope.select_tests(changed, backend)
        assert set(expected).issubset(selected)
        assert set(scope.BASE_TESTS).issubset(selected)
    unrelated = scope.select_tests(["backend/src/app/core/config.py"], backend)
    assert "tests/test_alembic_heads_helper.py" not in unrelated


def test_alembic_config_and_heads_helper_select_migration_tests():
    backend = ROOT / "backend"
    expected = scope.migration_tests(backend)
    assert "tests/test_alembic_heads_helper.py" in expected
    for changed in (
        ["backend/alembic.ini"],
        ["backend/src/app/db/migrations/env.py"],
        ["backend/tests/support/alembic_heads.py"],
    ):
        selected = scope.select_tests(changed, backend)
        assert set(expected).issubset(selected)
        assert set(scope.BASE_TESTS).issubset(selected)


def test_missing_tests_and_paths_outside_tests_cannot_expand_selection():
    selected = scope.select_tests(
        ["backend/tests/test_deleted.py", "backend/tests/../../test_escape.py", "docs/README.md"],
        ROOT / "backend",
    )
    assert selected == sorted(scope.BASE_TESTS)


@pytest.mark.parametrize("base,head", [("--output=unsafe", "a" * 40), ("", "HEAD;bad")])
def test_invalid_commit_refs_fail_before_invoking_git(base, head):
    with patch.object(scope.subprocess, "check_output") as git, pytest.raises(ValueError):
        scope.changed_paths(ROOT, base, head)
    git.assert_not_called()


@pytest.mark.parametrize("base", ["", "0" * 40, "b" * 40])
def test_commit_diff_uses_fixed_arguments_and_includes_deleted_or_merge_changes(base):
    with patch.object(
        scope.subprocess, "check_output", return_value="backend/src/app/core/config.py\n"
    ) as git:
        assert scope.changed_paths(ROOT, base, "a" * 40) == ["backend/src/app/core/config.py"]
    command = git.call_args.args[0]
    assert git.call_args.kwargs == {"cwd": ROOT, "text": True}
    if base == "b" * 40:
        assert command == [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACDMR",
            base,
            "a" * 40,
            "--",
        ]
    else:
        assert "-m" in command and "--root" in command


def test_focused_command_preserves_pytest_failure_and_reports_incomplete_acceptance(
    monkeypatch, tmp_path, capsys
):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr("sys.argv", ["runner", "--head", "a" * 40])
    with (
        patch.object(scope, "changed_paths", return_value=[]),
        patch.object(scope.subprocess, "run") as run,
    ):
        run.return_value.returncode = 1
        assert scope.main() == 1
    assert run.call_args.args[0] == ["uv", "run", "pytest", *sorted(scope.BASE_TESTS)]
    assert run.call_args.kwargs["check"] is False
    assert "full backend release acceptance is pending" in capsys.readouterr().out
    assert "full backend release acceptance is pending" in summary.read_text()


def test_workflow_keeps_quality_evaluation_browser_and_explicit_full_release_gate():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    trigger = workflow["on"]
    assert set(trigger) == {"pull_request", "workflow_dispatch"}
    full = trigger["workflow_dispatch"]["inputs"]["full_backend"]
    assert full == {
        "description": "Run the complete backend suite for final release acceptance",
        "type": "boolean",
        "required": "true",
        "default": "true",
    }
    jobs = workflow["jobs"]
    assert set(jobs) == {
        "backend",
        "backend-full",
        "frontend",
        "deployment-safety",
        "evaluation",
        "docker-build",
        "e2e-smoke",
    }
    assert "schedule" not in trigger
    assert trigger["pull_request"]["types"] == [
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
        "edited",
    ]
    scope_script = (ROOT / ".github/workflows/ci-scope.sh").read_text()
    assert "git diff --name-only --no-renames" in scope_script
    assert "unrelated-label" not in scope_script
    safety_runs = [step.get("run") or "" for step in jobs["deployment-safety"]["steps"]]
    assert any("ci-scope.sh --self-test" in run for run in safety_runs)
    self_test = subprocess.run(
        ["bash", str(ROOT / ".github/workflows/ci-scope.sh"), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert self_test.returncode == 0, self_test.stderr + self_test.stdout
    for case in (
        "ok docs-push",
        "ok edited-title-docs-does-not-skip",
        "ok retarget-draft-code",
        "ok reopened-draft-code",
        "ok ready-draft-flag",
        "ok fork-missing-head",
        "ok draft-lookalike-label",
        "rename-into-docs",
        "labeled-other-does-not-skip",
    ):
        assert case in self_test.stdout
    assert "env -i" in scope_script
    assert "*,run-ci,*" not in scope_script
    assert "edited-not-base" in scope_script
    assert "pull/${CI_SCOPE_PR_NUMBER}/head" in scope_script
    concurrency = workflow["concurrency"]
    assert concurrency == {
        "group": "${{ github.workflow }}-${{ github.ref }}",
        "cancel-in-progress": "true",
    }
    backend = jobs["backend"]
    steps = {step.get("name"): step for step in backend["steps"]}
    assert "bash .github/workflows/ci-scope.sh" in steps["Decide CI scope"]["run"]
    paths = steps["Select checks by path"]
    # The Full-Backend trailer is local evidence only: no commit message can
    # start the hosted full suite (Rule 11), only workflow_dispatch can.
    assert "trailers" not in paths["run"] and "%B" not in paths["run"]
    assert 'full_backend="$DISPATCH_FULL"' in paths["run"]
    assert paths["env"]["DISPATCH_FULL"] == "${{ inputs.full_backend }}"
    assert backend["outputs"]["full_backend"] == "${{ steps.paths.outputs.full_backend }}"
    assert steps["Ruff check"]["run"] == "uv run ruff check . ../scripts/run_backend_focused.py"
    assert steps["Ruff format"]["run"] == (
        "uv run ruff format --check . ../scripts/run_backend_focused.py"
    )
    full_job = jobs["backend-full"]
    assert full_job["needs"] == "backend"
    assert (
        full_job["if"] == "always() && !cancelled() && needs.backend.outputs.full_backend == 'true'"
    )
    assert "postgres" in full_job["services"] and "services" not in backend
    full_steps = {step.get("name"): step for step in full_job["steps"]}
    acceptance = full_steps["Complete backend release acceptance"]
    assert acceptance["shell"] == "bash -eo pipefail {0}"
    assert acceptance["run"] == (
        "uv run pytest -o addopts= --timeout=300 --timeout-method=thread "
        "-rfE --durations=50 --junitxml=full-backend-junit.xml | tee full-backend-pytest.log"
    )
    for job in jobs.values():
        for step in job["steps"]:
            script = step.get("run") or ""
            if "| tee" not in script:
                continue
            assert "pipefail" in (step.get("shell") or ""), step.get("name")
    for name in (
        "Install dependencies",
        "Ruff check",
        "Ruff format",
        "Focused backend development tests",
    ):
        assert steps[name]["if"] == "steps.paths.outputs.backend == 'true'"
    assert "run_backend_focused.py" in steps["Focused backend development tests"]["run"]
    upload = full_steps["Upload full backend pytest artifacts"]
    assert upload["if"] == "always()"
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"]["name"] == "full-backend-pytest"
    assert "backend/full-backend-junit.xml" in upload["with"]["path"]
    assert "backend/full-backend-pytest.log" in upload["with"]["path"]
    assert int(backend["timeout-minutes"]) <= 30
    assert int(full_job["timeout-minutes"]) <= 120
    assert jobs["evaluation"]["needs"] == "backend"
    for name, output in (
        ("deployment-safety", "deployment"),
        ("frontend", "frontend"),
        ("evaluation", "evaluation"),
        ("docker-build", "docker"),
    ):
        assert f"needs.backend.outputs.{output}" in jobs[name]["if"]
    smoke = jobs["e2e-smoke"]
    assert smoke["if"] == "github.event_name == 'workflow_dispatch' && inputs.e2e_smoke"
    assert smoke["continue-on-error"] == "true"
    for name, job in jobs.items():
        assert int(job["timeout-minutes"]) > 0
        if name != "e2e-smoke":
            assert "continue-on-error" not in job
        assert all("continue-on-error" not in step for step in job["steps"])


def test_ci_scope_self_test_ignores_polluted_environment(tmp_path: Path) -> None:
    script = ROOT / ".github/workflows/ci-scope.sh"
    output = tmp_path / "github_output"
    summary = tmp_path / "github_summary"
    output.write_text("")
    summary.write_text("")
    env = os.environ.copy()
    env.update(
        {
            "CI_SCOPE_EVENT_NAME": "pull_request",
            "CI_SCOPE_PR_ACTION": "labeled",
            "CI_SCOPE_PR_DRAFT": "true",
            "CI_SCOPE_PR_LABEL": "run-ci",
            "CI_SCOPE_PR_LABELS": "run-ci",
            "CI_SCOPE_BASE_SHA": "0" * 40,
            "CI_SCOPE_HEAD_SHA": "a" * 40,
            "CI_SCOPE_PR_NUMBER": "9",
            "CI_SCOPE_BASE_CHANGED": "true",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_ENV": str(tmp_path / "github_env"),
            "GITHUB_PATH": str(tmp_path / "github_path"),
            "GITHUB_WORKSPACE": str(tmp_path),
            "GIT_DIR": str(tmp_path / "not-a-git-dir"),
            "GIT_WORK_TREE": str(tmp_path),
        }
    )
    result = subprocess.run(
        ["bash", str(script), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "ok draft-code" in result.stdout
    assert "ok draft-lookalike-label" in result.stdout
    assert "ci-scope self-test passed" in result.stdout
    assert output.read_text() == ""
    assert summary.read_text() == ""
