#!/usr/bin/env python3
"""Run the unfiltered backend gate with bounded, retained failure diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path


def postgres_snapshot() -> dict[str, object]:
    """Capture fixture contention without URLs, SQL literals or provider data."""
    import psycopg

    url = os.environ.get("PHASE1_POSTGRES_URL", "")
    if not url:
        return {"available": False, "reason": "test_database_not_configured"}
    try:
        with psycopg.connect(
            url.replace("postgresql+psycopg://", "postgresql://", 1),
            autocommit=True,
            connect_timeout=3,
            options="-c statement_timeout=3000",
        ) as conn:
            rows = conn.execute(
                "SELECT pid,state,wait_event_type,wait_event,pg_blocking_pids(pid),"
                "extract(epoch from now()-xact_start),extract(epoch from now()-query_start),"
                "CASE WHEN query ~ '^DROP SCHEMA' THEN 'DROP SCHEMA' "
                "WHEN query ~ '^CREATE' THEN 'CREATE' WHEN query ~ '^SELECT' THEN 'SELECT' "
                "WHEN query ~ '^ROLLBACK' THEN 'ROLLBACK' ELSE 'other' END "
                "FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()"
            ).fetchall()
        return {"available": True, "rows": rows}
    except Exception as error:
        return {"available": False, "error_type": type(error).__name__}


def _save(root: Path, name: str, value: object) -> None:
    temporary = root / f"{name}.tmp"
    temporary.write_text(json.dumps(value, default=str, indent=2) + "\n")
    temporary.replace(root / name)


class _SnapshotReader:
    """A stalled diagnostic connection must never stall the watchdog itself."""

    def __init__(self) -> None:
        self.latest: dict[str, object] = {
            "available": False,
            "reason": "diagnostic_pending",
        }
        self.thread: threading.Thread | None = None
        self.completed_at: float | None = None

    def request(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return

        def sample() -> None:
            try:
                self.latest = postgres_snapshot()
            except Exception as error:
                self.latest = {"available": False, "error_type": type(error).__name__}
            self.completed_at = time.time()

        self.thread = threading.Thread(target=sample, daemon=True)
        self.thread.start()


def _active_phase(root: Path) -> dict[str, object]:
    try:
        value = json.loads((root / "active.json").read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _stop(proc: subprocess.Popen, *, owns_group: bool = True) -> None:
    try:
        os.kill(proc.pid, signal.SIGUSR1)
        time.sleep(0.2)
        if owns_group:
            os.killpg(proc.pid, signal.SIGINT)
        else:
            proc.send_signal(signal.SIGINT)
    except ProcessLookupError:
        pass
    try:
        # Finish before the runner escalates cancellation to an uncatchable kill.
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # The leader may exit while one of its children ignores SIGINT.
        with suppress(ProcessLookupError):
            if owns_group:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        proc.wait(timeout=2)


def run(command: list[str], root: Path, *, phase_seconds: float, run_seconds: float) -> int:
    root.mkdir(parents=True, exist_ok=True)
    env = dict(
        os.environ,
        BACKEND_RELEASE_DIAGNOSTICS=str(root.resolve()),
        PYTHONUNBUFFERED="1",
    )
    started = time.monotonic()
    owns_group = os.environ.get("BACKEND_RELEASE_SHARED_GROUP") != "1"
    proc = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=owns_group,
    )

    def stream() -> None:
        assert proc.stdout is not None
        with (root / "pytest.log").open("w") as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)

    output = threading.Thread(target=stream, daemon=True)
    output.start()
    snapshots = _SnapshotReader()
    snapshots.request()
    last_snapshot = 0.0
    reason = None
    received_signal = 0
    previous_handlers = {}

    def cancelled(signum, _frame) -> None:
        nonlocal received_signal
        received_signal = received_signal or signum

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, cancelled)
    try:
        while proc.poll() is None:
            time.sleep(0.2)
            active = _active_phase(root)
            phase_start = active.get("started")
            elapsed = time.time() - phase_start if isinstance(phase_start, (int, float)) else 0
            expired = elapsed >= phase_seconds or time.monotonic() - started >= run_seconds
            stopping = expired or received_signal
            if stopping or time.monotonic() - last_snapshot >= 10:
                last_snapshot = time.monotonic()
                snapshot = {
                    "time": time.time(),
                    "active": active,
                    "postgres": snapshots.latest,
                    "postgres_sampled_at": snapshots.completed_at,
                }
                with (root / "postgres.jsonl").open("a") as handle:
                    handle.write(json.dumps(snapshot, default=str) + "\n")
                if not stopping:
                    snapshots.request()
            if stopping:
                reason = (
                    f"signal_{signal.Signals(received_signal).name}"
                    if received_signal
                    else "phase_deadline"
                    if elapsed >= phase_seconds
                    else "run_deadline"
                )
                _save(root, "before-cancellation.json", {"reason": reason, **snapshot})
                _stop(proc, owns_group=owns_group)
                break
    finally:
        if proc.poll() is None:
            _stop(proc, owns_group=owns_group)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    output.join(timeout=3)
    code = 128 + received_signal if received_signal else 124 if reason else proc.returncode
    _save(
        root,
        "result.json",
        {
            "exitcode": code,
            "reason": reason,
            "seconds": time.monotonic() - started,
            "diagnostics_saved_before_cancellation": bool(reason),
        },
    )
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("../backend-ci-diagnostics"))
    parser.add_argument("--phase-seconds", type=float, default=180)
    parser.add_argument("--run-seconds", type=float, default=6900)
    args = parser.parse_args()
    if args.phase_seconds <= 0 or args.run_seconds <= 0:
        parser.error("deadlines must be positive")
    return run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.support.release_diagnostics",
            "-x",
            "-vv",
            "-o",
            "faulthandler_timeout=60",
            "--durations=20",
            "--tb=short",
            f"--junitxml={args.output.resolve() / 'junit.xml'}",
        ],
        args.output,
        phase_seconds=args.phase_seconds,
        run_seconds=args.run_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
