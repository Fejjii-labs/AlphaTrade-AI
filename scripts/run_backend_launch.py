#!/usr/bin/env python3
"""Bound the whole release command, including uv startup, and retain provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]


def save(root: Path, name: str, value: object) -> None:
    temporary = root / f"{name}.tmp"
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(root / name)


def prepare(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    checkout = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=SOURCE_ROOT, text=True, timeout=5
    ).strip()
    sources = [
        "scripts/run_backend_launch.py",
        "scripts/run_backend_release.py",
        "backend/tests/support/release_diagnostics.py",
        ".github/workflows/ci.yml",
        ".github/workflows/backend-launch-proof.yml",
    ]
    save(
        root,
        "provenance.json",
        {
            "checkout_sha": checkout,
            "python": sys.version,
            "recorded_at": time.time(),
            "github": {
                key: os.environ.get(key)
                for key in (
                    "GITHUB_SHA",
                    "GITHUB_WORKFLOW_REF",
                    "GITHUB_WORKFLOW_SHA",
                    "GITHUB_RUN_ID",
                    "GITHUB_RUN_ATTEMPT",
                    "GITHUB_JOB",
                )
            },
            "sha256": {
                name: hashlib.sha256((SOURCE_ROOT / name).read_bytes()).hexdigest()
                for name in sources
                if (SOURCE_ROOT / name).is_file()
            },
        },
    )
    print(f"Backend launch provenance retained for {checkout}", flush=True)


def stop_group(proc: subprocess.Popen) -> None:
    """The launcher owns the group; the inner supervisor must not detach pytest."""
    with suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        proc.poll()
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    with suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(timeout=2)


def run(command: list[str], root: Path, seconds: float, stage: str) -> int:
    root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    received = 0

    def cancelled(signum, _frame) -> None:
        nonlocal received
        received = received or signum

    previous = {s: signal.signal(s, cancelled) for s in (signal.SIGINT, signal.SIGTERM)}
    proc = None
    code = 127
    reason = "launch_error"
    save(root, f"{stage}-launch.json", {"state": "starting", "budget_seconds": seconds})
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            env=dict(
                os.environ,
                BACKEND_RELEASE_DIAGNOSTICS=str(root.resolve()),
                BACKEND_RELEASE_SHARED_GROUP="1",
                PYTHONUNBUFFERED="1",
            ),
        )
        save(root, f"{stage}-launch.json", {"state": "running", "budget_seconds": seconds})
        while proc.poll() is None and not received and time.monotonic() - started < seconds:
            time.sleep(0.05)
        if received:
            code, reason = 128 + received, signal.Signals(received).name
        elif proc.returncode is None:
            code, reason = 124, "launch_deadline"
        else:
            code, reason = proc.returncode, "command_exit"
        # Save before signaling the group, even if cleanup itself is interrupted.
        save(root, f"{stage}-result.json", {"exitcode": code, "reason": reason})
    except OSError as error:
        save(root, f"{stage}-error.json", {"error_type": type(error).__name__})
    finally:
        if proc is not None:
            stop_group(proc)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        save(
            root,
            f"{stage}-result.json",
            {"exitcode": code, "reason": reason, "seconds": time.monotonic() - started},
        )
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--stage", choices=("collection", "release", "probe"), default="release")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("deadline must be positive")
    prepare(args.output)
    if args.prepare_only:
        return 0
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required")
    return run(command, args.output, args.seconds, args.stage)


if __name__ == "__main__":
    raise SystemExit(main())
