"""Arming refusal visibility, exit-monitor liveness, and kill-switch pages.

Flags stay off. Real-money trading stays blocked. These tests do not arm a
worker and do not send a live Telegram message.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, StaticPool
from structlog.testing import capture_logs

from app.core.config import Settings
from app.db.base import Base
from app.db.models import KillSwitchState, Organization, User
from app.db.runtime_status import (
    CONTROLLED_RUNTIME_COMPONENT_LENGTH,
    ControlledRuntimeStatusRow,
)
from app.persistence.runtime_status import (
    PAPER_ARMING_COMPONENT,
    PAPER_EXIT_MONITOR_COMPONENT,
    PAPER_KILL_SWITCH_COMPONENT,
    PAPER_WORKER_COMPONENT,
    RuntimeStatusWrite,
    load_runtime_rows,
    paper_kill_switch_component,
    paper_kill_switch_truncated_component,
    publish_runtime_status,
)
from app.providers.alert_delivery.base import AlertDeliveryPayload, AlertDeliveryResult
from app.providers.alert_delivery.telegram import TelegramAlertDeliveryProvider
from app.schemas.common import AlertDeliveryChannel
from app.schemas.health import PaperWorkerHeartbeatResponse
from app.schemas.risk import KillSwitchMutationRequest
from app.security.passwords import hash_password
from app.services.arming_gates import COOLDOWN_INVALID, CYCLE_INTERVAL_EXCEEDS_TIMEFRAME
from app.services.arming_visibility import describe_boot_arming, publish_boot_arming_posture
from app.services.audit_service import AuditService
from app.services.autonomous_trading import AutonomousMode, AutonomousModeResolution
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.risk.kill_switch import (
    KillSwitchService,
    kill_switch_retry_should_schedule,
    reset_kill_switch_retry_window,
    retry_unsent_kill_switch_alert,
)
from app.services.safety_alerts import (
    deliver_observed_alert_outside_transaction,
    notify_kill_switch_activation,
    queue_safety_alert,
    reset_safety_alert_dedup,
    safety_alert_message,
)
from app.services.worker_heartbeat import (
    MemoryHeartbeatAlertLedger,
    exit_monitor_heartbeat_alert,
    exit_monitor_liveness_status,
    publish_exit_monitor_heartbeat,
    raise_prepared_alert,
)
from app.workers.paper_worker import (
    CycleOutcome,
    PaperWorkerSupervisor,
    build_paper_worker_supervisor,
    run_paper_worker_process,
)

_NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
_TOKEN = "123456:TESTTOKENVALUENOTASECRET1234567890"
_CHAT = "424242"
_ORG = uuid.UUID("00000000-0000-0000-0000-0000000000d1")
_USER = uuid.UUID("00000000-0000-0000-0000-0000000000d2")


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "telegram_bot_token": _TOKEN,
        "telegram_chat_id": _CHAT,
    }
    values.update(updates)
    return Settings(**values)  # type: ignore[arg-type]


def _run_heartbeat(settings: Settings) -> PaperWorkerHeartbeatResponse:
    from app.api.routes.health import paper_worker_heartbeat

    return asyncio.run(paper_worker_heartbeat(settings, BackgroundTasks()))


def _capture_telegram(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    sent: list[str] = []

    def _deliver(
        self: TelegramAlertDeliveryProvider,
        payload: object,
        *,
        bypass_enable_check: bool = False,
    ) -> AlertDeliveryResult:
        del self, bypass_enable_check
        sent.append(payload.message)  # type: ignore[attr-defined]
        return AlertDeliveryResult(success=True, channel=AlertDeliveryChannel.TELEGRAM)

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _deliver)
    return sent


def _arming_row(*, reason: str, source: str, state: str = "refused") -> object:
    return type(
        "Row",
        (),
        {
            "activation_state": state,
            "last_error_code": reason,
            "last_scan_reason": source,
        },
    )()


class _Rows:
    def __init__(self) -> None:
        self.rows: dict[str, object] = {}


class _Session:
    def __init__(self, rows: _Rows) -> None:
        self._rows = rows
        self.info: dict[str, object] = {}

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def begin(self) -> _Session:
        return self

    def get(self, _model: object, key: str) -> object | None:
        return self._rows.rows.get(key)

    def add(self, row: object) -> None:
        component = getattr(row, "component", "")
        self._rows.rows[str(component)] = row

    def delete(self, row: object) -> None:
        component = getattr(row, "component", "")
        self._rows.rows.pop(str(component), None)


def test_flags_stay_off() -> None:
    settings = Settings(_env_file=None)
    assert settings.paper_exit_monitor_enabled is False
    assert settings.autonomous_internal_paper_enabled is False
    assert settings.autonomous_blofin_demo_enabled is False
    assert settings.real_trading_enabled is False
    assert settings.enable_real_trading is False


def test_boot_records_a_cooldown_refusal_and_sends_one_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_safety_alert_dedup()
    sent = _capture_telegram(monkeypatch)
    published: list[tuple[str, str, str]] = []

    def _publish(_factory: object, snapshot: RuntimeStatusWrite) -> None:
        published.append((snapshot.component, snapshot.activation_state, snapshot.last_error_code))

    monkeypatch.setattr("app.services.arming_visibility.publish_runtime_status", _publish)
    monkeypatch.setattr("app.services.worker_heartbeat.publish_runtime_status", _publish)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _Session(_Rows()))
    settings = _settings(
        paper_exit_monitor_enabled=True,
        autonomous_internal_paper_enabled=True,
        paper_signal_cooldown_after_loss_seconds=0,
    )
    posture = publish_boot_arming_posture(
        settings,
        exit_monitor_attached=False,
        now=_NOW,
    )
    assert posture is not None
    assert posture.reason == COOLDOWN_INVALID
    assert posture.source == "autonomous+exit_monitor"
    assert published[0] == (PAPER_ARMING_COMPONENT, "refused", COOLDOWN_INVALID)
    assert len(sent) == 1
    publish_boot_arming_posture(settings, exit_monitor_attached=False, now=_NOW)
    assert len(sent) == 1
    text = sent[0]
    assert "arming_refused" in text
    assert COOLDOWN_INVALID in text
    assert _TOKEN not in text
    assert _CHAT not in text
    assert "pnl" not in text.lower()


def test_autonomous_interval_refusal_is_separate_from_the_exit_monitor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_safety_alert_dedup()
    sent = _capture_telegram(monkeypatch)
    monkeypatch.setattr(
        "app.services.arming_visibility.publish_runtime_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.worker_heartbeat.publish_runtime_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _Session(_Rows()))
    settings = _settings(
        autonomous_internal_paper_enabled=True,
        watcher_paper_poll_interval_seconds=120,
        paper_signal_allowed_timeframes=["1m"],
    )
    posture = describe_boot_arming(settings, exit_monitor_attached=False)
    assert posture.reason == CYCLE_INTERVAL_EXCEEDS_TIMEFRAME
    assert posture.source == "autonomous"
    assert publish_boot_arming_posture(settings, exit_monitor_attached=False, now=_NOW) is not None
    assert len(sent) == 1
    assert CYCLE_INTERVAL_EXCEEDS_TIMEFRAME in sent[0]


def test_disarmed_boot_does_not_open_a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden() -> None:
        raise AssertionError("disarmed boot opened a session")

    monkeypatch.setattr(
        "app.core.disarmed_worker_boot.settings_are_disarmed_paper_worker",
        lambda _settings: True,
    )
    monkeypatch.setattr("app.db.session.get_session_factory", _forbidden)
    assert (
        publish_boot_arming_posture(
            _settings(paper_exit_monitor_enabled=True),
            exit_monitor_attached=False,
        )
        is None
    )


def test_supervisor_refusal_does_not_attach_or_open_a_session_when_disarmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.workers.paper_worker._build_armed_supervisor",
        lambda *_args: PaperWorkerSupervisor(
            watcher_cycle=lambda: CycleOutcome("disarmed"),
            telegram_cycle=lambda: CycleOutcome("disarmed"),
        ),
    )
    monkeypatch.setattr(
        "app.core.disarmed_worker_boot.settings_are_disarmed_paper_worker",
        lambda _settings: True,
    )

    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("disarmed supervisor published arming")

    monkeypatch.setattr("app.services.arming_visibility.publish_boot_arming_posture", _forbidden)
    worker = build_paper_worker_supervisor(
        Settings(
            _env_file=None,
            paper_exit_monitor_enabled=True,
            paper_signal_cooldown_after_loss_seconds=0,
        )
    )
    assert worker.snapshot().exit_monitor is None


def test_health_shows_the_refusal_and_pages_once(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_safety_alert_dedup()
    sent = _capture_telegram(monkeypatch)
    from app.api.routes.health import health

    row = _arming_row(reason=COOLDOWN_INVALID, source="exit_monitor")
    rows = {PAPER_ARMING_COMPONENT: row, PAPER_WORKER_COMPONENT: None}

    monkeypatch.setattr("app.api.routes.health._load_runtime_rows", lambda: rows)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _Session(_Rows()))
    settings = _settings()
    body = asyncio.run(health(settings))
    assert body.arming_refusal.refused is True
    assert body.arming_refusal.reason == COOLDOWN_INVALID
    assert body.arming_refusal.source == "exit_monitor"
    assert body.real_trading_enabled is False
    report = _run_heartbeat(settings)
    assert report.arming_refusal.reason == COOLDOWN_INVALID
    assert report.arming_alert_raised is True
    assert report.exit_monitor_status == "disabled"
    again = _run_heartbeat(settings)
    assert again.arming_alert_raised is False
    blob = "\n".join(sent)
    assert _TOKEN not in blob
    assert "pnl" not in blob.lower()
    rendered = report.model_dump_json()
    assert _TOKEN not in rendered
    assert _CHAT not in rendered


def test_exit_monitor_thread_is_stale_while_the_watcher_heartbeat_is_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_safety_alert_dedup()
    sent = _capture_telegram(monkeypatch)

    moment = datetime.now(UTC)
    fresh = _heartbeat_row(at=moment, state="running")
    stale = _heartbeat_row(at=moment - timedelta(minutes=10), state="running")
    armed = _arming_row(reason="", source="exit_monitor", state="armed")
    rows = {
        PAPER_WORKER_COMPONENT: fresh,
        PAPER_ARMING_COMPONENT: armed,
        PAPER_EXIT_MONITOR_COMPONENT: stale,
    }
    monkeypatch.setattr("app.api.routes.health._load_runtime_rows", lambda: rows)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _Session(_Rows()))
    settings = _settings()
    report = _run_heartbeat(settings)
    assert report.status == "fresh"
    assert report.alert_raised is False
    assert report.exit_monitor_status == "stale"
    assert report.exit_monitor_alert_raised is True
    assert "exit_monitor_heartbeat_stale" in sent[0]
    assert _TOKEN not in sent[0]
    missing_rows = {
        PAPER_WORKER_COMPONENT: fresh,
        PAPER_ARMING_COMPONENT: armed,
    }
    monkeypatch.setattr("app.api.routes.health._load_runtime_rows", lambda: missing_rows)
    missing = _run_heartbeat(settings)
    assert missing.exit_monitor_status == "missing"
    assert missing.exit_monitor_alert_raised is True
    quiet = _arming_row(reason="", source="", state="clear")
    monkeypatch.setattr(
        "app.api.routes.health._load_runtime_rows",
        lambda: {PAPER_ARMING_COMPONENT: quiet, PAPER_EXIT_MONITOR_COMPONENT: stale},
    )
    disabled = _run_heartbeat(settings)
    assert disabled.exit_monitor_status == "disabled"
    assert disabled.exit_monitor_alert_raised is False


def test_exit_monitor_heartbeat_is_not_the_watcher_row(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_safety_alert_dedup()
    sent = _capture_telegram(monkeypatch)
    published: list[str] = []

    def _publish(_factory: object, snapshot: RuntimeStatusWrite) -> None:
        published.append(snapshot.component)

    monkeypatch.setattr("app.services.worker_heartbeat.publish_runtime_status", _publish)
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _Session(_Rows()))
    settings = _settings(paper_exit_monitor_enabled=True)
    publish_exit_monitor_heartbeat(settings, now=_NOW, status="running")
    assert published == [PAPER_EXIT_MONITOR_COMPONENT]
    status, _observation = exit_monitor_liveness_status(
        expected=True,
        row=None,
        now=_NOW,
        stale_after_seconds=90,
    )
    assert status == "missing"
    alert = exit_monitor_heartbeat_alert(_observation, status)
    assert alert is not None
    assert raise_prepared_alert(
        settings,
        alert,
        now=_NOW,
        ledger=MemoryHeartbeatAlertLedger(),
    )
    assert sent
    assert "exit_monitor_heartbeat_missing" in sent[0]
    assert _TOKEN not in sent[0]


def test_observed_alert_is_sent_when_the_database_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_safety_alert_dedup()
    sent = _capture_telegram(monkeypatch)
    settings = _settings()
    alert = ExitMonitorAlert(
        code="degraded_repeat",
        severity="critical",
        message="Paper exit monitor kept failing.",
        fields={"dedup_key": "degraded_repeat:boot", "reason": "candles_unusable"},
    )
    session = MagicMock()
    session.info = {}
    queue_safety_alert(session, settings, alert)
    with capture_logs() as logs:
        session.rollback()
        from app.services.safety_alerts import discard_queued_safety_alerts

        discard_queued_safety_alerts(session)
        assert deliver_observed_alert_outside_transaction(settings, alert) is True
    assert any(entry["event"] == "safety_alert_unpersisted" for entry in logs)
    assert any(entry["event"] == "safety_alert_discarded_with_rollback" for entry in logs)
    assert len(sent) == 1
    assert "degraded_repeat" in sent[0]
    assert "pnl" not in sent[0].lower()


def test_kill_switch_page_drops_secrets_and_amounts() -> None:
    text = safety_alert_message(
        code="kill_switch_activated",
        message="Kill switch activated. net_pnl=-42.00 telegram_bot_token=" + _TOKEN,
        fields={
            "source": "manual",
            "organization_id": str(_ORG),
            "reason": "net_pnl=-42.00",
            "telegram_bot_token": _TOKEN,
            "dedup_key": f"kill_switch_activated:manual:{_ORG}:2",
            "refusal": "kill_switch",
        },
    )
    assert "source: manual" in text
    assert "-42.00" not in text
    assert _TOKEN not in text
    assert "net_pnl" not in text


def test_global_and_fail_closed_pages_are_deduped(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_safety_alert_dedup()
    sent = _capture_telegram(monkeypatch)
    settings = _settings(global_kill_switch_active=True)
    assert (
        notify_kill_switch_activation(
            settings,
            source="global_setting",
            organization_id="system",
            now=_NOW,
        )
        is True
    )
    assert (
        notify_kill_switch_activation(
            settings,
            source="global_setting",
            organization_id="system",
            now=_NOW,
        )
        is False
    )
    service = KillSwitchService(MagicMock(), MagicMock(), settings)
    service.get_status = MagicMock(side_effect=RuntimeError("postgres password=secret down"))  # type: ignore[method-assign]
    evaluation = service.evaluate(organization_id=_ORG)
    assert evaluation.reason_code == "kill_switch_unavailable"
    assert evaluation.blocked is True
    blob = "\n".join(sent)
    assert "global_setting" in blob
    assert "fail_closed" in blob
    assert "secret" not in blob
    assert "password" not in blob
    assert _TOKEN not in blob
    assert "pnl" not in blob.lower()


@pytest.fixture
def _kill_db() -> Iterator[tuple[sessionmaker[Session], Settings]]:
    reset_kill_switch_retry_window()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = _settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        exchange_mode="paper_internal",
        provider_mode="mock",
        market_data_provider="mock",
        global_kill_switch_active=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="arming-visibility-secret-32-bytes-min",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
    )
    with factory() as session:
        session.add(Organization(id=_ORG, name="Arming Org"))
        session.add(
            User(
                id=_USER,
                email="owner@test.example",
                hashed_password=hash_password("TestPassword123!", settings),
            )
        )
        session.commit()
    yield factory, settings
    reset_kill_switch_retry_window()
    engine.dispose()


def test_manual_and_drawdown_activations_page_after_commit(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_safety_alert_dedup()
    sent = _capture_telegram(monkeypatch)
    factory, settings = _kill_db
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), settings)
        service.activate(
            organization_id=_ORG,
            actor_user_id=_USER,
            payload=KillSwitchMutationRequest(
                confirm=True,
                reason="operator note net_pnl=-99.50",
            ),
            source="manual",
        )
        assert sent == []
        session.commit()
    assert len(sent) == 1
    assert "source: manual" in sent[0]
    assert "-99.50" not in sent[0]
    assert _TOKEN not in sent[0]
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), settings)
        service.activate(
            organization_id=_ORG,
            actor_user_id=_USER,
            payload=KillSwitchMutationRequest(confirm=True, reason="again"),
            source="drawdown",
        )
        session.commit()
    assert len(sent) == 1
    with factory() as session:
        with capture_logs() as logs:
            service = KillSwitchService(session, AuditService(session), settings)
            service.deactivate(
                organization_id=_ORG,
                actor_user_id=_USER,
                payload=KillSwitchMutationRequest(confirm=True, reason="clear"),
            )
            session.commit()
            service.activate(
                organization_id=_ORG,
                actor_user_id=_USER,
                payload=KillSwitchMutationRequest(confirm=True, reason="drawdown latch"),
                source="drawdown",
            )
            session.rollback()
        assert any(entry["event"] == "safety_alert_discarded_with_rollback" for entry in logs)
    assert len(sent) == 1
    assert all("drawdown" not in message for message in sent)


def test_attached_exit_monitor_stays_expected_when_the_autonomous_loop_refuses(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    """Loop refused, monitor attached, then the monitor heartbeat goes stale."""

    reset_safety_alert_dedup()
    sent = _capture_telegram(monkeypatch)
    factory, base = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    monkeypatch.setattr(
        "app.services.arming_visibility.resolve_autonomous_mode",
        lambda _settings: AutonomousModeResolution(
            AutonomousMode.OFF,
            CYCLE_INTERVAL_EXCEEDS_TIMEFRAME,
        ),
    )
    settings = base.model_copy(
        update={
            "paper_exit_monitor_enabled": True,
            "autonomous_internal_paper_enabled": True,
            "watcher_paper_poll_interval_seconds": 5,
            "paper_signal_allowed_timeframes": ["1m"],
            "watcher_heartbeat_stale_after_seconds": 30,
            "paper_signal_cooldown_after_loss_seconds": 3600,
        }
    )
    published_at = datetime.now(UTC)
    posture = publish_boot_arming_posture(
        settings,
        exit_monitor_attached=True,
        now=published_at,
    )
    assert posture is not None
    assert posture.refused is True
    assert posture.reason == CYCLE_INTERVAL_EXCEEDS_TIMEFRAME
    assert posture.exit_monitor_attached is True
    assert "autonomous" in posture.source
    with factory() as session:
        rows = load_runtime_rows(session)
        arming = rows[PAPER_ARMING_COMPONENT]
        assert arming.activation_state == "refused"
        assert "monitor_running" in arming.last_scan_reason
        monitor = rows[PAPER_EXIT_MONITOR_COMPONENT]
        assert monitor.heartbeat_at is not None
    stale_at = published_at - timedelta(minutes=10)
    with factory() as session, session.begin():
        row = session.get(ControlledRuntimeStatusRow, PAPER_EXIT_MONITOR_COMPONENT)
        assert row is not None
        row.heartbeat_at = stale_at

    report = _run_heartbeat(settings)
    assert report.arming_refusal.refused is True
    assert report.arming_refusal.reason == CYCLE_INTERVAL_EXCEEDS_TIMEFRAME
    assert report.exit_monitor_status == "stale"
    assert report.exit_monitor_alert_raised is True
    assert any("exit_monitor_heartbeat_stale" in message for message in sent)
    blob = "\n".join(sent)
    assert _TOKEN not in blob
    assert _CHAT not in blob
    assert "pnl" not in blob.lower()
    rendered = report.model_dump_json()
    assert _TOKEN not in rendered
    assert "pnl" not in rendered.lower()


def test_unattached_monitor_heartbeat_is_deleted_when_the_loop_refuses(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_safety_alert_dedup()
    _capture_telegram(monkeypatch)
    factory, base = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    settings = base.model_copy(
        update={
            "paper_exit_monitor_enabled": True,
            "autonomous_internal_paper_enabled": True,
            "paper_signal_cooldown_after_loss_seconds": 0,
        }
    )
    moment = datetime.now(UTC)
    publish_exit_monitor_heartbeat(settings, now=moment, status="running", session_factory=factory)
    posture = publish_boot_arming_posture(settings, exit_monitor_attached=False, now=moment)
    assert posture is not None
    assert posture.refused is True
    assert posture.exit_monitor_attached is False
    with factory() as session:
        rows = load_runtime_rows(session)
        assert PAPER_EXIT_MONITOR_COMPONENT not in rows
        assert rows[PAPER_ARMING_COMPONENT].activation_state == "refused"

    def _rows() -> dict[str, object]:
        with factory() as session:
            return dict(load_runtime_rows(session))

    monkeypatch.setattr("app.api.routes.health._load_runtime_rows", _rows)
    report = _run_heartbeat(settings)
    assert report.exit_monitor_status == "disabled"
    assert report.exit_monitor_alert_raised is False


def test_telegram_http_send_timeout_is_at_most_five_seconds() -> None:
    from app.providers.alert_delivery.telegram import telegram_http_send_timeout_seconds
    from app.telegram_activation.transport import HttpTelegramTransport

    assert telegram_http_send_timeout_seconds(5) <= 5
    assert telegram_http_send_timeout_seconds(30) == 5.0
    assert telegram_http_send_timeout_seconds(1) == 1.0
    captured: dict[str, float] = {}

    def _post(url: str, *, json: object, timeout: float) -> MagicMock:
        del url, json
        captured["timeout"] = float(timeout)
        response = MagicMock()
        response.status_code = 200
        return response

    payload = AlertDeliveryPayload(
        alert_id="timeout-check",
        organization_id="system",
        alert_type="kill_switch_activated",
        severity="critical",
        message="Kill switch activated.",
    )
    high = _settings(telegram_timeout_seconds=30)
    TelegramAlertDeliveryProvider(high, http_post=_post).deliver(
        payload,
        bypass_enable_check=True,
    )
    assert captured["timeout"] <= 5
    TelegramAlertDeliveryProvider(_settings(), http_post=_post).deliver(
        payload,
        bypass_enable_check=True,
    )
    assert captured["timeout"] <= 5
    transport = HttpTelegramTransport(
        token=_TOKEN,
        timeout_seconds=30,
        network_permitted=True,
        http_post=_post,
    )
    transport.send_private_message(
        bot_id="bot",
        chat_id=_CHAT,
        text="ping",
        idempotency_key="timeout-check",
    )
    assert captured["timeout"] <= 5


def test_global_and_fail_closed_pages_leave_the_open_transaction(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_safety_alert_dedup()
    sent = _capture_telegram(monkeypatch)
    factory, base = _kill_db
    settings = base.model_copy(update={"global_kill_switch_active": True})
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), settings)
        with session.begin():
            evaluation = service.evaluate(organization_id=_ORG)
            assert evaluation.reason_code == "global_kill_switch_active"
            assert evaluation.blocked is True
            assert sent == []
            assert session.in_transaction() is True
        assert session.in_transaction() is False
        assert any("global_setting" in message for message in sent)
    reset_safety_alert_dedup()
    sent.clear()
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), base)
        service.get_status = MagicMock(side_effect=RuntimeError("postgres password=secret down"))  # type: ignore[method-assign]
        try:
            with session.begin():
                evaluation = service.evaluate(organization_id=_ORG)
                assert evaluation.reason_code == "kill_switch_unavailable"
                assert sent == []
                raise RuntimeError("abort the transaction")
        except RuntimeError as exc:
            assert str(exc) == "abort the transaction"
        blob = "\n".join(sent)
        assert "fail_closed" in blob
        assert "secret" not in blob
        assert "password" not in blob
        assert _TOKEN not in blob
        assert "pnl" not in blob.lower()


def test_committed_kill_switch_activation_logs_a_failed_telegram_send_at_error(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_safety_alert_dedup()

    def _deliver(
        self: TelegramAlertDeliveryProvider,
        payload: object,
        *,
        bypass_enable_check: bool = False,
    ) -> AlertDeliveryResult:
        del self, payload, bypass_enable_check
        return AlertDeliveryResult(
            success=False,
            channel=AlertDeliveryChannel.TELEGRAM,
            error="http 500",
        )

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _deliver)
    factory, settings = _kill_db
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), settings)
        service.activate(
            organization_id=_ORG,
            actor_user_id=_USER,
            payload=KillSwitchMutationRequest(
                confirm=True,
                reason="operator note net_pnl=-1.25",
            ),
            source="drawdown",
        )
        with capture_logs() as logs:
            session.commit()
    failed = [
        entry
        for entry in logs
        if entry.get("event") == "safety_alert_telegram_failed"
        and entry.get("log_level") == "error"
    ]
    assert failed
    blob = repr(logs)
    assert _TOKEN not in blob
    assert "-1.25" not in blob
    assert "net_pnl" not in blob.lower()


def test_paper_worker_health_reports_kill_switch_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_safety_alert_dedup()
    _capture_telegram(monkeypatch)
    factory, settings = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)

    idle = _run_heartbeat(settings)
    assert idle.kill_switch.active is False
    assert idle.kill_switch.source == ""
    assert idle.kill_switch.since is None
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), settings)
        service.activate(
            organization_id=_ORG,
            actor_user_id=_USER,
            payload=KillSwitchMutationRequest(
                confirm=True,
                reason="operator note net_pnl=-99.50",
            ),
            source="manual",
        )
        session.commit()
    report = _run_heartbeat(settings)
    assert report.kill_switch.active is True
    assert report.kill_switch.source == "manual"
    assert report.kill_switch.since is not None
    rendered = report.model_dump_json()
    assert "net_pnl" not in rendered.lower()
    assert "-99.50" not in rendered
    assert "operator note" not in rendered
    assert _TOKEN not in rendered
    assert _CHAT not in rendered
    global_settings = settings.model_copy(update={"global_kill_switch_active": True})
    covered = _run_heartbeat(global_settings)
    assert covered.kill_switch.active is True
    assert covered.kill_switch.source == "global_setting"
    assert covered.kill_switch.since is None


def _heartbeat_row(*, at: datetime, state: str) -> object:
    return type(
        "Beat",
        (),
        {
            "heartbeat_at": at,
            "worker_id": "watcher-paper-1",
            "activation_state": state,
            "lease_owner": "",
            "lease_epoch": 0,
            "lease_expires_at": None,
            "fence_held": False,
            "last_scan_at": None,
            "last_scan_reason": "",
            "market_source": "paper_worker",
            "freshness_seconds": None,
            "telegram_runtime_state": "absent",
            "inbound_mode": "off",
            "outbox_pending": 0,
            "outbox_retryable": 0,
            "outbox_dead_letter": 0,
            "last_delivery_at": None,
            "last_error_code": "",
            "kill_switch_active": False,
            "request_count": 0,
            "request_weight": 0,
            "rate_limited_count": 0,
            "cache_hits": 0,
            "process_rss_bytes": None,
            "process_rss_peak_bytes": None,
        },
    )()


def _fresh_worker_heartbeat(factory: sessionmaker[Session]) -> None:
    publish_runtime_status(
        factory,
        RuntimeStatusWrite(
            component=PAPER_WORKER_COMPONENT,
            worker_id="watcher-paper-1",
            heartbeat_at=datetime.now(UTC),
            activation_state="running",
            market_source="paper_worker",
        ),
    )


def test_disarmed_reboot_clears_an_armed_monitor_and_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    """Armed boot, then a disarmed reboot, leaves no stale page and no refusal."""

    reset_safety_alert_dedup()
    sent = _capture_telegram(monkeypatch)
    factory, base = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    settings = base.model_copy(
        update={
            "paper_exit_monitor_enabled": True,
            "watcher_paper_poll_interval_seconds": 5,
            "paper_signal_allowed_timeframes": ["1m"],
            "watcher_heartbeat_stale_after_seconds": 30,
            "paper_signal_cooldown_after_loss_seconds": 3600,
        }
    )
    posture = publish_boot_arming_posture(
        settings,
        exit_monitor_attached=True,
        now=datetime.now(UTC) - timedelta(hours=1),
    )
    assert posture is not None
    assert posture.refused is False
    with factory() as session:
        heartbeat = session.get(ControlledRuntimeStatusRow, PAPER_EXIT_MONITOR_COMPONENT)
        assert heartbeat is not None
        status, _observation = exit_monitor_liveness_status(
            expected=True,
            row=heartbeat,
            now=datetime.now(UTC),
            stale_after_seconds=30,
        )
    assert status == "stale"

    monkeypatch.setattr(
        "app.core.disarmed_worker_boot.settings_are_disarmed_paper_worker",
        lambda _settings: True,
    )
    monkeypatch.setattr(
        "app.core.disarmed_worker_boot.load_worker_process_settings",
        lambda _role: settings,
    )
    assert run_paper_worker_process(once=True) == "disarmed"
    with factory() as session:
        assert session.get(ControlledRuntimeStatusRow, PAPER_ARMING_COMPONENT) is None
        assert session.get(ControlledRuntimeStatusRow, PAPER_EXIT_MONITOR_COMPONENT) is None

    from app.api.routes.health import health

    sent.clear()
    _fresh_worker_heartbeat(factory)
    body = asyncio.run(health(settings))
    report = _run_heartbeat(settings)
    assert body.arming_refusal.refused is False
    assert report.arming_refusal.refused is False
    assert report.exit_monitor_status == "disabled"
    assert report.exit_monitor_alert_raised is False
    assert all("exit monitor heartbeat" not in message.lower() for message in sent)

    monkeypatch.setattr(
        "app.core.disarmed_worker_boot.settings_are_disarmed_paper_worker",
        lambda _settings: False,
    )
    refused = _settings(
        paper_exit_monitor_enabled=True,
        paper_signal_cooldown_after_loss_seconds=0,
        telegram_bot_token=base.telegram_bot_token,
        telegram_chat_id=base.telegram_chat_id,
    )
    refused_posture = publish_boot_arming_posture(
        refused,
        exit_monitor_attached=False,
        now=datetime.now(UTC),
    )
    assert refused_posture is not None
    assert refused_posture.refused is True
    monkeypatch.setattr(
        "app.core.disarmed_worker_boot.settings_are_disarmed_paper_worker",
        lambda _settings: True,
    )
    monkeypatch.setattr(
        "app.core.disarmed_worker_boot.load_worker_process_settings",
        lambda _role: settings,
    )
    assert run_paper_worker_process(once=True) == "disarmed"
    sent.clear()
    _fresh_worker_heartbeat(factory)
    body = asyncio.run(health(settings))
    report = _run_heartbeat(settings)
    assert body.arming_refusal.refused is False
    assert report.exit_monitor_status == "disabled"
    assert report.exit_monitor_alert_raised is False
    assert all("exit monitor heartbeat" not in message.lower() for message in sent)


def test_worker_boot_clears_disarmed_rows_and_retries_unsent_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleared: list[bool] = []
    retried: list[bool] = []
    monkeypatch.setattr(
        "app.services.arming_visibility.clear_disarmed_boot_rows",
        lambda: cleared.append(True),
    )
    monkeypatch.setattr(
        "app.services.risk.kill_switch.retry_unsent_kill_switch_alert",
        lambda _settings: retried.append(True) or False,
    )
    monkeypatch.setattr(
        "app.core.disarmed_worker_boot.load_worker_process_settings",
        lambda _role: _settings(),
    )
    monkeypatch.setattr(
        "app.core.disarmed_worker_boot.settings_are_disarmed_paper_worker",
        lambda _settings: True,
    )
    assert run_paper_worker_process(once=True) == "disarmed"
    assert cleared == [True]
    assert retried == [True]


def test_failed_kill_switch_page_retries_once_per_version(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_safety_alert_dedup()

    def _fail(
        self: TelegramAlertDeliveryProvider,
        payload: object,
        *,
        bypass_enable_check: bool = False,
    ) -> AlertDeliveryResult:
        del self, payload, bypass_enable_check
        return AlertDeliveryResult(
            success=False,
            channel=AlertDeliveryChannel.TELEGRAM,
            error="http 500",
        )

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _fail)
    factory, settings = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), settings)
        service.activate(
            organization_id=_ORG,
            actor_user_id=_USER,
            payload=KillSwitchMutationRequest(confirm=True, reason="operator note"),
            source="drawdown",
        )
        session.commit()
        slot = paper_kill_switch_component(_ORG)
        row = session.get(ControlledRuntimeStatusRow, slot)
        assert row is not None
        assert session.get(ControlledRuntimeStatusRow, PAPER_KILL_SWITCH_COMPONENT) is None
        version = int(row.lease_epoch)
        assert row.last_error_code == "unsent"
        assert version >= 1

    sent = _capture_telegram(monkeypatch)
    _fresh_worker_heartbeat(factory)
    from app.api.routes.health import paper_worker_heartbeat

    tasks = BackgroundTasks()
    report = asyncio.run(paper_worker_heartbeat(settings, tasks))
    assert report.kill_switch.active is True
    assert report.kill_switch.source == "drawdown"
    page = "Kill switch activated. New entries stay blocked until a human deactivates it."
    assert page not in "\n".join(sent)
    asyncio.run(tasks())
    assert sum(page in message for message in sent) == 1
    with factory() as session:
        row = session.get(ControlledRuntimeStatusRow, slot)
        assert row is not None
        assert row.last_error_code == "sent"
        assert int(row.lease_epoch) == version

    reset_safety_alert_dedup()
    sent.clear()
    again_tasks = BackgroundTasks()
    again = asyncio.run(paper_worker_heartbeat(settings, again_tasks))
    asyncio.run(again_tasks())
    assert again.kill_switch.active is True
    assert all(page not in message for message in sent)


def test_crash_before_kill_switch_send_retries_on_boot(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_safety_alert_dedup()
    monkeypatch.setattr(
        "app.services.safety_alerts.deliver_queued_safety_alerts",
        lambda _session: None,
    )
    factory, settings = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), settings)
        service.activate(
            organization_id=_ORG,
            actor_user_id=_USER,
            payload=KillSwitchMutationRequest(confirm=True, reason="operator note"),
            source="manual",
        )
        session.commit()
    sent = _capture_telegram(monkeypatch)
    assert retry_unsent_kill_switch_alert(settings) is True
    page = "Kill switch activated. New entries stay blocked until a human deactivates it."
    assert sent and page in sent[0]
    assert "operator note" not in sent[0]
    reset_safety_alert_dedup()
    sent.clear()
    assert retry_unsent_kill_switch_alert(settings) is False
    assert sent == []


def test_failed_bookkeeping_write_leaves_the_activation_committed(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    """A bookkeeping IntegrityError must not roll back the kill switch."""

    reset_safety_alert_dedup()
    _capture_telegram(monkeypatch)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise IntegrityError("INSERT", {}, Exception("duplicate component"))

    monkeypatch.setattr("app.services.risk.kill_switch._upsert_kill_switch_posture", _boom)
    factory, settings = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), settings)
        service.activate(
            organization_id=_ORG,
            actor_user_id=_USER,
            payload=KillSwitchMutationRequest(confirm=True, reason="operator note"),
            source="manual",
        )
        session.commit()
    with factory() as session:
        state = session.scalar(
            select(KillSwitchState).where(KillSwitchState.organization_id == _ORG)
        )
        assert state is not None
        assert state.active is True
        assert session.get(ControlledRuntimeStatusRow, paper_kill_switch_component(_ORG)) is None


def test_concurrent_first_slot_inserts_leave_both_activations_committed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Two first inserts of one slot both commit their kill-switch rows."""

    reset_safety_alert_dedup()
    _capture_telegram(monkeypatch)
    database = tmp_path / "kill.db"
    engine = create_engine(
        f"sqlite+pysqlite:///{database}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = _settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        exchange_mode="paper_internal",
        provider_mode="mock",
        market_data_provider="mock",
        global_kill_switch_active=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="arming-visibility-secret-32-bytes-min",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
    )
    org_b = uuid.UUID("00000000-0000-0000-0000-000000000d30")
    with factory() as session:
        session.add(Organization(id=_ORG, name="Arming Org"))
        session.add(Organization(id=org_b, name="Second Org"))
        session.add(
            User(
                id=_USER,
                email="owner@test.example",
                hashed_password=hash_password("TestPassword123!", settings),
            )
        )
        session.commit()
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    monkeypatch.setattr(
        "app.services.risk.kill_switch.paper_kill_switch_component",
        lambda _organization_id: "shared-kill-switch-slot",
    )
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def _activate(organization_id: uuid.UUID) -> None:
        try:
            barrier.wait(timeout=10)
            with factory() as session:
                service = KillSwitchService(session, AuditService(session), settings)
                service.activate(
                    organization_id=organization_id,
                    actor_user_id=_USER,
                    payload=KillSwitchMutationRequest(confirm=True, reason="operator note"),
                    source="manual",
                )
                session.commit()
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_activate, args=(organization_id,))
        for organization_id in (_ORG, org_b)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    with factory() as session:
        states = list(session.scalars(select(KillSwitchState)).all())
    assert {row.organization_id for row in states} == {_ORG, org_b}
    assert all(bool(row.active) for row in states)
    engine.dispose()


