"""Watchdog streak alert and unclean-previous-exit detection (Griller PR #426)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.observability.memory_watchdog import MEMORY_WATCHDOG_EXIT_CODE, MemoryWatchdog
from app.observability.run_marker import UncleanExit, claim_run_marker, clear_run_marker
from app.services import safety_alerts
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.safety_alerts import safety_alert_message, safety_alert_telegram_allowed
from app.services.worker_restart_alerts import (
    alert_memory_watchdog_repeat,
    alert_unclean_previous_exit,
)

MB = 1024 * 1024
NOW = datetime(2027, 1, 15, 12, 0, tzinfo=UTC)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[ExitMonitorAlert]:
    alerts: list[ExitMonitorAlert] = []

    def _deliver(_settings: Settings, alert: ExitMonitorAlert, **_kwargs: Any) -> bool:
        alerts.append(alert)
        return True

    monkeypatch.setattr(safety_alerts, "deliver_safety_alert", _deliver)
    return alerts


def _tripped(tmp_path: Path, prior_trips: int) -> MemoryWatchdog:
    state = tmp_path / "wd.json"
    wall = 1_800_000_000.0
    if prior_trips:
        state.write_text(
            json.dumps({"consecutive_trips": prior_trips, "tripped_at": wall - 5.0}),
            encoding="utf-8",
        )
    clock = [0.0]
    watchdog = MemoryWatchdog(
        limit_bytes=440 * MB,
        check_seconds=30.0,
        startup_grace_seconds=300.0,
        backoff_max_seconds=3600.0,
        state_path=state,
        read_rss=lambda: 500 * MB,
        monotonic=lambda: clock[0],
        wall_clock=lambda: wall,
    )
    clock[0] = watchdog.grace_seconds
    assert watchdog.check() is True
    return watchdog


# --- Repeated watchdog trips --------------------------------------------------


def test_second_consecutive_trip_alerts_and_first_does_not(
    tmp_path: Path, sent: list[ExitMonitorAlert]
) -> None:
    settings = Settings(_env_file=None)
    assert settings.paper_worker_memory_watchdog_alert_after_trips == 2
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = _tripped(tmp_path / "a", 0)
    assert first.consecutive_trips == 1
    assert alert_memory_watchdog_repeat(settings, first, now=NOW) is False
    assert sent == []
    second = _tripped(tmp_path / "b", 1)
    assert second.consecutive_trips == 2
    assert alert_memory_watchdog_repeat(settings, second, now=NOW) is True
    [alert] = sent
    assert alert.code == "worker_memory_watchdog_repeat"
    assert alert.fields["consecutive_trips"] == "2"
    assert alert.fields["grace_seconds"] == "600"


def test_watchdog_alert_threshold_is_configurable_and_zero_disables(
    tmp_path: Path, sent: list[ExitMonitorAlert]
) -> None:
    watchdog = _tripped(tmp_path, 2)
    assert watchdog.consecutive_trips == 3
    four = Settings(_env_file=None, paper_worker_memory_watchdog_alert_after_trips=4)
    assert alert_memory_watchdog_repeat(four, watchdog, now=NOW) is False
    off = Settings(_env_file=None, paper_worker_memory_watchdog_alert_after_trips=0)
    assert alert_memory_watchdog_repeat(off, watchdog, now=NOW) is False
    three = Settings(_env_file=None, paper_worker_memory_watchdog_alert_after_trips=3)
    assert alert_memory_watchdog_repeat(three, watchdog, now=NOW) is True
    assert [alert.fields["consecutive_trips"] for alert in sent] == ["3"]


def test_untripped_or_absent_watchdog_never_alerts(sent: list[ExitMonitorAlert]) -> None:
    settings = Settings(_env_file=None, paper_worker_memory_watchdog_alert_after_trips=1)
    idle = MemoryWatchdog(limit_bytes=1 << 40, check_seconds=1.0, read_rss=lambda: 1)
    assert alert_memory_watchdog_repeat(settings, idle, now=NOW) is False
    assert alert_memory_watchdog_repeat(settings, None, now=NOW) is False
    assert sent == []


def test_restart_alert_codes_use_the_default_telegram_safety_path() -> None:
    settings = Settings(_env_file=None)
    for code in ("worker_memory_watchdog_repeat", "worker_unclean_exit"):
        assert safety_alert_telegram_allowed(settings, code) is True
    text = safety_alert_message(
        code="worker_memory_watchdog_repeat",
        message="tripped",
        fields={"consecutive_trips": "2", "grace_seconds": "600", "component": "paper_worker"},
    )
    assert "consecutive_trips: 2" in text and "grace_seconds: 600" in text


def test_alert_delivery_failure_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("telegram down")

    monkeypatch.setattr(safety_alerts, "deliver_safety_alert", _boom)
    settings = Settings(_env_file=None, paper_worker_memory_watchdog_alert_after_trips=1)
    assert alert_memory_watchdog_repeat(settings, _tripped(tmp_path, 0), now=NOW) is False
    assert alert_unclean_previous_exit(settings, UncleanExit(started_at=None), now=NOW) is False


# --- Running marker -----------------------------------------------------------


def test_marker_reports_a_run_that_did_not_stop_cleanly(tmp_path: Path) -> None:
    marker = tmp_path / "worker.running"
    assert claim_run_marker(marker, now=1000.0) is None
    assert json.loads(marker.read_text(encoding="utf-8")) == {"started_at": 1000.0}
    # No clean stop: the next boot finds the marker.
    assert claim_run_marker(marker, now=2000.0) == UncleanExit(started_at=1000.0)
    clear_run_marker(marker)
    assert not marker.exists()
    assert claim_run_marker(marker, now=3000.0) is None


def test_marker_failures_and_disabled_path_never_raise(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.running"
    corrupt.write_text("{nope", encoding="utf-8")
    assert claim_run_marker(corrupt, now=1.0) == UncleanExit(started_at=None)
    assert claim_run_marker(tmp_path / "no-dir" / "x.running", now=1.0) is None
    clear_run_marker(tmp_path / "no-dir" / "x.running")
    assert claim_run_marker("", now=1.0) is None
    assert claim_run_marker(None, now=1.0) is None
    clear_run_marker("")


def test_unclean_exit_is_logged_and_alerted(sent: list[ExitMonitorAlert]) -> None:
    settings = Settings(_env_file=None)
    assert alert_unclean_previous_exit(settings, None, now=NOW) is False
    assert sent == []
    started = NOW.timestamp() - 125.0
    assert alert_unclean_previous_exit(settings, UncleanExit(started), now=NOW) is True
    assert alert_unclean_previous_exit(settings, UncleanExit(None), now=NOW) is True
    assert [alert.code for alert in sent] == ["worker_unclean_exit"] * 2
    assert [alert.fields["elapsed_seconds"] for alert in sent] == ["125", "unknown"]


# --- Boot and exit wiring -----------------------------------------------------


class _FakeSupervisor:
    def __init__(self, watchdog: MemoryWatchdog | None, *, fail: bool = False) -> None:
        self.memory_watchdog = watchdog
        self._fail = fail
        self.closed = False

    @property
    def memory_watchdog_tripped(self) -> bool:
        return self.memory_watchdog is not None and self.memory_watchdog.tripped

    def run_round(self) -> None:
        if self._fail:
            raise RuntimeError("crash")

    def close(self) -> None:
        self.closed = True


def _boot(monkeypatch: pytest.MonkeyPatch, settings: Settings, supervisor: _FakeSupervisor) -> str:
    import app.core.disarmed_worker_boot as boot
    import app.core.paper_safety as paper_safety
    import app.services.risk.kill_switch as kill_switch
    import app.services.worker_config_report as config_report
    from app.workers import paper_worker

    monkeypatch.setattr(boot, "load_worker_process_settings", lambda _role: settings)
    monkeypatch.setattr(boot, "settings_are_disarmed_paper_worker", lambda _s: False)
    monkeypatch.setattr(paper_safety, "assert_execution_capable_composition_root", lambda _s: None)
    monkeypatch.setattr(config_report, "publish_worker_config_report", lambda _s: None)
    monkeypatch.setattr(kill_switch, "retry_unsent_kill_switch_alert", lambda _s: None)
    monkeypatch.setattr(paper_worker, "build_paper_worker_supervisor", lambda _s: supervisor)
    return paper_worker.run_paper_worker_process(once=True)


def test_worker_boot_alerts_once_after_a_run_that_crashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sent: list[ExitMonitorAlert]
) -> None:
    marker = tmp_path / "worker.running"
    settings = Settings(_env_file=None, paper_worker_run_marker_path=str(marker))
    assert _boot(monkeypatch, settings, _FakeSupervisor(None)) == "armed"
    assert not marker.exists() and sent == []
    with pytest.raises(RuntimeError):
        _boot(monkeypatch, settings, _FakeSupervisor(None, fail=True))
    assert marker.exists()
    _boot(monkeypatch, settings, _FakeSupervisor(None))
    assert [alert.code for alert in sent] == ["worker_unclean_exit"]
    assert not marker.exists()


def test_watchdog_exit_is_clean_and_alerts_on_a_streak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sent: list[ExitMonitorAlert]
) -> None:
    marker = tmp_path / "worker.running"
    settings = Settings(_env_file=None, paper_worker_run_marker_path=str(marker))
    supervisor = _FakeSupervisor(_tripped(tmp_path, 1))
    with pytest.raises(SystemExit) as raised:
        _boot(monkeypatch, settings, supervisor)
    assert raised.value.code == MEMORY_WATCHDOG_EXIT_CODE
    assert supervisor.closed and not marker.exists()
    assert [alert.code for alert in sent] == ["worker_memory_watchdog_repeat"]
    # The restart after the watchdog exit is not reported as unclean.
    _boot(monkeypatch, settings, _FakeSupervisor(None))
    assert [alert.code for alert in sent] == ["worker_memory_watchdog_repeat"]


# --- Hardening: the shared dedup map stays bounded ------------------------------


@pytest.fixture
def _fresh_dedup() -> Any:
    safety_alerts.reset_safety_alert_dedup()
    yield
    safety_alerts.reset_safety_alert_dedup()


@pytest.mark.usefixtures("_fresh_dedup")
def test_expired_dedup_keys_are_dropped_and_live_ones_still_suppress() -> None:
    start = datetime(2026, 11, 4, tzinfo=UTC)
    for index in range(300):
        safety_alerts._remember(f"stop_hit:{index}", start)
    later = start + timedelta(days=1)
    safety_alerts._remember("stop_hit:new", later)
    # One day is the longest cooldown, so the 300 old keys can no longer dedup.
    assert set(safety_alerts._SENT) == {"stop_hit:new"}
    assert safety_alerts._recently_sent("stop_hit:new", later, timedelta(seconds=900))

    for index in range(300):
        safety_alerts._remember(f"live:{index}", later + timedelta(seconds=index))
    assert len(safety_alerts._SENT) == 301
    assert safety_alerts._recently_sent(
        "live:0", later + timedelta(seconds=300), timedelta(hours=1)
    )


@pytest.mark.usefixtures("_fresh_dedup")
def test_dedup_map_is_capped_and_drops_the_oldest_first() -> None:
    start = datetime(2026, 11, 4, tzinfo=UTC)
    for index in range(5000):
        safety_alerts._remember(f"degraded_repeat:{index}", start + timedelta(seconds=index))
    assert len(safety_alerts._SENT) == safety_alerts._SENT_MAX
    assert "degraded_repeat:0" not in safety_alerts._SENT
    assert "degraded_repeat:4999" in safety_alerts._SENT


@pytest.mark.usefixtures("_fresh_dedup")
def test_concurrent_alert_bookkeeping_stays_bounded() -> None:
    import threading

    start = datetime(2026, 11, 4, tzinfo=UTC)

    def send(worker: int) -> None:
        for index in range(1000):
            moment = start + timedelta(seconds=index)
            safety_alerts._remember(f"w{worker}:{index}", moment)
            safety_alerts._recently_sent(f"w{worker}:{index}", moment, timedelta(seconds=900))

    threads = [threading.Thread(target=send, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(safety_alerts._SENT) <= safety_alerts._SENT_MAX
