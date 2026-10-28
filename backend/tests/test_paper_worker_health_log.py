"""paper_worker_health is logged at startup, periodically, and at shutdown."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from structlog.testing import capture_logs

from app.workers.paper_worker import CycleOutcome, PaperWorkerSupervisor

ROOT = Path(__file__).resolve().parents[2]

_SCRIPT = """
import sys
import structlog
structlog.configure(
    processors=[structlog.processors.add_log_level, structlog.processors.JSONRenderer()],
    logger_factory=structlog.PrintLoggerFactory(sys.stdout),
)
from app.workers.paper_worker import CycleOutcome, PaperWorkerSupervisor

supervisor = PaperWorkerSupervisor(
    watcher_cycle=lambda: CycleOutcome(status="running", record_scan=True),
    telegram_cycle=lambda: CycleOutcome(status="running"),
    poll_seconds=0.05,
    join_timeout_seconds=2,
    health_log_interval_seconds=0.2,
)
supervisor.serve_until_signal(ready=lambda: print("SERVING", flush=True))
"""


def _health_lines(stdout: str) -> list[dict[str, object]]:
    lines = []
    for raw in stdout.splitlines():
        if not raw.startswith("{"):
            continue
        record = json.loads(raw)
        if record.get("event") == "paper_worker_health":
            lines.append(record)
    return lines


def test_threaded_worker_logs_health_at_startup_while_running_and_at_shutdown() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", _SCRIPT],
        cwd="/tmp",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(ROOT / "backend" / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "RENDER_INSTANCE_ID": "srv-test-abc12",
        },
    )
    assert process.stdout is not None
    ready, _, _ = select.select([process.stdout], [], [], 20)
    assert ready, "paper worker did not report that it was serving"
    time.sleep(1.0)
    process.send_signal(signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, stderr

    health = _health_lines(stdout)
    phases = [line["phase"] for line in health]
    assert phases.count("startup") == 1
    assert phases[0] == "startup"
    assert phases[-1] == "shutdown"
    assert phases.count("shutdown") == 1

    running = [line for line in health if line["phase"] == "running"]
    assert running, phases
    assert all(line["watcher_status"] != "stopped" for line in running)
    assert any(line["watcher_status"] == "running" for line in running)
    assert all(line["stopping"] is False for line in running)

    shutdown = health[-1]
    assert shutdown["watcher_status"] == "stopped"
    assert shutdown["telegram_status"] == "stopped"
    assert shutdown["stopping"] is True

    worker_ids = {line["worker_id"] for line in health}
    assert len(worker_ids) == 1
    assert next(iter(worker_ids))
    assert {line["render_instance_id"] for line in health} == {"srv-test-abc12"}


class _Monotonic:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_periodic_health_waits_for_the_interval() -> None:
    clock = _Monotonic()
    supervisor = PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome(status="running"),
        telegram_cycle=lambda: CycleOutcome(status="running"),
        poll_seconds=0.0,
        health_log_interval_seconds=60.0,
        monotonic=clock,
    )
    # Not serving yet: nothing is due.
    assert supervisor.log_health_if_due() is False
    supervisor._next_health_log_at = clock.now + 60.0
    with capture_logs() as logs:
        clock.now += 59.0
        assert supervisor.log_health_if_due() is False
        clock.now += 1.0
        assert supervisor.log_health_if_due() is True
        clock.now += 30.0
        assert supervisor.log_health_if_due() is False
        clock.now += 30.0
        assert supervisor.log_health_if_due() is True
        supervisor.request_stop()
        clock.now += 120.0
        assert supervisor.log_health_if_due() is False
    health = [entry for entry in logs if entry["event"] == "paper_worker_health"]
    assert [entry["phase"] for entry in health] == ["running", "running"]
    assert all(entry["worker_id"] == supervisor.worker_id for entry in health)


def test_worker_ids_differ_per_supervisor() -> None:
    first = PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome(status="idle"),
        telegram_cycle=lambda: CycleOutcome(status="idle"),
    )
    second = PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome(status="idle"),
        telegram_cycle=lambda: CycleOutcome(status="idle"),
    )
    assert first.worker_id != second.worker_id
    assert str(os.getpid()) in first.worker_id
