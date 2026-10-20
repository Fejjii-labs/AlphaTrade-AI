"""Arming refusal visibility, exit-monitor liveness, and kill-switch pages.

Flags stay off. Real-money trading stays blocked. These tests do not arm a
worker and do not send a live Telegram message.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Organization, User
from app.persistence.runtime_status import (
    PAPER_ARMING_COMPONENT,
    PAPER_EXIT_MONITOR_COMPONENT,
    PAPER_WORKER_COMPONENT,
    RuntimeStatusWrite,
)
from app.providers.alert_delivery.base import AlertDeliveryResult
from app.providers.alert_delivery.telegram import TelegramAlertDeliveryProvider
from app.schemas.common import AlertDeliveryChannel
from app.schemas.risk import KillSwitchMutationRequest
from app.security.passwords import hash_password
from app.services.arming_gates import COOLDOWN_INVALID, CYCLE_INTERVAL_EXCEEDS_TIMEFRAME
from app.services.arming_visibility import describe_boot_arming, publish_boot_arming_posture
from app.services.audit_service import AuditService
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.risk.kill_switch import KillSwitchService
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
    from app.api.routes.health import health, paper_worker_heartbeat

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
    report = asyncio.run(paper_worker_heartbeat(settings))
    assert report.arming_refusal.reason == COOLDOWN_INVALID
    assert report.arming_alert_raised is True
    assert report.exit_monitor_status == "disabled"
    again = asyncio.run(paper_worker_heartbeat(settings))
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
    from app.api.routes.health import paper_worker_heartbeat

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
    report = asyncio.run(paper_worker_heartbeat(settings))
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
    missing = asyncio.run(paper_worker_heartbeat(settings))
    assert missing.exit_monitor_status == "missing"
    assert missing.exit_monitor_alert_raised is True
    quiet = _arming_row(reason="", source="", state="clear")
    monkeypatch.setattr(
        "app.api.routes.health._load_runtime_rows",
        lambda: {PAPER_ARMING_COMPONENT: quiet, PAPER_EXIT_MONITOR_COMPONENT: stale},
    )
    disabled = asyncio.run(paper_worker_heartbeat(settings))
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
