#!/usr/bin/env python3
"""Relevant development tests; explicit combined validation runs the full frontend suite."""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "backend_scope", ROOT / "scripts/run_backend_focused.py"
)
assert SPEC is not None and SPEC.loader is not None
scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scope)

BASE_TESTS = {"src/lib/api/generated-contracts.test.ts", "src/lib/auth/session.test.ts"}
BOUNDARIES = {
    "frontend/src/lib/api/": ("src/lib/api/", "src/components/agent/", "src/components/activity/"),
    "frontend/src/lib/auth/": (
        "src/lib/auth/",
        "src/lib/private-query",
        "src/components/activity/",
    ),
    "frontend/src/components/agent/": ("src/components/agent/", "src/lib/voice/"),
    "frontend/src/lib/voice/": ("src/lib/voice/", "src/components/agent/"),
    "frontend/src/components/dashboard/": ("src/components/dashboard/", "src/components/activity/"),
    "frontend/src/components/journal/": (
        "src/components/journal/",
        "src/app/(app)/journal/",
        "src/components/activity/",
    ),
    "frontend/src/app/(app)/agent/": ("src/app/(app)/agent/", "src/components/agent/"),
    "frontend/src/app/(app)/journal/": (
        "src/app/(app)/journal/",
        "src/components/journal/",
        "src/components/activity/",
    ),
}


def select_tests(changed: list[str], frontend: Path) -> list[str]:
    tests = {
        p.relative_to(frontend).as_posix()
        for p in (frontend / "src").rglob("*.test.*")
        if p.suffix in {".ts", ".tsx"}
    }
    selected = BASE_TESTS & tests
    for path in changed:
        if not path.startswith("frontend/src/"):
            continue
        relative = path.removeprefix("frontend/")
        if relative in tests:
            selected.add(relative)
        source = Path(relative)
        selected.update(
            test
            for test in tests
            if Path(test).parent == source.parent
            or (
                source.stem not in {"page", "index", "types", "layout"}
                and Path(test).name.startswith(source.stem + ".test.")
            )
        )
        for boundary, prefixes in BOUNDARIES.items():
            if path.startswith(boundary):
                selected.update(test for test in tests if test.startswith(prefixes))
    return sorted(selected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", required=True)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()
    selection = select_tests(scope.changed_paths(ROOT, args.base, args.head), ROOT / "frontend")
    if not selection:
        raise ValueError("Focused selection must not be empty.")
    message = (
        "Focused frontend development checks; combined validation "
        "and full release acceptance remain pending."
    )
    print(message, flush=True)
    print("\n".join(selection), flush=True)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as output:
            output.write(f"{message}\n\nSelected {len(selection)} test files.\n")
    if args.list_only:
        return 0
    return subprocess.run(
        ["npm", "exec", "--", "vitest", "run", *selection], cwd=ROOT / "frontend", check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
