"""Staging paper worker resilience: config report, fail-soft Telegram, DB retry, ban state."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError, OperationalError, PendingRollbackError

from app.core.config import Settings, get_settings
from app.core.disarmed_worker_boot import (
    TELEGRAM_CONFIG_CONFLICT,
    WorkerBootRole,
    load_worker_process_settings,
    reset_disarmed_worker_boot,
    worker_boot_degradations,
)
from app.market_contracts.adapters.binance_rest_gate import BinanceRestGate
from app.services import market_ban_visibility, worker_config_report
from app.services.market_ban_visibility import MarketBanPublisher, project_market_ban
from app.services.safety_alerts import reset_safety_alert_dedup
from app.services.worker_config_report import (
    build_worker_config_report,
    project_worker_config,
    publish_worker_config_report,
)
from app.workers import paper_worker
from app.workers.paper_worker import (
    ComponentHealth,
    CycleOutcome,
    PaperWorkerSupervisor,
    is_transient_db_error,
)
from tests.test_controlled_paper_activation import _STAGING, _package

_TOKEN = "123456789:AAHtestTokenValueForStagingPackage"
_NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


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
    report = build_worker_config_report(settings, boot_degradations=(TELEGRAM_CONFIG_CONFLICT,))
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


def test_published_row_round_trips_to_health() -> None:
    report = build_worker_config_report(
        _package(telegram_paper_activation_armed=False),
        boot_degradations=(TELEGRAM_CONFIG_CONFLICT,),
    )
    actual = {check.name: check.actual for check in report.checks}
    row = SimpleNamespace(
        activation_state=report.status,
        request_count=report.failure_mask,
        last_scan_reason=actual["exchange_mode"],
        market_source=actual["perpetual_evidence_source"],
        telegram_runtime_state=actual["telegram_projection"],
        inbound_mode=actual["autonomy_mode"],
        last_error_code=actual["telegram_boot_conflict"],
        heartbeat_at=_NOW,
    )
    observed = project_worker_config(row)
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


# --- 2. Telegram conflicts fail soft on the paper worker ------------------------


@pytest.fixture
def _staging_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    reset_disarmed_worker_boot()
    get_settings.cache_clear()
    for key, value in _STAGING.items():
        text = str(value).lower() if isinstance(value, bool) else str(value)
        monkeypatch.setenv(key.upper(), text)
    conflict = {
        "WATCHER_ORCHESTRATION_ENABLED": "true",
        "WATCHER_PAPER_STAGING_ACTIVATION": "true",
        # Armed projection without a bot token: the 2026-11-01 class of conflict.
        "TELEGRAM_PAPER_ACTIVATION_ARMED": "true",
        "TELEGRAM_INTERACTION_ENABLED": "true",
        "TELEGRAM_INBOUND_MODE": "polling",
        "TELEGRAM_NETWORK_PERMITTED": "true",
        "TELEGRAM_BOT_ID": "123456",
        "TELEGRAM_CHAT_ID": "789",
    }
    for key, value in conflict.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    yield monkeypatch
    reset_disarmed_worker_boot()
    get_settings.cache_clear()


def test_paper_worker_boots_with_telegram_off(_staging_env: pytest.MonkeyPatch) -> None:
    settings = load_worker_process_settings(WorkerBootRole.PAPER_WORKER)
    assert settings.telegram_paper_activation_armed is False
    assert settings.telegram_network_permitted is False
    assert settings.watcher_orchestration_enabled is True
    assert settings.real_trading_enabled is False
    assert worker_boot_degradations() == (TELEGRAM_CONFIG_CONFLICT,)
    report = build_worker_config_report(settings, boot_degradations=worker_boot_degradations())
    assert report.status == "degraded"
    assert "telegram_boot_conflict" in report.failing


def test_trade_path_conflict_still_raises(_staging_env: pytest.MonkeyPatch) -> None:
    _staging_env.setenv("EXECUTION_MODE", "trade")
    with pytest.raises(ValueError):
        load_worker_process_settings(WorkerBootRole.PAPER_WORKER)
    assert worker_boot_degradations() == ()
    assert os.environ["TELEGRAM_NETWORK_PERMITTED"] == "true"


def test_dedicated_telegram_worker_still_fails_closed(_staging_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        load_worker_process_settings(WorkerBootRole.TELEGRAM_PAPER)


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


# --- 3. transient DB errors retry (allowlisted components only) ----------------


def _operational() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("server closed the connection"))


def test_transient_db_error_classification() -> None:
    invalidated = DBAPIError("SELECT 1", {}, Exception("x"), connection_invalidated=True)
    wrapped = RuntimeError("scan failed")
    wrapped.__cause__ = PendingRollbackError("rollback first")
    assert is_transient_db_error(_operational())
    assert is_transient_db_error(invalidated)
    assert is_transient_db_error(wrapped)
    assert not is_transient_db_error(DBAPIError("SELECT 1", {}, Exception("x")))
    assert not is_transient_db_error(ValueError("bad"))


def _supervisor(
    cycle: object, monkeypatch: pytest.MonkeyPatch, *, component: str = "activity"
) -> PaperWorkerSupervisor:
    """``cycle`` runs as ``component``; every other component is a no-op."""

    monkeypatch.setattr(paper_worker, "_DB_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    idle = lambda: CycleOutcome("disarmed")  # noqa: E731
    supervisor = PaperWorkerSupervisor(
        watcher_cycle=cycle if component == "watcher" else idle,  # type: ignore[arg-type]
        telegram_cycle=cycle if component == "telegram" else idle,  # type: ignore[arg-type]
    )
    if component == "activity":
        supervisor.attach_activity(cycle, poll_seconds=1.0, request_stop=lambda: None)  # type: ignore[arg-type]
    elif component == "indexing":
        supervisor.attach_indexing(cycle, close=lambda: None)  # type: ignore[arg-type]
    elif component == "exit_monitor":
        supervisor.attach_exit_monitor(cycle, poll_seconds=1.0, request_stop=lambda: None)  # type: ignore[arg-type]
    elif component == "demo_snapshot":
        supervisor.attach_demo_snapshot(cycle, poll_seconds=1.0, request_stop=lambda: None)  # type: ignore[arg-type]
    return supervisor


def _health(supervisor: PaperWorkerSupervisor, component: str) -> ComponentHealth:
    health = getattr(supervisor.run_round(), component)
    assert health is not None
    return health


@pytest.mark.parametrize("component", ["activity", "indexing"])
def test_allowlisted_component_retries_then_succeeds(
    component: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def cycle() -> CycleOutcome:
        calls.append(1)
        if len(calls) < 3:
            raise _operational()
        return CycleOutcome("running")

    health = _health(_supervisor(cycle, monkeypatch, component=component), component)
    assert len(calls) == 3
    assert (health.status, health.restart_count) == ("running", 0)


def test_persistent_transient_error_fails_after_bounded_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def cycle() -> CycleOutcome:
        calls.append(1)
        raise _operational()

    health = _health(_supervisor(cycle, monkeypatch), "activity")
    assert len(calls) == 3
    assert (health.status, health.restart_count) == ("failed", 1)


def test_non_transient_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def cycle() -> CycleOutcome:
        calls.append(1)
        raise ValueError("risk refused")

    assert _health(_supervisor(cycle, monkeypatch), "activity").status == "failed"
    assert len(calls) == 1


@pytest.mark.parametrize("component", ["watcher", "telegram", "exit_monitor", "demo_snapshot"])
def test_side_effect_component_is_not_retried(
    component: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Order, claim, journal, kill-switch and message paths fail once, as before."""

    calls: list[int] = []

    def cycle() -> CycleOutcome:
        calls.append(1)
        raise _operational()

    health = _health(_supervisor(cycle, monkeypatch, component=component), component)
    assert len(calls) == 1
    assert (health.status, health.restart_count) == ("failed", 1)


def test_unknown_component_defaults_to_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paper_worker, "_DB_RETRY_BACKOFF_SECONDS", (0.0, 0.0))
    supervisor = PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome("disarmed"),
        telegram_cycle=lambda: CycleOutcome("disarmed"),
    )
    calls: list[int] = []

    def cycle() -> CycleOutcome:
        calls.append(1)
        raise _operational()

    component = paper_worker._Component("new_component", cycle, None)
    with pytest.raises(OperationalError):
        supervisor._cycle_with_db_retry(component)
    assert len(calls) == 1
    assert component.name not in paper_worker._DB_RETRY_COMPONENTS


# --- 5. Binance ban visibility --------------------------------------------------


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