def test_two_organizations_keep_separate_unsent_kill_switch_slots(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_safety_alert_dedup()

    def _fail(
        self: TelegramAlertDeliveryProvider,
        payload: object,
        *,
        bypass_enable_check: bool = False,
    ) -> AlertDeliveryResult:
        del self, payload, bypass_enable_check
        return AlertDeliveryResult(
            success=False,
            channel=AlertDeliveryChannel.TELEGRAM,
            error="http 500",
        )

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _fail)
    factory, settings = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    org_b = uuid.UUID("00000000-0000-0000-0000-000000000d30")
    with factory() as session:
        session.add(Organization(id=org_b, name="Second Org"))
        session.commit()
        service = KillSwitchService(session, AuditService(session), settings)
        for organization_id in (_ORG, org_b):
            service.activate(
                organization_id=organization_id,
                actor_user_id=_USER,
                payload=KillSwitchMutationRequest(confirm=True, reason="operator note"),
                source="manual",
            )
        session.commit()
    assert paper_kill_switch_component(_ORG) == "k" + _ORG.hex
    assert len(paper_kill_switch_component(_ORG)) == 33
    assert paper_kill_switch_component(_ORG) != paper_kill_switch_component(org_b)
    with factory() as session:
        first = session.get(ControlledRuntimeStatusRow, paper_kill_switch_component(_ORG))
        second = session.get(ControlledRuntimeStatusRow, paper_kill_switch_component(org_b))
        assert first is not None and second is not None
        assert first.component != second.component
        assert first.worker_id == str(_ORG)
        assert second.worker_id == str(org_b)
        assert first.last_error_code == "unsent"
        assert second.last_error_code == "unsent"


