"""Staging paper worker visibility: boot config report and Binance ban state."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks

from app.core.config import Settings
from app.market_contracts.adapters.binance_rest_gate import BinanceRestGate
from app.persistence.runtime_status import PAPER_WORKER_COMPONENT
from app.services import market_ban_visibility, worker_config_report
from app.services.market_ban_visibility import (
    MARKET_GATE_COMPONENT,
    MarketBanPublisher,
    project_market_ban,
)
from app.services.safety_alerts import reset_safety_alert_dedup
from app.services.worker_config_report import (
    WORKER_CONFIG_COMPONENT,
    build_worker_config_report,
    project_worker_config,
    publish_worker_config_report,
)
from app.workers import paper_worker
from app.workers.paper_worker import CycleOutcome, PaperWorkerSupervisor
from tests.test_controlled_paper_activation import _STAGING, _package

_TOKEN = "123456789:AAHtestTokenValueForStagingPackage"
_NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
_CONFLICT = "telegram_example_conflict"


# --- 1. config report ---------------------------------------------------------


def test_report_not_applicable_outside_staging() -> None:
    settings = Settings(environment="local")
    report = build_worker_config_report(settings)
    assert report.status == "not_applicable"
    assert report.checks == ()


def test_full_armed_package_without_autonomy_is_degraded_not_disarmed() -> None:
    report = build_worker_config_report(_package())
    assert report.status == "degraded"
    assert report.failing == ("autonomy_mode",)


def test_full_armed_package_with_autonomy_is_ok() -> None:
    report = build_worker_config_report(_package(autonomous_internal_paper_enabled=True))
    assert report.status == "ok", report.failing


def test_enrollment_and_boot_conflict_are_degraded() -> None:
    settings = _package(
        autonomous_internal_paper_enabled=True,
        telegram_paper_activation_armed=False,
    )
    report = build_worker_config_report(settings, boot_degradations=(_CONFLICT,))
    assert report.status == "degraded"
    assert set(report.failing) == {"telegram_projection", "telegram_boot_conflict"}


def test_watcher_off_is_disarmed_unexpected_and_holds_no_secret() -> None:
    settings = Settings(**{**_STAGING, "telegram_bot_token": _TOKEN, "telegram_chat_id": "c"})
    report = build_worker_config_report(settings)
    assert report.status == "disarmed_unexpected"
    assert "watcher_orchestration_enabled" in report.failing
    dumped = repr([check.model_dump() for check in report.checks])
    assert _TOKEN not in dumped
    assert "token_present" in dumped


def _row_for(report: worker_config_report.WorkerConfigReport) -> SimpleNamespace:
    actual = {check.name: check.actual for check in report.checks}
    return SimpleNamespace(
        activation_state=report.status,
        request_count=report.failure_mask,
        last_scan_reason=actual["exchange_mode"],
        market_source=actual["perpetual_evidence_source"],
        telegram_runtime_state=actual["telegram_projection"],
        inbound_mode=actual["autonomy_mode"],
        last_error_code=actual["telegram_boot_conflict"],
        heartbeat_at=_NOW,
    )


def test_published_row_round_trips_to_health() -> None:
    report = build_worker_config_report(
        _package(telegram_paper_activation_armed=False),
        boot_degradations=(_CONFLICT,),
    )
    observed = project_worker_config(_row_for(report))
    assert observed.status == report.status
    assert observed.checks == list(report.checks)
    assert project_worker_config(None).status == "unknown"


def test_publish_never_raises_and_alerts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_safety_alert_dedup()
    sent: list[str] = []

    class _Provider:
        def __init__(self, _settings: object) -> None:
            pass

        def deliver(self, payload: object, **_kwargs: object) -> object:
            sent.append(str(getattr(payload, "message", "")))
            return SimpleNamespace(success=True, skipped=False)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("database down")

    monkeypatch.setattr("app.services.safety_alerts.TelegramAlertDeliveryProvider", _Provider)
    monkeypatch.setattr(worker_config_report, "_persist", _boom)
    monkeypatch.setattr("app.services.worker_heartbeat._session_factory", _boom)
    settings = _package()
    for _ in range(2):
        assert publish_worker_config_report(settings, now=_NOW).status == "degraded"
    assert len(sent) == 1
    assert _TOKEN not in sent[0]
    reset_safety_alert_dedup()


def test_memory_diagnostics_failure_does_not_stop_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken() -> None:
        raise OSError("proc unreadable")

    monkeypatch.setattr(paper_worker, "WorkerMemoryDiagnostics", _broken)
    supervisor = PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome("running"),
        telegram_cycle=lambda: CycleOutcome("disarmed"),
        memory_diagnostics_enabled=True,
    )
    assert supervisor.run_round().watcher.status == "running"


# --- 2. Binance ban visibility --------------------------------------------------


def test_gate_state_reports_ban_and_keeps_start() -> None:
    now = [1000.0]
    gate = BinanceRestGate(clock=lambda: now[0], wall_clock=lambda: 1_800_000_000 + now[0])
    assert gate.state().banned is False
    gate.note_failure("ban", 600.0)
    first = gate.state()
    assert (first.banned, first.last_status_code) == (True, 418)
    assert first.until_epoch is not None and first.until_epoch > 1_800_000_000 + 1000
    now[0] += 10
    gate.note_failure("ban", 600.0)
    assert gate.state().ban_started_epoch == first.ban_started_epoch
    now[0] += 5000
    assert gate.state().banned is False


def test_concurrent_bans_record_one_ban_start() -> None:
    """The ban check and the close share one lock: a second 418 sees the first ban."""

    stamps: list[float] = []

    def wall_clock() -> float:
        stamps.append(float(len(stamps)))
        return 1_800_000_000 + stamps[-1]

    def slow_rng() -> float:
        # Widens the window between the ban-start write and the close.
        time.sleep(0.05)
        return 0.0

    gate = BinanceRestGate(clock=time.monotonic, wall_clock=wall_clock, rng=slow_rng)
    start = threading.Barrier(2)

    def hit() -> None:
        start.wait()
        gate.note_failure("ban", 600.0)

    threads = [threading.Thread(target=hit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert len(stamps) == 1
    assert gate.state().ban_started_epoch == 1_800_000_000


def test_publisher_writes_on_change_and_alerts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    gate = BinanceRestGate(clock=lambda: now[0], wall_clock=lambda: 1_800_000_000 + now[0])
    monkeypatch.setattr(
        "app.market_contracts.adapters.binance_rest_gate.process_binance_rest_gate_state",
        gate.state,
    )
    writes: list[str] = []
    alerts: list[str] = []
    monkeypatch.setattr(
        "app.persistence.runtime_status.publish_runtime_status",
        lambda _factory, write: writes.append(write.activation_state),
    )
    monkeypatch.setattr(
        "app.services.worker_heartbeat.raise_prepared_alert",
        lambda _settings, alert, **_kwargs: alerts.append(alert.code) or True,
    )
    publisher = MarketBanPublisher(_package(), object())  # type: ignore[arg-type]
    publisher.observe()
    gate.note_failure("ban", 600.0)
    publisher.observe()
    publisher.observe()
    now[0] += 5000
    publisher.observe()
    assert writes == ["open", "banned", "open"]
    assert alerts == ["market_data_banned"]


def test_publisher_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> None:
        raise RuntimeError("gate broken")

    monkeypatch.setattr(
        "app.market_contracts.adapters.binance_rest_gate.process_binance_rest_gate_state", _boom
    )
    MarketBanPublisher(_package(), object()).observe()  # type: ignore[arg-type]
    assert market_ban_visibility.MARKET_GATE_COMPONENT == "market_data_gate"


def test_ban_projection_expires_with_deadline() -> None:
    row = SimpleNamespace(
        activation_state="banned",
        lease_expires_at=_NOW + timedelta(minutes=5),
        last_error_code="http_418",
        heartbeat_at=_NOW,
    )
    live = project_market_ban(row, now=_NOW)
    assert (live.banned, live.last_status_code, live.state) == (True, 418, "banned")
    expired = project_market_ban(row, now=_NOW + timedelta(minutes=10))
    assert (expired.banned, expired.state) == (False, "open")
    assert project_market_ban(None, now=_NOW).state == "unknown"


# --- 3. health route -----------------------------------------------------------


def test_paper_worker_health_projects_config_and_ban_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routes.health import paper_worker_heartbeat

    reset_safety_alert_dedup()
    observed_at = datetime.now(UTC)
    report = build_worker_config_report(_package())
    ban = SimpleNamespace(
        activation_state="banned",
        lease_expires_at=observed_at + timedelta(hours=1),
        last_error_code="http_418",
        heartbeat_at=observed_at,
    )
    rows = {
        PAPER_WORKER_COMPONENT: None,
        WORKER_CONFIG_COMPONENT: _row_for(report),
        MARKET_GATE_COMPONENT: ban,
    }
    monkeypatch.setattr("app.api.routes.health._load_runtime_rows", lambda: rows)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: None)
    monkeypatch.setattr(
        "app.services.worker_heartbeat.raise_heartbeat_alert", lambda *_a, **_k: False
    )
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    body = asyncio.run(paper_worker_heartbeat(settings, BackgroundTasks()))
    assert body.worker_config.status == "degraded"
    assert [check.name for check in body.worker_config.checks if not check.ok] == ["autonomy_mode"]
    assert (body.market_data_ban.banned, body.market_data_ban.last_status_code) == (True, 418)
    assert _TOKEN not in body.model_dump_json()
