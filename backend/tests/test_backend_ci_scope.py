"""Development scope cannot replace the explicit complete release acceptance gate."""

import importlib.util
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
    assert "pull_request" in trigger and "push" in trigger
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
        "frontend",
        "deployment-safety",
        "evaluation",
        "docker-build",
        "e2e-smoke",
    }
    steps = {step.get("name"): step for step in jobs["backend"]["steps"]}
    assert steps["Ruff check"]["run"] == "uv run ruff check . ../scripts/run_backend_focused.py"
    assert steps["Ruff format"]["run"] == (
        "uv run ruff format --check . ../scripts/run_backend_focused.py"
    )
    assert steps["Complete backend release acceptance"]["run"] == "uv run pytest"
    assert steps["Complete backend release acceptance"]["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.full_backend"
    )
    assert steps["Focused backend development tests"]["if"] == (
        "github.event_name != 'workflow_dispatch' || !inputs.full_backend"
    )
    assert "run_backend_focused.py" in steps["Focused backend development tests"]["run"]
    assert jobs["evaluation"]["needs"] == ["backend"]
    assert jobs["e2e-smoke"]["needs"] == ["backend", "frontend"]
    for job in jobs.values():
        assert "if" not in job and "continue-on-error" not in job
        assert all("continue-on-error" not in step for step in job["steps"])