def test_legacy_kill_switch_slot_is_still_retried(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_safety_alert_dedup()
    monkeypatch.setattr(
        "app.services.safety_alerts.deliver_queued_safety_alerts",
        lambda _session: None,
    )
    factory, settings = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), settings)
        service.activate(
            organization_id=_ORG,
            actor_user_id=_USER,
            payload=KillSwitchMutationRequest(confirm=True, reason="operator note"),
            source="manual",
        )
        session.commit()
    with factory() as session, session.begin():
        current = session.get(ControlledRuntimeStatusRow, paper_kill_switch_component(_ORG))
        assert current is not None
        session.add(
            ControlledRuntimeStatusRow(
                component=PAPER_KILL_SWITCH_COMPONENT,
                worker_id=current.worker_id,
                heartbeat_at=current.heartbeat_at,
                activation_state=current.activation_state,
                last_scan_reason=current.last_scan_reason,
                last_error_code=current.last_error_code,
                lease_epoch=current.lease_epoch,
                market_source=current.market_source,
            )
        )
        session.delete(current)
    sent = _capture_telegram(monkeypatch)
    assert retry_unsent_kill_switch_alert(settings) is True
    page = "Kill switch activated. New entries stay blocked until a human deactivates it."
    assert sent and page in sent[0]
    with factory() as session:
        legacy = session.get(ControlledRuntimeStatusRow, PAPER_KILL_SWITCH_COMPONENT)
        assert legacy is not None
        assert legacy.last_error_code == "sent"


