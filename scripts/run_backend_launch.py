#!/usr/bin/env python3
"""Bound the whole release command, including uv startup, and retain provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

from backend_checkpoints import CheckpointPublisher

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
        "scripts/backend_checkpoints.py",
        "backend/tests/support/release_diagnostics.py",
        ".github/workflows/ci.yml",
        ".github/workflows/backend-launch-proof.yml",
        ".github/actions/backend-release/entry.mjs",
        ".github/actions/backend-release/upload.mjs",
        ".github/actions/backend-release/package-lock.json",
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


def run(
    command: list[str],
    root: Path,
    seconds: float,
    stage: str,
    *,
    publisher: CheckpointPublisher | None = None,
) -> int:
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
        if publisher is not None:
            # Verify off-runner storage before spending time on the actual suite.
            publisher.tick()
            while (
                publisher.proc is not None and not received and time.monotonic() - started < seconds
            ):
                publisher.tick(start=False)
                time.sleep(0.05)
            if received:
                code, reason = 128 + received, signal.Signals(received).name
                return code
            if time.monotonic() - started >= seconds:
                code, reason = 124, "launch_deadline"
                publisher.close()
                return code
            if publisher.failure:
                code, reason = 74, publisher.failure
                return code
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
        save(
            root,
            f"{stage}-launch.json",
            {"state": "running", "budget_seconds": seconds},
        )
        while proc.poll() is None and not received and time.monotonic() - started < seconds:
            if publisher is not None:
                publisher.tick()
                if publisher.failure:
                    break
            time.sleep(0.05)
        if received:
            code, reason = 128 + received, signal.Signals(received).name
        elif publisher is not None and publisher.failure:
            code, reason = 74, publisher.failure
        elif proc.returncode is None:
            code, reason = 124, "launch_deadline"
        else:
            code, reason = proc.returncode, "command_exit"
        # Save before signaling the group, even if cleanup itself is interrupted.
        save(
            root,
            f"{stage}-result.json",
            {
                "exitcode": code,
                "reason": reason,
                "checkpoint": publisher.state() if publisher is not None else None,
            },
        )
    except OSError as error:
        save(root, f"{stage}-error.json", {"error_type": type(error).__name__})
    finally:
        if proc is not None:
            stop_group(proc)
        if publisher is not None:
            if received:
                publisher.close()
            else:
                publisher.finish()
            if publisher.failure and code == 0:
                code, reason = 74, publisher.failure
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        save(
            root,
            f"{stage}-result.json",
            {
                "exitcode": code,
                "reason": reason,
                "seconds": time.monotonic() - started,
                "checkpoint": publisher.state() if publisher is not None else None,
            },
        )
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--stage", choices=("collection", "release", "probe"), default="release")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--upload-checkpoints", action="store_true")
    parser.add_argument("--checkpoint-seconds", type=float, default=900)
    parser.add_argument("--upload-seconds", type=float, default=30)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("deadline must be positive")
    if args.upload_checkpoints and (
        not math.isfinite(args.checkpoint_seconds)
        or args.checkpoint_seconds <= 0
        or math.ceil(args.seconds / args.checkpoint_seconds) > 7
        or not math.isfinite(args.upload_seconds)
        or not 0 < args.upload_seconds <= 30
    ):
        parser.error("checkpoint schedule must fit seven artifacts and bounded uploads")
    prepare(args.output)
    if args.prepare_only:
        return 0
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required")
    publisher = (
        CheckpointPublisher(
            args.output,
            [
                os.environ.get("BACKEND_CHECKPOINT_NODE", "node"),
                str(SOURCE_ROOT / ".github/actions/backend-release/upload.mjs"),
            ],
            interval=args.checkpoint_seconds,
            upload_seconds=args.upload_seconds,
        )
        if args.upload_checkpoints
        else None
    )
    return run(command, args.output, args.seconds, args.stage, publisher=publisher)


if __name__ == "__main__":
    raise SystemExit(main())
