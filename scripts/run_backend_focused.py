#!/usr/bin/env python3
"""Development selection only; full release acceptance is a separate CI dispatch."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
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


def select_tests(changed: list[str], backend: Path) -> list[str]:
    selected = set(BASE_TESTS)
    for path in changed:
        if path.startswith(SFP_PATHS):
            selected.update(SFP_TESTS)
        candidate = Path(path.removeprefix("backend/"))
        if path.startswith("backend/tests/") and candidate.name.startswith("test_"):
            selected.add(candidate.as_posix())
        if path.startswith("backend/src/app/") and path.endswith(".py"):
            selected.update(
                item.relative_to(backend).as_posix()
                for item in (backend / "tests").glob(f"test_{candidate.stem}*.py")
            )
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
    if base and base != "0" * 40:
        command = ["git", "diff", "--name-only", "--diff-filter=ACDMR", base, head, "--"]
    else:
        command = ["git", "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-m", head]
    return subprocess.check_output(command, cwd=root, text=True).splitlines()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", required=True)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    selection = select_tests(changed_paths(root, args.base, args.head), root / "backend")
    if not selection:
        raise ValueError("Focused selection must not be empty.")
    message = "Focused backend development checks; full backend release acceptance is pending."
    print(message, flush=True)
    print("\n".join(selection), flush=True)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as output:
            output.write(f"{message}\n\nSelected {len(selection)} test files/nodes.\n")
    if args.list_only:
        return 0
    return subprocess.run(
        ["uv", "run", "pytest", *selection], cwd=root / "backend", check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