def test_unsent_kill_switch_retry_is_limited_to_once_per_minute(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_safety_alert_dedup()
    monkeypatch.setattr(
        "app.services.safety_alerts.deliver_queued_safety_alerts",
        lambda _session: None,
    )
    calls: list[int] = []

    def _fail(
        self: TelegramAlertDeliveryProvider,
        payload: object,
        *,
        bypass_enable_check: bool = False,
    ) -> AlertDeliveryResult:
        del self, payload, bypass_enable_check
        calls.append(1)
        return AlertDeliveryResult(
            success=False,
            channel=AlertDeliveryChannel.TELEGRAM,
            error="http 500",
        )

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _fail)
    factory, settings = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), settings)
        service.activate(
            organization_id=_ORG,
            actor_user_id=_USER,
            payload=KillSwitchMutationRequest(confirm=True, reason="operator note"),
            source="drawdown",
        )
        session.commit()
    started = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    assert retry_unsent_kill_switch_alert(settings, now=started) is False
    assert len(calls) == 1
    assert retry_unsent_kill_switch_alert(settings, now=started + timedelta(seconds=59)) is False
    assert len(calls) == 1
    assert retry_unsent_kill_switch_alert(settings, now=started + timedelta(seconds=61)) is False
    assert len(calls) == 2


