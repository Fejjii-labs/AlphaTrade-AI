"""Arming gates, worker heartbeat, Telegram safety alerts, and the persisted counter.

Issue #291. Flags stay off. Real-money trading stays blocked. These tests
do not arm a worker and do not send a live Telegram message.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.db.runtime_status import ControlledRuntimeStatusRow
from app.persistence.paper_exit_degradation import (
    PAPER_EXIT_DEGRADATION_COMPONENT,
    PostgresDegradationStreakStore,
    streak_from_row,
)
from app.providers.alert_delivery.base import AlertDeliveryResult
from app.providers.alert_delivery.telegram import TelegramAlertDeliveryProvider
from app.schemas.common import AlertDeliveryChannel
from app.services.arming_gates import (
    COOLDOWN_INVALID,
    CYCLE_INTERVAL_EXCEEDS_TIMEFRAME,
    paper_arming_refusal,
)
from app.services.autonomous_trading import AutonomousMode, resolve_autonomous_mode
from app.services.paper_exit_monitor import DegradationStreak, ExitMonitorAlert, MonitorReport
from app.services.safety_alerts import (
    deliver_queued_safety_alerts,
    deliver_safety_alert,
    queue_safety_alert,
    reset_safety_alert_dedup,
    safety_alert_message,
)
from app.services.worker_heartbeat import (
    MemoryHeartbeatAlertLedger,
    heartbeat_alert,
    heartbeat_status,
    project_paper_worker_heartbeat,
    publish_paper_worker_heartbeat,
    raise_heartbeat_alert,
    watch_cycle_with_heartbeat,
)
from app.workers.paper_exit_monitor import PaperExitMonitorCycle, run_configured_exit_monitor
from app.workers.paper_worker import (
    CycleOutcome,
    PaperWorkerSupervisor,
    build_paper_worker_supervisor,
)

_NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
_TOKEN = "123456:TESTTOKENVALUENOTASECRET1234567890"
_CHAT = "424242"


def _view(**updates: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "execution_mode": "paper",
        "exchange_mode": "paper_internal",
        "enable_real_trading": False,
        "real_trading_enabled": False,
        "environment": "local",
        "autonomous_internal_paper_enabled": False,
        "autonomous_blofin_demo_enabled": False,
        "paper_signal_cooldown_after_loss_seconds": 3600,
        "watcher_paper_poll_interval_seconds": 15.0,
        "paper_signal_allowed_timeframes": ["1m", "15m"],
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _alert(**fields: str) -> ExitMonitorAlert:
    payload = {
        "dedup_key": "backfill_loss:org:command",
        "refusal": "kill_switch",
        "net_loss": "true",
        "exit_origin": "backfill",
    }
    payload.update(fields)
    return ExitMonitorAlert(
        code="backfill_loss",
        severity="critical",
        message="A historical backfill close recorded a net loss.",
        fields=payload,
    )


class _MemoryStreak:
    def __init__(self) -> None:
        self.streak: DegradationStreak | None = None

    def load(self) -> DegradationStreak | None:
        return self.streak

    def save(self, streak: DegradationStreak | None, *, observed_at: datetime) -> None:
        del observed_at
        self.streak = streak


class _Rows:
    def __init__(self) -> None:
        self.rows: dict[str, ControlledRuntimeStatusRow] = {}


class _Session:
    def __init__(self, rows: _Rows) -> None:
        self._rows = rows

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def begin(self) -> _Session:
        return self

    def get(self, _model: object, key: str) -> ControlledRuntimeStatusRow | None:
        return self._rows.rows.get(key)

    def add(self, row: ControlledRuntimeStatusRow) -> None:
        self._rows.rows[row.component] = row

    def delete(self, row: ControlledRuntimeStatusRow) -> None:
        self._rows.rows.pop(row.component, None)


def test_flags_stay_off_and_real_trading_stays_blocked() -> None:
    settings = Settings(_env_file=None)
    assert settings.paper_exit_monitor_enabled is False
    assert settings.paper_exit_monitor_dry_run is True
    assert settings.autonomous_internal_paper_enabled is False
    assert settings.autonomous_blofin_demo_enabled is False
    assert settings.real_trading_enabled is False
    assert settings.enable_real_trading is False
    blocked = resolve_autonomous_mode(
        _view(enable_real_trading=True, autonomous_internal_paper_enabled=True)
    )
    assert blocked.mode is AutonomousMode.OFF
    assert blocked.refusal == "real_trading_blocked"


@pytest.mark.parametrize(
    "cooldown",
    [0, -1, True, False, None, float("nan"), float("inf"), Decimal("NaN"), "3600"],
)
def test_non_positive_cooldown_refuses_to_arm(cooldown: object) -> None:
    paper = resolve_autonomous_mode(
        _view(
            autonomous_internal_paper_enabled=True,
            paper_signal_cooldown_after_loss_seconds=cooldown,
        )
    )
    demo = resolve_autonomous_mode(
        _view(
            autonomous_blofin_demo_enabled=True,
            exchange_mode="paper_exchange_demo",
            paper_signal_cooldown_after_loss_seconds=cooldown,
        )
    )
    assert paper.mode is AutonomousMode.OFF
    assert paper.refusal == COOLDOWN_INVALID
    assert demo.mode is AutonomousMode.OFF
    assert demo.refusal == COOLDOWN_INVALID


def test_production_still_refuses_autonomous_flags_before_the_cooldown_gate() -> None:
    resolution = resolve_autonomous_mode(
        _view(
            environment="production",
            autonomous_internal_paper_enabled=True,
            paper_signal_cooldown_after_loss_seconds=0,
        )
    )
    assert resolution.mode is AutonomousMode.OFF
    assert resolution.refusal == "autonomous_production_refused"


def test_cycle_longer_than_the_smallest_timeframe_refuses_to_arm() -> None:
    settings = _view(
        autonomous_internal_paper_enabled=True,
        watcher_paper_poll_interval_seconds=120,
        paper_signal_allowed_timeframes=["1m", "5m"],
    )
    resolution = resolve_autonomous_mode(settings)
    assert resolution.mode is AutonomousMode.OFF
    assert resolution.refusal == CYCLE_INTERVAL_EXCEEDS_TIMEFRAME
    assert paper_arming_refusal(settings) == CYCLE_INTERVAL_EXCEEDS_TIMEFRAME


def test_cycle_equal_to_the_smallest_timeframe_can_arm() -> None:
    settings = _view(
        autonomous_internal_paper_enabled=True,
        watcher_paper_poll_interval_seconds=60,
        paper_signal_allowed_timeframes=["1m", "15m"],
    )
    resolution = resolve_autonomous_mode(settings)
    assert resolution.mode is AutonomousMode.INTERNAL_PAPER
    assert resolution.refusal is None


def test_unreadable_timeframe_list_fails_closed() -> None:
    assert (
        paper_arming_refusal(
            _view(
                watcher_paper_poll_interval_seconds=15,
                paper_signal_allowed_timeframes=["1m", "not-a-timeframe"],
            )
        )
        == CYCLE_INTERVAL_EXCEEDS_TIMEFRAME
    )
    assert paper_arming_refusal(_view(paper_signal_allowed_timeframes=[])) == (
        CYCLE_INTERVAL_EXCEEDS_TIMEFRAME
    )


def test_exit_monitor_refuses_to_arm_without_opening_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbidden() -> object:
        raise AssertionError("session opened while arming was refused")

    monkeypatch.setattr("app.db.session.get_session_factory", _forbidden)
    cooldown = Settings(
        _env_file=None,
        paper_exit_monitor_enabled=True,
        paper_signal_cooldown_after_loss_seconds=0,
    )
    report = run_configured_exit_monitor(cooldown)
    assert report.status == "refused"
    assert report.error == COOLDOWN_INVALID

    interval = Settings(
        _env_file=None,
        paper_exit_monitor_enabled=True,
        watcher_paper_poll_interval_seconds=120,
        paper_signal_allowed_timeframes=["1m"],
    )
    refused = run_configured_exit_monitor(interval)
    assert refused.status == "refused"
    assert refused.error == CYCLE_INTERVAL_EXCEEDS_TIMEFRAME


def test_supervisor_does_not_attach_a_monitor_with_an_invalid_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.workers.paper_worker._build_armed_supervisor",
        lambda *_args: PaperWorkerSupervisor(
            watcher_cycle=lambda: CycleOutcome("disarmed"),
            telegram_cycle=lambda: CycleOutcome("disarmed"),
        ),
    )
    settings = Settings(
        _env_file=None,
        paper_exit_monitor_enabled=True,
        paper_signal_cooldown_after_loss_seconds=0,
    )
    worker = build_paper_worker_supervisor(settings)
    assert worker.snapshot().exit_monitor is None


def test_disabled_monitor_stays_disarmed_when_the_cooldown_is_zero() -> None:
    settings = Settings(
        _env_file=None,
        paper_exit_monitor_enabled=False,
        paper_signal_cooldown_after_loss_seconds=0,
    )
    report = run_configured_exit_monitor(settings)
    assert report.status == "disarmed"
    assert report.error == ""


def test_disarmed_worker_does_not_write_a_database_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _forbidden() -> object:
        raise AssertionError("disarmed worker opened a session")

    monkeypatch.setattr("app.db.session.get_session_factory", _forbidden)
    monkeypatch.setattr(
        "app.services.worker_heartbeat.settings_are_disarmed_paper_worker",
        lambda _settings: True,
    )
    publish_paper_worker_heartbeat(Settings(_env_file=None), now=_NOW, status="disarmed")


def test_worker_cycle_publishes_a_heartbeat_and_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[tuple[str, str]] = []

    def _publish(_factory: object, snapshot: object) -> None:
        published.append((snapshot.component, snapshot.activation_state))  # type: ignore[attr-defined]

    monkeypatch.setattr("app.services.worker_heartbeat.publish_runtime_status", _publish)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: None)
    settings = Settings(_env_file=None)

    def _ok() -> CycleOutcome:
        return CycleOutcome(status="running")

    assert watch_cycle_with_heartbeat(settings, _ok, now=lambda: _NOW)().status == "running"
    assert published[0] == ("paper_worker", "running")
    assert published[-1] == ("paper_worker", "running")

    def _boom() -> CycleOutcome:
        raise RuntimeError("cycle failed")

    with pytest.raises(RuntimeError, match="cycle failed"):
        watch_cycle_with_heartbeat(settings, _boom, now=lambda: _NOW)()
    assert published[-1] == ("paper_worker", "failed")


def test_external_check_reports_missing_and_stale_heartbeats_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_safety_alert_dedup()
    sent: list[str] = []

    def _deliver(self: object, payload: object, *, bypass_enable_check: bool = False) -> object:
        del self, bypass_enable_check
        sent.append(payload.message)  # type: ignore[attr-defined]
        return AlertDeliveryResult(success=True, channel=AlertDeliveryChannel.TELEGRAM)

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _deliver)
    settings = Settings(_env_file=None, telegram_bot_token=_TOKEN, telegram_chat_id=_CHAT)
    ledger = MemoryHeartbeatAlertLedger()
    missing = project_paper_worker_heartbeat(None, now=_NOW, stale_after_seconds=90)
    assert heartbeat_status(missing) == "missing"
    assert raise_heartbeat_alert(settings, missing, now=_NOW, ledger=ledger) is True
    assert raise_heartbeat_alert(settings, missing, now=_NOW, ledger=ledger) is False

    stale_row = SimpleNamespace(
        heartbeat_at=_NOW - timedelta(minutes=10),
        worker_id="watcher-paper-1",
        activation_state="running",
        lease_owner="",
        lease_epoch=0,
        lease_expires_at=None,
        fence_held=False,
        last_scan_at=None,
        last_scan_reason="",
        market_source="paper_worker",
        freshness_seconds=None,
        telegram_runtime_state="absent",
        inbound_mode="off",
        outbox_pending=0,
        outbox_retryable=0,
        outbox_dead_letter=0,
        last_delivery_at=None,
        last_error_code="",
        kill_switch_active=False,
        request_count=0,
        request_weight=0,
        rate_limited_count=0,
        cache_hits=0,
        process_rss_bytes=None,
        process_rss_peak_bytes=None,
    )
    stale = project_paper_worker_heartbeat(stale_row, now=_NOW, stale_after_seconds=90)
    assert heartbeat_status(stale) == "stale"
    assert raise_heartbeat_alert(settings, stale, now=_NOW, ledger=ledger) is True
    fresh = project_paper_worker_heartbeat(
        SimpleNamespace(**{**stale_row.__dict__, "heartbeat_at": _NOW}),
        now=_NOW,
        stale_after_seconds=90,
    )
    assert heartbeat_status(fresh) == "fresh"
    assert heartbeat_alert(fresh) is None
    assert raise_heartbeat_alert(settings, fresh, now=_NOW, ledger=ledger) is False
    blob = "\n".join(sent)
    assert _TOKEN not in blob
    assert _CHAT not in blob
    assert "pnl" not in blob.lower()
    assert "worker_heartbeat_missing" in blob
    assert "worker_heartbeat_stale" in blob


def test_safety_alert_text_drops_secrets_and_pnl_amounts() -> None:
    text = safety_alert_message(
        code="backfill_loss",
        message="A historical backfill close recorded a net loss. net_pnl=-12.50",
        fields={
            "net_loss": "true",
            "net_pnl": "-12.50",
            "telegram_bot_token": _TOKEN,
            "refusal": "kill_switch",
            "dedup_key": "backfill_loss:org:command",
        },
    )
    assert "net_loss: true" in text
    assert "kill_switch" in text
    assert "-12.50" not in text
    assert "net_pnl" not in text
    assert _TOKEN not in text


def test_safety_alerts_use_the_existing_telegram_path_and_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_safety_alert_dedup()
    calls: list[tuple[str, bool, str | None]] = []

    def _deliver(
        self: TelegramAlertDeliveryProvider,
        payload: object,
        *,
        bypass_enable_check: bool = False,
    ) -> AlertDeliveryResult:
        calls.append(
            (
                payload.message,  # type: ignore[attr-defined]
                bypass_enable_check,
                self._settings.telegram_chat_id,
            )
        )
        return AlertDeliveryResult(success=True, channel=AlertDeliveryChannel.TELEGRAM)

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _deliver)
    settings = Settings(_env_file=None, telegram_bot_token=_TOKEN, telegram_chat_id=_CHAT)
    alert = _alert()
    assert deliver_safety_alert(settings, alert, now=_NOW) is True
    assert deliver_safety_alert(settings, alert, now=_NOW + timedelta(seconds=10)) is False
    assert len(calls) == 1
    message, bypass, chat = calls[0]
    assert bypass is True
    assert chat == _CHAT
    assert _TOKEN not in message
    assert _CHAT not in message
    assert "Code: backfill_loss" in message

    session = SimpleNamespace(info={})
    queued = ExitMonitorAlert(
        code="stale_live_loss",
        severity="critical",
        message="A losing close is older than the loss pause or one candle.",
        fields={
            "dedup_key": "stale_live_loss:org:command",
            "refusal": "kill_switch",
            "net_loss": "true",
        },
    )
    queue_safety_alert(session, settings, queued)  # type: ignore[arg-type]
    assert calls == calls[:1]
    deliver_queued_safety_alerts(session)  # type: ignore[arg-type]
    assert len(calls) == 2
    assert "stale_live_loss" in calls[1][0]


def test_exit_monitor_record_queues_telegram_only_for_a_new_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.paper_exit_protection import record_exit_monitor_alert

    created = {"row": object()}

    class _Alerts:
        def __init__(self, _session: object) -> None:
            return None

        def create(self, **_kwargs: object) -> object | None:
            return created["row"]

    class _Audit:
        def __init__(self, _session: object) -> None:
            return None

        def record(self, _payload: object) -> None:
            return None

    monkeypatch.setattr("app.services.paper_exit_protection.PaperAlertService", _Alerts)
    monkeypatch.setattr("app.services.paper_exit_protection.AuditService", _Audit)
    session = SimpleNamespace(info={})
    settings = Settings(_env_file=None, telegram_bot_token=_TOKEN, telegram_chat_id=_CHAT)
    alert = ExitMonitorAlert(
        code="clock_skew",
        severity="critical",
        message="Paper exit monitor refused the cycle because the worker clock does not agree.",
        fields={"dedup_key": "clock:clock_skew", "reason": "clock_skew"},
    )
    assert (
        record_exit_monitor_alert(
            session,  # type: ignore[arg-type]
            alert,
            organization_id=uuid4(),
            user_id=None,
            settings=settings,
        )
        is True
    )
    assert session.info["alphatrade_safety_alerts"][0][1].code == "clock_skew"
    created["row"] = None
    session.info.clear()
    assert (
        record_exit_monitor_alert(
            session,  # type: ignore[arg-type]
            alert,
            organization_id=uuid4(),
            user_id=None,
            settings=settings,
        )
        is False
    )
    assert session.info == {}


def test_degraded_cycle_counter_survives_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _MemoryStreak()
    settings = Settings(
        _env_file=None,
        paper_exit_monitor_enabled=True,
        paper_exit_monitor_degraded_alert_after_seconds=60,
    )
    clock = {"now": _NOW}
    seen: list[str] = []

    def _now() -> datetime:
        return clock["now"]

    def _capture(alert: ExitMonitorAlert) -> None:
        seen.append(alert.code)

    def _degraded(*_args: object, **_kwargs: object) -> MonitorReport:
        return MonitorReport(status="degraded", error="candles_unusable")

    monkeypatch.setattr(
        "app.workers.paper_exit_monitor.run_configured_exit_monitor",
        _degraded,
    )
    first = PaperExitMonitorCycle(
        settings,
        clock=_now,
        alert_sink=_capture,
        streak_store=store,
    )
    assert first().status == "degraded"
    assert seen == []
    assert store.streak is not None
    assert store.streak.cycles == 1

    clock["now"] = _NOW + timedelta(seconds=61)
    second = PaperExitMonitorCycle(
        settings,
        clock=_now,
        alert_sink=_capture,
        streak_store=store,
    )
    assert second().status == "degraded"
    assert seen == ["degraded_repeat"]
    assert store.streak is not None
    assert store.streak.cycles == 2
    assert store.streak.last_alert_at == clock["now"]

    def _idle(*_args: object, **_kwargs: object) -> MonitorReport:
        return MonitorReport(status="idle")

    monkeypatch.setattr("app.workers.paper_exit_monitor.run_configured_exit_monitor", _idle)
    assert second().status == "running"
    assert store.streak is None


def test_postgres_streak_store_round_trips_without_pnl() -> None:
    rows = _Rows()

    def factory() -> _Session:
        return _Session(rows)

    store = PostgresDegradationStreakStore(factory)  # type: ignore[arg-type]
    started = _NOW - timedelta(minutes=20)
    store.save(
        DegradationStreak(started_at=started, cycles=4, last_alert_at=_NOW),
        observed_at=_NOW,
    )
    loaded = store.load()
    assert loaded == DegradationStreak(started_at=started, cycles=4, last_alert_at=_NOW)
    row = rows.rows[PAPER_EXIT_DEGRADATION_COMPONENT]
    assert "pnl" not in row.market_source
    assert streak_from_row(None) is None
    store.save(None, observed_at=_NOW)
    assert store.load() is None
    assert PAPER_EXIT_DEGRADATION_COMPONENT not in rows.rows


def test_paper_worker_health_route_reports_a_missing_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import BackgroundTasks

    from app.api.routes.health import _read_worker_runtime, paper_worker_heartbeat

    def _down() -> object:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("app.db.session.get_session_factory", _down)
    settings = Settings(_env_file=None)
    runtime = _read_worker_runtime(settings, now=_NOW)
    assert runtime.available is False
    assert runtime.paper_worker.health_state == "UNAVAILABLE"
    report = asyncio.run(paper_worker_heartbeat(settings, BackgroundTasks()))
    assert report.status == "missing"
    assert report.alert_raised is False
    assert report.observation.health_state == "UNAVAILABLE"
    rendered = report.model_dump_json()
    assert "telegram_bot_token" not in rendered
    assert _TOKEN not in rendered
    assert settings.real_trading_enabled is False
