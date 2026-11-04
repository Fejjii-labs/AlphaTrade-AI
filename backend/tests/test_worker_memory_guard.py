"""Worker memory guard: allocator arenas and the soft RSS watchdog."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
import yaml

from app.core.config import Settings
from app.observability.memory_watchdog import MEMORY_WATCHDOG_EXIT_CODE, MemoryWatchdog
from app.workers.paper_worker import (
    CycleOutcome,
    PaperWorkerSupervisor,
    build_paper_worker_supervisor,
    exit_if_memory_watchdog_tripped,
)

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_image_caps_glibc_arenas() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile.split("AS runtime", 1)[1]
    assert "MALLOC_ARENA_MAX=2" in runtime


def test_staging_paper_worker_blueprint_caps_glibc_arenas() -> None:
    document = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    worker = next(
        item for item in document["services"] if item["name"] == "alphatrade-paper-worker-staging"
    )
    env = {item["key"]: item.get("value") for item in worker["envVars"]}
    assert env["MALLOC_ARENA_MAX"] == "2"


def _watchdog(rss: list[int], clock: list[float], **kwargs: object) -> MemoryWatchdog:
    return MemoryWatchdog(
        limit_bytes=int(kwargs.get("limit_bytes", 440 * 1024 * 1024)),  # type: ignore[arg-type]
        check_seconds=float(kwargs.get("check_seconds", 30.0)),  # type: ignore[arg-type]
        read_rss=lambda: rss[0],
        monotonic=lambda: clock[0],
    )


def test_watchdog_does_not_trip_below_the_limit() -> None:
    rss, clock = [439 * 1024 * 1024], [0.0]
    watchdog = _watchdog(rss, clock)
    assert watchdog.check() is False
    clock[0] = 31.0
    assert watchdog.check() is False
    assert watchdog.tripped is False


def test_watchdog_trips_at_the_limit_on_the_next_due_check() -> None:
    rss, clock = [100 * 1024 * 1024], [0.0]
    watchdog = _watchdog(rss, clock)
    assert watchdog.check() is False
    rss[0] = 440 * 1024 * 1024
    clock[0] = 10.0
    assert watchdog.check() is False  # not due yet: 30s interval
    assert watchdog.seconds_until_check() == 20.0
    clock[0] = 30.0
    assert watchdog.check() is True
    assert watchdog.tripped is True


def test_watchdog_read_failure_never_trips() -> None:
    def broken() -> int:
        raise OSError("no /proc")

    watchdog = MemoryWatchdog(limit_bytes=1, check_seconds=1, read_rss=broken)
    assert watchdog.check() is False


def test_watchdog_settings_defaults_and_disable() -> None:
    settings = Settings(_env_file=None)
    assert settings.paper_worker_memory_watchdog_enabled is True
    assert settings.paper_worker_memory_watchdog_limit_mb == 440
    assert settings.paper_worker_memory_watchdog_check_seconds == 30.0
    off = Settings(_env_file=None, paper_worker_memory_watchdog_enabled=False)
    assert build_paper_worker_supervisor(off)._memory_watchdog is None
    on = build_paper_worker_supervisor(settings)._memory_watchdog
    assert on is not None and on.limit_bytes == 440 * 1024 * 1024


def _supervisor() -> PaperWorkerSupervisor:
    return PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome(status="running"),
        telegram_cycle=lambda: CycleOutcome(status="running"),
        poll_seconds=0.05,
        join_timeout_seconds=2,
    )


def test_tripped_watchdog_stops_the_supervisor_cleanly_and_exits_nonzero() -> None:
    supervisor = _supervisor()
    rss = [10]
    supervisor.attach_memory_watchdog(
        MemoryWatchdog(limit_bytes=1000, check_seconds=1.0, read_rss=lambda: rss[0])
    )
    # The first check is below the limit; the next due check trips it.
    threading.Timer(0.2, lambda: rss.__setitem__(0, 2000)).start()
    # Safety stop: if the watchdog never stops the loop, end it so the test fails.
    safety = threading.Timer(5.0, lambda: setattr(supervisor, "_signaled", True))
    safety.start()
    done = threading.Event()
    started = time.monotonic()
    try:
        supervisor.serve_until_signal(ready=done.set)
    finally:
        safety.cancel()
    assert done.is_set()
    # Stopped by the watchdog (about 1s check interval), not by the safety stop.
    assert time.monotonic() - started < 4.0
    health = supervisor.snapshot()
    assert health.stopping is True
    assert health.watcher.status == "stopped" and health.telegram.status == "stopped"
    assert supervisor.memory_watchdog_tripped is True
    with pytest.raises(SystemExit) as raised:
        exit_if_memory_watchdog_tripped(supervisor)
    assert raised.value.code == MEMORY_WATCHDOG_EXIT_CODE != 0


def test_untripped_or_absent_watchdog_does_not_exit() -> None:
    supervisor = _supervisor()
    exit_if_memory_watchdog_tripped(supervisor)
    supervisor.attach_memory_watchdog(
        MemoryWatchdog(limit_bytes=1 << 40, check_seconds=1.0, read_rss=lambda: 10)
    )
    assert supervisor._memory_watchdog_tripped_now() is False
    exit_if_memory_watchdog_tripped(supervisor)