def test_full_hex_keys_do_not_collide_on_the_last_digit(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    """Orgs that share the first 31 hex digits used to share one slot."""

    reset_safety_alert_dedup()

    def _fail(
        self: TelegramAlertDeliveryProvider,
        payload: object,
        *,
        bypass_enable_check: bool = False,
    ) -> AlertDeliveryResult:
        del self, payload, bypass_enable_check
        return AlertDeliveryResult(
            success=False,
            channel=AlertDeliveryChannel.TELEGRAM,
            error="http 500",
        )

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _fail)
    factory, settings = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    left = uuid.UUID("11111111-1111-1111-1111-111111111110")
    right = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert ("k" + left.hex)[:32] == ("k" + right.hex)[:32]
    assert paper_kill_switch_component(left) == "k" + left.hex
    assert paper_kill_switch_component(right) == "k" + right.hex
    assert paper_kill_switch_component(left) != paper_kill_switch_component(right)
    assert len(paper_kill_switch_component(left)) <= CONTROLLED_RUNTIME_COMPONENT_LENGTH
    with factory() as session:
        session.add(Organization(id=left, name="Left Org"))
        session.add(Organization(id=right, name="Right Org"))
        session.commit()
        service = KillSwitchService(session, AuditService(session), settings)
        for organization_id in (left, right):
            service.activate(
                organization_id=organization_id,
                actor_user_id=_USER,
                payload=KillSwitchMutationRequest(confirm=True, reason="operator note"),
                source="manual",
            )
        session.commit()
    with factory() as session:
        first = session.get(ControlledRuntimeStatusRow, paper_kill_switch_component(left))
        second = session.get(ControlledRuntimeStatusRow, paper_kill_switch_component(right))
        assert first is not None and second is not None
        assert first.component != second.component
        assert first.worker_id == str(left)
        assert second.worker_id == str(right)
        assert first.last_error_code == "unsent"
        assert second.last_error_code == "unsent"


