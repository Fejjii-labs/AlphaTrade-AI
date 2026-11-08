"""Worker memory guard: allocator arenas and the soft RSS watchdog."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
import yaml
from structlog.testing import capture_logs

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


# --- Startup grace and restart backoff (Griller PR #426 blocker 2) ----------

MB = 1024 * 1024


def _graced(
    rss: list[int], clock: list[float], wall: list[float], state: Path | None
) -> MemoryWatchdog:
    return MemoryWatchdog(
        limit_bytes=440 * MB,
        check_seconds=30.0,
        startup_grace_seconds=300.0,
        backoff_max_seconds=3600.0,
        state_path=state,
        read_rss=lambda: rss[0],
        monotonic=lambda: clock[0],
        wall_clock=lambda: wall[0],
    )


def test_worker_starting_above_the_limit_does_not_exit_immediately(tmp_path: Path) -> None:
    rss, clock, wall = [500 * MB], [1000.0], [1_800_000_000.0]
    watchdog = _graced(rss, clock, wall, tmp_path / "wd.json")
    assert watchdog.grace_seconds == 300.0
    assert watchdog.seconds_until_check() == 300.0
    for elapsed in (0.0, 30.0, 299.0):
        clock[0] = 1000.0 + elapsed
        assert watchdog.check() is False
    assert watchdog.tripped is False
    clock[0] = 1300.0
    assert watchdog.check() is True


def test_supervisor_does_not_stop_during_the_startup_grace() -> None:
    supervisor = _supervisor()
    supervisor.attach_memory_watchdog(
        MemoryWatchdog(
            limit_bytes=1000, check_seconds=0.05, startup_grace_seconds=60.0, read_rss=lambda: 2000
        )
    )
    stop = threading.Timer(0.5, lambda: setattr(supervisor, "_signaled", True))
    # Timed from ready, so the serve loop runs (and would check) before the stop.
    supervisor.serve_until_signal(ready=stop.start)
    assert supervisor.memory_watchdog_tripped is False
    exit_if_memory_watchdog_tripped(supervisor)


def _trip(rss_mb: int, clock: list[float], wall: list[float], state: Path) -> MemoryWatchdog:
    rss = [rss_mb * MB]
    watchdog = _graced(rss, clock, wall, state)
    clock[0] += watchdog.grace_seconds
    wall[0] += watchdog.grace_seconds
    assert watchdog.check() is True
    return watchdog


def test_early_trips_back_off_exponentially_across_restarts_up_to_the_cap(
    tmp_path: Path,
) -> None:
    state = tmp_path / "wd.json"
    clock, wall = [0.0], [1_800_000_000.0]
    graces = []
    for _ in range(6):
        graces.append(_trip(500, clock, wall, state).grace_seconds)
        wall[0] += 5.0  # restart delay
    assert graces == [300.0, 600.0, 1200.0, 2400.0, 3600.0, 3600.0]
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert set(saved) == {"consecutive_trips", "tripped_at"}


def test_late_leak_trip_and_stale_state_reset_the_backoff(tmp_path: Path) -> None:
    state = tmp_path / "wd.json"
    clock, wall = [0.0], [1_800_000_000.0]
    _trip(500, clock, wall, state)
    assert _graced([0], clock, wall, state).grace_seconds == 600.0
    # A trip hours after start is an ordinary leak: the streak restarts at one.
    rss = [100 * MB]
    watchdog = _graced(rss, clock, wall, state)
    clock[0] += 600.0
    assert watchdog.check() is False
    rss[0] = 500 * MB
    clock[0] += 5 * 3600.0
    wall[0] += 5 * 3600.0
    assert watchdog.check() is True
    assert json.loads(state.read_text(encoding="utf-8"))["consecutive_trips"] == 1
    assert _graced([0], clock, wall, state).grace_seconds == 600.0
    # State older than the backoff cap is ignored.
    wall[0] += 3601.0
    assert _graced([0], clock, wall, state).grace_seconds == 300.0


def test_missing_corrupt_or_unwritable_state_falls_back_to_the_plain_grace(
    tmp_path: Path,
) -> None:
    clock, wall = [0.0], [1_800_000_000.0]
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _graced([0], clock, wall, bad).grace_seconds == 300.0
    assert _graced([0], clock, wall, tmp_path / "missing.json").grace_seconds == 300.0
    assert _graced([0], clock, wall, None).grace_seconds == 300.0
    unwritable = tmp_path / "no-such-dir" / "wd.json"
    assert _trip(500, clock, wall, unwritable).tripped is True


def test_watchdog_grace_settings_reach_the_supervisor(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    assert settings.paper_worker_memory_watchdog_startup_grace_seconds == 300.0
    assert settings.paper_worker_memory_watchdog_backoff_max_seconds == 3600.0
    custom = Settings(
        _env_file=None,
        paper_worker_memory_watchdog_startup_grace_seconds=120.0,
        paper_worker_memory_watchdog_state_path=str(tmp_path / "wd.json"),
    )
    watchdog = build_paper_worker_supervisor(custom)._memory_watchdog
    assert watchdog is not None and watchdog.grace_seconds == 120.0


def _streak_after_trip_at(
    uptime: float, prior_trips: int, tmp_path: Path, *, window: float = 900.0
) -> tuple[MemoryWatchdog, int]:
    state = tmp_path / "wd.json"
    wall = [1_800_000_000.0]
    state.write_text(
        json.dumps({"consecutive_trips": prior_trips, "tripped_at": wall[0] - 5.0}),
        encoding="utf-8",
    )
    clock = [0.0]
    watchdog = MemoryWatchdog(
        limit_bytes=440 * MB,
        check_seconds=30.0,
        startup_grace_seconds=300.0,
        backoff_max_seconds=3600.0,
        early_trip_window_seconds=window,
        state_path=state,
        read_rss=lambda: 500 * MB,
        monotonic=lambda: clock[0],
        wall_clock=lambda: wall[0],
    )
    clock[0] = uptime
    wall[0] += uptime
    assert watchdog.check() is True
    return watchdog, json.loads(state.read_text(encoding="utf-8"))["consecutive_trips"]


def test_leak_tripping_90_minutes_after_start_resets_the_streak(tmp_path: Path) -> None:
    # Three earlier boot trips: the grace is 2400 s. The old rule (grace plus
    # the 3600 s backoff cap) still counted a 90 min trip as a boot trip.
    watchdog, saved = _streak_after_trip_at(90 * 60.0, 3, tmp_path)
    assert watchdog.grace_seconds == 2400.0
    assert watchdog.consecutive_trips == 1 and saved == 1


def test_leak_tripping_an_hour_after_start_resets_the_streak(tmp_path: Path) -> None:
    watchdog, saved = _streak_after_trip_at(3600.0, 1, tmp_path)
    assert watchdog.grace_seconds == 600.0
    assert watchdog.consecutive_trips == 1 and saved == 1


def test_trip_inside_the_early_window_continues_the_streak(tmp_path: Path) -> None:
    # Grace 600 s after one prior trip; the window ends 900 s after the grace.
    watchdog, saved = _streak_after_trip_at(600.0 + 900.0, 1, tmp_path)
    assert watchdog.consecutive_trips == 2 and saved == 2
    watchdog, saved = _streak_after_trip_at(600.0 + 901.0, 1, tmp_path)
    assert watchdog.consecutive_trips == 1 and saved == 1


def test_early_trip_window_is_configurable(tmp_path: Path) -> None:
    settings = Settings(_env_file=None)
    assert settings.paper_worker_memory_watchdog_early_trip_window_seconds == 900.0
    _, saved = _streak_after_trip_at(600.0 + 1000.0, 1, tmp_path, window=1200.0)
    assert saved == 2
    custom = Settings(
        _env_file=None,
        paper_worker_memory_watchdog_early_trip_window_seconds=60.0,
        paper_worker_memory_watchdog_state_path=str(tmp_path / "wd.json"),
    )
    watchdog = build_paper_worker_supervisor(custom)._memory_watchdog
    assert watchdog is not None and watchdog._early_trip_window_seconds == 60.0


def _ceilinged(rss: list[int], clock: list[float], **kwargs: object) -> MemoryWatchdog:
    return MemoryWatchdog(
        limit_bytes=440 * MB,
        check_seconds=30.0,
        startup_grace_seconds=float(kwargs.get("grace", 300.0)),  # type: ignore[arg-type]
        ceiling_bytes=int(kwargs.get("ceiling", 480 * MB)),  # type: ignore[arg-type]
        ceiling_check_seconds=5.0,
        read_rss=lambda: rss[0],
        monotonic=lambda: clock[0],
    )


def test_ceiling_trips_during_the_startup_grace() -> None:
    rss, clock = [300 * MB], [0.0]
    watchdog = _ceilinged(rss, clock)
    assert watchdog.check() is False
    # Between the soft limit and the ceiling the grace still holds.
    rss[0] = 460 * MB
    clock[0] = 5.0
    assert watchdog.check() is False
    assert watchdog.seconds_until_check() == 5.0
    # A catch-up spike inside the grace becomes a clean stop, not a kernel kill.
    rss[0] = 490 * MB
    clock[0] = 10.0
    with capture_logs() as logs:
        assert watchdog.check() is True
    tripped = next(e for e in logs if e["event"] == "paper_worker_memory_watchdog_tripped")
    assert tripped["trigger"] == "ceiling"
    assert tripped["ceiling_bytes"] == 480 * MB


def test_ceiling_catches_a_spike_between_soft_limit_checks() -> None:
    rss, clock = [300 * MB], [0.0]
    watchdog = _ceilinged(rss, clock, grace=0.0)
    assert watchdog.check() is False
    rss[0] = 500 * MB
    clock[0] = 5.0
    # The 30 s soft check is not due yet; the 5 s ceiling check is.
    assert watchdog.seconds_until_check() == 0.0
    assert watchdog.check() is True


def test_ceiling_is_read_only_on_its_own_interval() -> None:
    reads: list[int] = []

    def read() -> int:
        reads.append(1)
        return 300 * MB

    clock = [0.0]
    watchdog = MemoryWatchdog(
        limit_bytes=440 * MB,
        check_seconds=30.0,
        startup_grace_seconds=300.0,
        ceiling_bytes=480 * MB,
        ceiling_check_seconds=5.0,
        read_rss=read,
        monotonic=lambda: clock[0],
    )
    for moment in (0.0, 1.0, 4.9, 5.0, 9.0, 10.0):
        clock[0] = moment
        watchdog.check()
    assert len(reads) == 3


def test_ceiling_below_the_soft_limit_is_raised_to_it() -> None:
    rss, clock = [450 * MB], [0.0]
    watchdog = _ceilinged(rss, clock, ceiling=400 * MB)
    assert watchdog.ceiling_bytes == 440 * MB
    # 450 MB is over the clamped ceiling, so it trips even inside the grace.
    assert watchdog.check() is True


def test_zero_ceiling_keeps_the_grace_behaviour() -> None:
    rss, clock = [500 * MB], [0.0]
    watchdog = _ceilinged(rss, clock, ceiling=0)
    assert watchdog.ceiling_bytes is None
    assert watchdog.seconds_until_check() == 300.0
    clock[0] = 299.0
    assert watchdog.check() is False


def test_ceiling_settings_reach_the_supervisor() -> None:
    settings = Settings(_env_file=None)
    assert settings.paper_worker_memory_watchdog_ceiling_mb == 480
    assert settings.paper_worker_memory_watchdog_ceiling_check_seconds == 5.0
    watchdog = build_paper_worker_supervisor(settings)._memory_watchdog
    assert watchdog is not None and watchdog.ceiling_bytes == 480 * MB
    off = Settings(_env_file=None, paper_worker_memory_watchdog_ceiling_mb=0)
    disabled = build_paper_worker_supervisor(off)._memory_watchdog
    assert disabled is not None and disabled.ceiling_bytes is None


def test_watchdog_trip_streak_survives_a_lost_state_file() -> None:
    """L8: the durable store keeps the backoff when /tmp is wiped by a restart."""

    from app.observability.memory_watchdog import MemoryWatchdog

    class _Store:
        def __init__(self) -> None:
            self.saved: tuple[int, float] | None = None

        def load(self) -> tuple[int, float] | None:
            return self.saved

        def save(self, *, trips: int, tripped_at: float) -> None:
            self.saved = (trips, tripped_at)

    store = _Store()
    wall = {"now": 1_000.0}
    ticks = {"m": 0.0}
    watchdog = MemoryWatchdog(
        limit_bytes=100,
        check_seconds=1.0,
        startup_grace_seconds=60.0,
        backoff_max_seconds=600.0,
        state_store=store,
        read_rss=lambda: 200,
        monotonic=lambda: ticks["m"],
        wall_clock=lambda: wall["now"],
    )
    ticks["m"] = 61.0
    assert watchdog.check() is True
    assert store.saved == (1, 1_000.0)
    # Next boot: no state file at all, but the store remembers one early trip.
    wall["now"] = 1_100.0
    rebooted = MemoryWatchdog(
        limit_bytes=100,
        check_seconds=1.0,
        startup_grace_seconds=60.0,
        backoff_max_seconds=600.0,
        state_store=store,
        read_rss=lambda: 0,
        monotonic=lambda: 0.0,
        wall_clock=lambda: wall["now"],
    )
    assert rebooted.prior_trips == 1
    assert rebooted.grace_seconds == 120.0


def test_watchdog_store_failures_never_block_a_trip() -> None:
    from app.observability.memory_watchdog import MemoryWatchdog

    class _Broken:
        def load(self) -> tuple[int, float] | None:
            raise RuntimeError("db down")

        def save(self, *, trips: int, tripped_at: float) -> None:
            raise RuntimeError("db down")

    ticks = {"m": 0.0}
    watchdog = MemoryWatchdog(
        limit_bytes=100,
        check_seconds=1.0,
        state_store=_Broken(),
        read_rss=lambda: 200,
        monotonic=lambda: ticks["m"],
    )
    assert watchdog.prior_trips == 0
    assert watchdog.check() is True
