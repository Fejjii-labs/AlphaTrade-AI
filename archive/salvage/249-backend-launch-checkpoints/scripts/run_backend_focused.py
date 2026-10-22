#!/usr/bin/env python3
"""Development selection only; full release acceptance is a separate CI dispatch.

A changed application module selects ``tests/test_<stem>.py`` when that exact
file exists, plus explicit path mappings. Filename prefixes such as
``test_agent*.py`` are not used: in this tree the stems ``agent`` and
``strategy`` match 26 and 19 test files.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE_TESTS = (
    "tests/test_config.py",
    "tests/test_deployment_safety.py",
    "tests/test_backend_ci_scope.py",
)
SFP_TESTS = (
    "tests/test_sfp_receipt_reuse.py",
    "tests/test_sfp_strategy_brain_runtime.py",
    "tests/test_sfp_detector.py",
    "tests/test_live_evidence_pipeline.py",
    "tests/test_frontier_remediation.py::test_telegram_token_cannot_appear_in_logs_exceptions_or_health",
)
SFP_PATHS = (
    "backend/src/app/strategy_brain/sfp",
    "backend/src/app/strategy_brain/assembly.py",
    "backend/src/app/evidence_pipeline/watcher_port.py",
    "backend/src/app/persistence/public_market_observations.py",
    "backend/tests/test_sfp",
)


REVIEWER_BOUNDARY_PATHS = (
    "backend/src/app/agents/",
    "backend/src/app/interactive_agent/",
    "backend/src/app/services/rag_service",
    "backend/src/app/rag/",
    "backend/src/app/workers/knowledge_indexing",
    "backend/src/app/services/turn_",
    "backend/src/app/services/chat_turn",
    "backend/src/app/services/quota_",
    "backend/src/app/repositories/usage",
    "frontend/scripts/generate-api",
    "frontend/src/lib/api/",
    "frontend/src/components/agent/",
)
REVIEWER_BOUNDARY_TESTS = (
    "tests/test_agent_vector_retrieval.py",
    "tests/test_reviewer_integration_postgres.py",
    "tests/test_knowledge_indexing.py",
    "tests/test_turn_coordinator_postgres.py",
    "tests/test_shared_turn_policy.py",
    "tests/test_usage_quota.py",
    "tests/test_strategy_conversation_postgres.py",
)

ACTIVITY_BOUNDARY_PATHS = (
    "backend/src/app/core/blofin_readonly_access.py",
    "backend/src/app/api/routes/blofin_activity.py",
    "backend/src/app/db/blofin_activity.py",
    "backend/src/app/db/migrations/versions/a10blofin",
    "backend/src/app/providers/exchange/blofin_activity.py",
    "backend/src/app/repositories/blofin_activity.py",
    "backend/src/app/schemas/blofin_activity.py",
    "backend/src/app/services/blofin_activity",
    "backend/src/app/workers/blofin_activity.py",
    "frontend/src/components/activity/",
    "frontend/src/lib/api/",
)
ACTIVITY_BOUNDARY_TESTS = (
    "tests/test_blofin_activity_provider.py",
    "tests/test_blofin_activity_postgres.py",
    "tests/test_blofin_activity_migration.py",
    "tests/test_blofin_provider.py",
    "tests/test_dashboard_demo_account.py",
)
# Exact source paths only. A directory prefix or a longer filename must not match.
EXPLICIT_DEPENDENCIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "scripts/backend_checkpoints.py",
        ("tests/test_backend_checkpoints.py",),
    ),
    (
        "scripts/run_backend_launch.py",
        (
            "tests/test_backend_launch.py",
            "tests/test_backend_release_diagnostics.py",
            "tests/test_backend_checkpoints.py",
        ),
    ),
    (
        "scripts/run_backend_release.py",
        (
            "tests/test_backend_launch.py",
            "tests/test_backend_release_diagnostics.py",
            "tests/test_backend_checkpoints.py",
        ),
    ),
    (
        "backend/src/app/core/config.py",
        ("tests/test_config.py", "tests/test_config_jwt.py"),
    ),
    (
        "backend/src/app/core/deployment_safety.py",
        ("tests/test_deployment_safety.py",),
    ),
    (
        "backend/src/app/core/exchange_safety.py",
        ("tests/test_exchange_safety.py",),
    ),
    (
        "backend/src/app/core/paper_safety.py",
        ("tests/test_phase1a_paper_mode_invariant.py",),
    ),
)


def _exact_module_test(path: str, backend: Path) -> str | None:
    """Return ``tests/test_<stem>.py`` for one application module, if it exists."""
    if not path.startswith("backend/src/app/") or not path.endswith(".py"):
        return None
    candidate = Path(path.removeprefix("backend/"))
    exact = backend / "tests" / f"test_{candidate.stem}.py"
    if exact.is_file():
        return exact.relative_to(backend).as_posix()
    return None


def select_tests(changed: list[str], backend: Path) -> list[str]:
    selected = set(BASE_TESTS)
    explicit = dict(EXPLICIT_DEPENDENCIES)
    for path in changed:
        if path.startswith(SFP_PATHS):
            selected.update(SFP_TESTS)
        if path.startswith(REVIEWER_BOUNDARY_PATHS):
            selected.update(REVIEWER_BOUNDARY_TESTS)
        if path.startswith(ACTIVITY_BOUNDARY_PATHS):
            selected.update(ACTIVITY_BOUNDARY_TESTS)
        if mapped := explicit.get(path):
            selected.update(mapped)
        candidate = Path(path.removeprefix("backend/"))
        if path.startswith("backend/tests/") and candidate.name.startswith("test_"):
            selected.add(candidate.as_posix())
        if exact := _exact_module_test(path, backend):
            selected.add(exact)
    tests = (backend / "tests").resolve()
    return sorted(
        node
        for node in selected
        if (file := backend / node.split("::", 1)[0]).suffix == ".py"
        and file.is_file()
        and file.resolve().is_relative_to(tests)
    )


def changed_paths(root: Path, base: str, head: str) -> list[str]:
    if not re.fullmatch(r"[0-9a-f]{40}", head) or (
        base and not re.fullmatch(r"[0-9a-f]{40}", base)
    ):
        raise ValueError("CI base/head must be commit SHAs.")
    if not base and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        # A manual combined check must cover the branch, even when its last commit
        # only records evidence. Checkout fetch-depth: 0 supplies origin/main.
        base = subprocess.check_output(
            ["git", "merge-base", "origin/main", head], cwd=root, text=True
        ).strip()
        if not re.fullmatch(r"[0-9a-f]{40}", base):
            raise ValueError("Manual comparison base must be a commit SHA.")
    if base and base != "0" * 40:
        command = [
            "git",
            "diff",
            "--name-only",
            "--diff-filter=ACDMR",
            base,
            head,
            "--",
        ]
    else:
        command = [
            "git",
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            "-m",
            head,
        ]
    return subprocess.check_output(command, cwd=root, text=True).splitlines()


def batch_selection(selection: list[str], index: int, count: int) -> list[str]:
    """Partition whole files; overlapping file/node arguments stay together."""
    if count < 1 or not 0 <= index < count:
        raise ValueError("Batch index must be within a positive batch count.")
    files = sorted({node.split("::", 1)[0] for node in selection})
    owners = {file: position % count for position, file in enumerate(files)}
    return [node for node in selection if owners[node.split("::", 1)[0]] == index]


def run_batch(command: list[str], output: Path) -> int:
    # Reuse the existing diagnostic supervisor; the workflow bounds uv startup
    # and this runner separately with the existing outer launcher.
    from run_backend_release import run

    previous = Path.cwd()
    try:
        os.chdir(Path(__file__).resolve().parents[1] / "backend")
        return run(command, output, phase_seconds=180, run_seconds=960)
    finally:
        os.chdir(previous)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", required=True)
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--batch-index", type=int, default=0)
    parser.add_argument("--batch-count", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.batch_count < 1 or not 0 <= args.batch_index < args.batch_count:
        parser.error("Batch index must be within a positive batch count.")
    root = Path(__file__).resolve().parents[1]
    selection = select_tests(changed_paths(root, args.base, args.head), root / "backend")
    if not selection:
        raise ValueError("Focused selection must not be empty.")
    selected = batch_selection(selection, args.batch_index, args.batch_count)
    if not selected:
        raise ValueError("Focused batch must not be empty.")
    message = "Focused backend development checks; full backend release acceptance is pending."
    print(message, flush=True)
    print("\n".join(selected), flush=True)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as output:
            output.write(
                f"{message}\n\nBatch {args.batch_index + 1}/{args.batch_count}: "
                f"{len(selected)} of {len(selection)} selected test files/nodes.\n"
            )
    receipt = {
        "base": args.base,
        "head": args.head,
        "batch_index": args.batch_index,
        "batch_count": args.batch_count,
        "selection": selected,
        "full_selection": selection,
    }
    if args.output:
        args.output = args.output.resolve()
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "selection.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if args.list_only:
        return 0
    started = time.monotonic()
    command = ["uv", "run", "pytest", *selected]
    if args.output:
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.support.release_diagnostics",
            "-vv",
            "-o",
            "faulthandler_timeout=60",
            *selected,
            f"--junitxml={(args.output / 'junit.xml').resolve()}",
        ]
        code = run_batch(command, args.output)
    else:
        code = subprocess.run(command, cwd=root / "backend", check=False).returncode
    if args.output:
        receipt.update(exitcode=code, seconds=time.monotonic() - started)
        (args.output / "selection.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