def test_truncated_kill_switch_slot_is_still_retried(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_safety_alert_dedup()
    monkeypatch.setattr(
        "app.services.safety_alerts.deliver_queued_safety_alerts",
        lambda _session: None,
    )
    factory, settings = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), settings)
        service.activate(
            organization_id=_ORG,
            actor_user_id=_USER,
            payload=KillSwitchMutationRequest(confirm=True, reason="operator note"),
            source="manual",
        )
        session.commit()
    with factory() as session, session.begin():
        current = session.get(ControlledRuntimeStatusRow, paper_kill_switch_component(_ORG))
        assert current is not None
        session.add(
            ControlledRuntimeStatusRow(
                component=paper_kill_switch_truncated_component(_ORG),
                worker_id=current.worker_id,
                heartbeat_at=current.heartbeat_at,
                activation_state=current.activation_state,
                last_scan_reason=current.last_scan_reason,
                last_error_code=current.last_error_code,
                lease_epoch=current.lease_epoch,
                market_source=current.market_source,
            )
        )
        session.delete(current)
    assert len(paper_kill_switch_truncated_component(_ORG)) == 32
    sent = _capture_telegram(monkeypatch)
    assert retry_unsent_kill_switch_alert(settings) is True
    page = "Kill switch activated. New entries stay blocked until a human deactivates it."
    assert sent and page in sent[0]
    with factory() as session:
        legacy = session.get(
            ControlledRuntimeStatusRow,
            paper_kill_switch_truncated_component(_ORG),
        )
        assert legacy is not None
        assert legacy.last_error_code == "sent"
        assert session.get(ControlledRuntimeStatusRow, paper_kill_switch_component(_ORG)) is None


