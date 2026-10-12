"""Development scope cannot replace the explicit complete release acceptance gate."""

import importlib.util
import re
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
def test_commit_diff_uses_fixed_arguments_and_includes_deleted_or_merge_changes(base, monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
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


def test_manual_development_selection_covers_branch_changes_not_only_last_evidence_commit(
    monkeypatch,
):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    with patch.object(
        scope.subprocess,
        "check_output",
        side_effect=["b" * 40 + "\n", "frontend/src/components/activity/NativeActivity.tsx\n"],
    ) as git:
        assert scope.changed_paths(ROOT, "", "a" * 40) == [
            "frontend/src/components/activity/NativeActivity.tsx"
        ]
    assert git.call_args_list[0].args[0] == ["git", "merge-base", "origin/main", "a" * 40]
    assert git.call_args_list[1].args[0][4:6] == ["b" * 40, "a" * 40]


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


def test_workflow_keeps_quality_checks_and_explicit_combined_release_gates():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    trigger = workflow["on"]
    assert "pull_request" in trigger and "push" in trigger
    full = trigger["workflow_dispatch"]["inputs"]["full_backend"]
    assert full == {
        "description": "Run the complete backend suite for final release acceptance",
        "type": "boolean",
        "required": "true",
        "default": "false",
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
    assert steps["Ruff check"]["run"] == (
        "uv run ruff check . ../scripts/run_backend_focused.py ../scripts/run_frontend_focused.py"
    )
    assert steps["Ruff format"]["run"] == (
        "uv run ruff format --check . ../scripts/run_backend_focused.py "
        "../scripts/run_frontend_focused.py"
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
    assert trigger["workflow_dispatch"]["inputs"]["combined_validation"]["default"] == "false"
    assert workflow["permissions"] == {"contents": "read"}
    assert (
        workflow["concurrency"]["cancel-in-progress"]
        == "${{ github.event_name == 'pull_request' }}"
    )
    assert "github.event.pull_request.number || github.ref" in workflow["concurrency"]["group"]
    assert (
        "github.event_name == 'workflow_dispatch' && github.run_id"
        in workflow["concurrency"]["group"]
    )
    for name, job in jobs.items():
        assert "continue-on-error" not in job
        if name != "backend":
            assert int(job["timeout-minutes"]) <= 20
        if name in {"backend", "frontend", "deployment-safety"}:
            assert "if" not in job
        else:
            assert (
                job["if"] == "github.event_name == 'workflow_dispatch' && "
                "(inputs.combined_validation || inputs.full_backend)"
            )
        assert all("continue-on-error" not in step for step in job["steps"])


def evaluate_event_value(expression, event, combined, full):
    expression = expression.replace("${{", "").replace("}}", "")
    expression = expression.replace("github.event_name", repr(event))
    expression = expression.replace("inputs.combined_validation", str(combined))
    expression = expression.replace("inputs.full_backend", str(full))
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", "not ", expression)
    return eval(expression.strip(), {"__builtins__": {}}, {})


def evaluate_event_condition(expression, event, combined, full):
    return bool(evaluate_event_value(expression, event, combined, full))


@pytest.mark.parametrize("event", ["pull_request", "push", "workflow_dispatch"])
@pytest.mark.parametrize(
    "combined,full", [(False, False), (True, False), (False, True), (True, True)]
)
def test_actual_event_conditions_never_start_expensive_or_full_checks_implicitly(
    event, combined, full
):
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]
    is_combined = event == "workflow_dispatch" and (combined or full)
    is_full = event == "workflow_dispatch" and full
    assert evaluate_event_value(jobs["backend"]["timeout-minutes"], event, combined, full) == (
        120 if is_full else 20
    )
    for name in ["evaluation", "docker-build", "e2e-smoke"]:
        assert evaluate_event_condition(jobs[name]["if"], event, combined, full) == is_combined
    backend = {step.get("name"): step for step in jobs["backend"]["steps"]}
    assert (
        evaluate_event_condition(
            backend["Complete backend release acceptance"]["if"], event, combined, full
        )
        == is_full
    )
    assert evaluate_event_condition(
        backend["Focused backend development tests"]["if"], event, combined, full
    ) == (not is_full)
    frontend = {step.get("name"): step for step in jobs["frontend"]["steps"]}
    assert evaluate_event_condition(
        frontend["Focused frontend development tests"]["if"], event, combined, full
    ) == (not is_combined)
    for name in [
        "Complete frontend unit tests",
        "Build",
        "Share the verified production browser build",
    ]:
        assert evaluate_event_condition(frontend[name]["if"], event, combined, full) == is_combined
    assert evaluate_event_condition(
        workflow["concurrency"]["cancel-in-progress"], event, combined, full
    ) == (event == "pull_request")


def test_frontend_selection_covers_changes_and_account_contract_boundaries():
    spec = importlib.util.spec_from_file_location(
        "frontend_scope", ROOT / "scripts/run_frontend_focused.py"
    )
    assert spec is not None and spec.loader is not None
    frontend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frontend)
    selected = frontend.select_tests(
        ["frontend/src/lib/api/generated/client.ts"], ROOT / "frontend"
    )
    assert "src/lib/api/generated-contracts.test.ts" in selected
    assert "src/components/agent/AgentWorkspace.test.tsx" in selected
    assert set(frontend.BASE_TESTS).issubset(selected)
    selected = frontend.select_tests(["frontend/src/lib/auth/session.ts"], ROOT / "frontend")
    assert "src/lib/auth/session.test.ts" in selected
    assert "src/lib/auth/session.test.ts" in selected
    assert len(frontend.select_tests(["docs/README.md"], ROOT / "frontend")) == 2
    selected = frontend.select_tests(["frontend/src/app/(app)/journal/page.tsx"], ROOT / "frontend")
    assert "src/app/(app)/journal/page.switch.test.tsx" in selected
    assert "src/app/(app)/watcher/page.test.tsx" not in selected


def test_native_activity_scope_and_postgres_checks_are_not_silently_omitted():
    selected = scope.select_tests(
        ["frontend/src/components/activity/NativeActivity.tsx"], ROOT / "backend"
    )
    assert "tests/test_blofin_provider.py" in selected
    assert "tests/test_dashboard_demo_account.py" in selected
    for node in scope.ACTIVITY_BOUNDARY_TESTS:
        if (ROOT / "backend" / node).exists():
            assert node in selected
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["jobs"]["backend"]["env"]["BLOFIN_ACTIVITY_TEST_POSTGRES_URL"] == (
        "postgresql+psycopg://alphatrade:alphatrade@localhost:5432/alphatrade_test"
    )


@pytest.mark.parametrize(
    "path",
    [
        "backend/src/app/rag/indexing.py",
        "frontend/src/lib/api/validated-fetch.ts",
        "frontend/src/components/agent/AgentWorkspace.tsx",
    ],
)
def test_combined_reviewer_changes_select_cross_module_postgres_boundaries(path):
    selected = scope.select_tests([path], ROOT / "backend")
    assert set(scope.REVIEWER_BOUNDARY_TESTS).issubset(selected)
    workflow = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
    assert "PHASE1_POSTGRES_URL" in workflow["jobs"]["backend"]["env"]