def test_health_skips_the_retry_thread_when_nothing_is_pending(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    _capture_telegram(monkeypatch)
    factory, settings = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    from app.api.routes.health import paper_worker_heartbeat

    tasks = BackgroundTasks()
    asyncio.run(paper_worker_heartbeat(settings, tasks))
    assert tasks.tasks == []
    assert kill_switch_retry_should_schedule(None) is True
    assert kill_switch_retry_should_schedule({}) is False


def test_health_skips_the_retry_thread_inside_the_minute(
    monkeypatch: pytest.MonkeyPatch,
    _kill_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_safety_alert_dedup()

    def _fail(
        self: TelegramAlertDeliveryProvider,
        payload: object,
        *,
        bypass_enable_check: bool = False,
    ) -> AlertDeliveryResult:
        del self, payload, bypass_enable_check
        return AlertDeliveryResult(
            success=False,
            channel=AlertDeliveryChannel.TELEGRAM,
            error="http 500",
        )

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _fail)
    factory, settings = _kill_db
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: factory)
    with factory() as session:
        service = KillSwitchService(session, AuditService(session), settings)
        service.activate(
            organization_id=_ORG,
            actor_user_id=_USER,
            payload=KillSwitchMutationRequest(confirm=True, reason="operator note"),
            source="drawdown",
        )
        session.commit()
    from app.api.routes.health import paper_worker_heartbeat

    due = BackgroundTasks()
    asyncio.run(paper_worker_heartbeat(settings, due))
    assert len(due.tasks) == 1
    asyncio.run(due())
    quiet = BackgroundTasks()
    asyncio.run(paper_worker_heartbeat(settings, quiet))
    assert quiet.tasks == []


def test_precheck_uses_the_same_sixty_second_window() -> None:
    reset_kill_switch_retry_window()
    started = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    pending = {
        "slot": SimpleNamespace(
            market_source="kill_switch",
            activation_state="active",
            last_error_code="unsent",
            lease_epoch=4,
            worker_id=str(_ORG),
            last_scan_reason="manual",
        )
    }
    assert kill_switch_retry_should_schedule(pending, now=started) is True
    from app.services.risk.kill_switch import _claim_kill_switch_retry

    assert _claim_kill_switch_retry(str(_ORG), 4, started) is True
    assert kill_switch_retry_should_schedule(pending, now=started + timedelta(seconds=59)) is False
    assert kill_switch_retry_should_schedule(pending, now=started + timedelta(seconds=61)) is True
    ignored = {
        "slot": SimpleNamespace(
            market_source="kill_switch",
            activation_state="active",
            last_error_code="sent",
            lease_epoch=4,
            worker_id=str(_ORG),
            last_scan_reason="manual",
        )
    }
    assert kill_switch_retry_should_schedule(ignored, now=started) is False
    reset_kill_switch_retry_window()
