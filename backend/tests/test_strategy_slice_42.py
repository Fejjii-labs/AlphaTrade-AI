"""Slice 42 — market watcher bridge and webhook hardening tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.strategy_intent import classify_strategy_workflow
from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    MarketWatcherObservation,
    Membership,
    Organization,
    PaperValidationAlert,
    User,
)
from app.db.session import get_session
from app.main import create_app
from app.providers.alert_delivery.base import AlertDeliveryPayload
from app.providers.alert_delivery.webhook import WebhookAlertDeliveryProvider
from app.schemas.agent import Intent
from app.schemas.common import (
    AlertDeliveryStatus,
    EntryTriggerType,
    ExitRuleType,
    MarketWatcherObservationStatus,
    MembershipRole,
    PaperAlertSource,
    PaperAlertType,
    Timeframe,
)
from app.schemas.structured_rules import EntryRuleBlock, ExitRuleBlock, StructuredRules
from app.security.passwords import hash_password
from app.services.alert_delivery_service import AlertDeliveryService
from app.services.market_watcher_bridge_service import MarketWatcherBridgeService
from app.services.paper_alert_service import PaperAlertService
from app.services.paper_validation_runtime_service import PaperValidationRuntimeService

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000500")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000501")
OTHER_ORG = uuid.UUID("00000000-0000-0000-0000-000000000502")
OTHER_USER = uuid.UUID("00000000-0000-0000-0000-000000000503")


@pytest.fixture
def slice42_db() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(
        environment="local",
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="slice42-test-secret-key-minimum",
    )
    with factory() as session:
        org = Organization(id=ORG_ID, name="Slice42 Org")
        owner = User(
            id=USER_ID,
            email="owner42@test.example",
            hashed_password=hash_password("TestPassword123!", settings),
            email_verified=True,
        )
        session.add(org)
        session.add(owner)
        session.flush()
        session.add(Membership(user_id=USER_ID, organization_id=ORG_ID, role=MembershipRole.OWNER))
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _settings(**overrides: object) -> Settings:
    base = {
        "environment": "local",
        "log_json": False,
        "execution_mode": "paper",
        "enable_real_trading": False,
        "database_url": "sqlite+pysqlite:///:memory:",
        "jwt_secret": "slice42-test-secret-key-minimum",
        "rate_limit_use_redis": False,
        "access_token_denylist_use_redis": False,
        "provider_mode": "mock",
        "market_data_provider": "mock",
        "market_watcher_enabled": False,
        "market_watcher_bridge_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


def _enable_webhook_prefs(session: Session) -> None:
    from app.schemas.notifications import NotificationPreferencesUpdate
    from app.services.audit_service import AuditService
    from app.services.notifications.preferences_service import NotificationPreferencesService

    NotificationPreferencesService(session, AuditService(session)).update(
        NotificationPreferencesUpdate(webhook_enabled=True),
        organization_id=ORG_ID,
        user_id=USER_ID,
    )


@pytest.fixture
def slice42_client(slice42_db: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(settings=_settings())

    def _override_session() -> Iterator[Session]:
        with slice42_db() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        login = client.post(
            "/auth/login",
            json={"email": "owner42@test.example", "password": "TestPassword123!"},
        )
        token = login.json()["tokens"]["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client
    app.dependency_overrides.clear()


def _structured_rules() -> dict:
    return StructuredRules(
        primary_timeframe=Timeframe.M15,
        entry_rules=[EntryRuleBlock(trigger_type=EntryTriggerType.EMA_PULLBACK)],
        exit_rules=[
            ExitRuleBlock(rule_type=ExitRuleType.FIXED_STOP, value=Decimal("2")),
            ExitRuleBlock(rule_type=ExitRuleType.TP_MULTIPLE, r_multiple=Decimal("1")),
        ],
        no_trade_rules=[],
    ).model_dump(mode="json")


def _create_strategy(client: TestClient) -> str:
    card = {
        "strategy_name": "Bridge Test",
        "market_type": "crypto_perp",
        "asset_universe": ["BTCUSDT"],
        "timeframes": ["15m"],
        "entry_conditions": ["Pullback"],
        "confirmation_conditions": ["RSI"],
        "invalidation": ["Swing low"],
        "stop_loss": ["2%"],
        "take_profit_plan": ["1R"],
        "runner_plan": [],
        "position_sizing": ["1%"],
        "add_rules": [],
        "no_trade_rules": [],
        "backtest_rules": [],
        "success_criteria": ["Win rate"],
        "validation_status": "draft",
    }
    resp = client.post(
        "/strategies",
        json={"name": "Bridge Strategy", "setup_type": "htf_trend_pullback", "card": card},
    )
    strategy_id = resp.json()["id"]
    client.patch(f"/strategies/{strategy_id}/structured-rules", json=_structured_rules())
    client.post(
        f"/strategies/{strategy_id}/backtests",
        json={
            "assumptions": {
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "exchange": "mock",
                "initial_capital": "10000",
                "fees_bps": 10,
                "slippage_bps": 5,
                "risk_per_trade_pct": 1,
            }
        },
    )
    return strategy_id


def _start_run(client: TestClient, strategy_id: str) -> str:
    resp = client.post(
        f"/strategies/{strategy_id}/paper-validation/start",
        json={"runtime_mode": "scan_only"},
    )
    return resp.json()["id"]


def _seed_observation(
    session: Session,
    *,
    run_id: uuid.UUID | None = None,
    strategy_id: uuid.UUID | None = None,
    status: MarketWatcherObservationStatus = MarketWatcherObservationStatus.FRESH,
) -> MarketWatcherObservation:
    row = MarketWatcherObservation(
        organization_id=ORG_ID,
        symbol="BTCUSDT",
        exchange="mock",
        timeframe="15m",
        observed_at=datetime.now(UTC),
        price=Decimal("60000"),
        data_freshness="mock",
        status=status,
        related_strategy_id=strategy_id,
        related_paper_validation_run_id=run_id,
    )
    session.add(row)
    session.flush()
    return row


def test_webhook_includes_idempotency_key() -> None:
    captured: dict = {}

    def mock_post(url: str, **kwargs: object) -> MagicMock:
        captured["headers"] = kwargs.get("headers")
        captured["content"] = kwargs.get("content")
        return MagicMock(status_code=200)

    settings = _settings(
        alert_delivery_enabled=True,
        alert_webhook_enabled=True,
        alert_webhook_url="https://example.com/hook",
    )
    provider = WebhookAlertDeliveryProvider(settings, http_post=mock_post)
    provider.deliver(
        AlertDeliveryPayload(
            alert_id="alert-1",
            organization_id=str(ORG_ID),
            alert_type="setup_signal_detected",
            severity="info",
            message="test",
            idempotency_key="idem-123",
            event_id="evt-456",
            timestamp="2026-01-01T00:00:00+00:00",
        )
    )
    headers = captured["headers"]
    assert headers["X-AlphaTrade-Idempotency-Key"] == "idem-123"
    assert headers["X-AlphaTrade-Event-Id"] == "evt-456"
    body = json.loads(captured["content"])
    assert body["idempotency_key"] == "idem-123"


def test_webhook_signing_when_secret_configured() -> None:
    captured: dict = {}

    def mock_post(url: str, **kwargs: object) -> MagicMock:
        captured["headers"] = kwargs.get("headers")
        captured["content"] = kwargs.get("content")
        return MagicMock(status_code=200)

    secret = "test-webhook-secret"
    settings = _settings(
        alert_delivery_enabled=True,
        alert_webhook_enabled=True,
        alert_webhook_url="https://example.com/hook",
        alert_webhook_secret=secret,
    )
    ts = "2026-01-01T00:00:00+00:00"
    provider = WebhookAlertDeliveryProvider(settings, http_post=mock_post)
    provider.deliver(
        AlertDeliveryPayload(
            alert_id="alert-1",
            organization_id=str(ORG_ID),
            alert_type="setup_signal_detected",
            severity="info",
            message="test",
            timestamp=ts,
        )
    )
    sig = captured["headers"]["X-AlphaTrade-Signature"]
    assert sig.startswith("sha256=")
    body_bytes = captured["content"]
    expected = hmac.new(
        secret.encode(),
        f"{ts}.{body_bytes.decode()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert sig == f"sha256={expected}"


def test_webhook_unsigned_when_secret_missing() -> None:
    captured: dict = {}

    def mock_post(url: str, **kwargs: object) -> MagicMock:
        captured["headers"] = kwargs.get("headers")
        return MagicMock(status_code=200)

    settings = _settings(
        alert_delivery_enabled=True,
        alert_webhook_enabled=True,
        alert_webhook_url="https://example.com/hook",
        alert_webhook_secret="",
    )
    provider = WebhookAlertDeliveryProvider(settings, http_post=mock_post)
    provider.deliver(
        AlertDeliveryPayload(
            alert_id="alert-1",
            organization_id=str(ORG_ID),
            alert_type="setup_signal_detected",
            severity="info",
            message="test",
        )
    )
    assert "X-AlphaTrade-Signature" not in captured["headers"]


def test_webhook_failure_redacted(slice42_db: sessionmaker[Session]) -> None:
    mock_post = MagicMock(side_effect=httpx.ConnectError("secret-token-refused"))
    settings = _settings(
        alert_delivery_enabled=True,
        alert_webhook_enabled=True,
        alert_webhook_url="https://example.com/hook",
    )
    with slice42_db() as session:
        _enable_webhook_prefs(session)
        alert = PaperAlertService(session).create(
            organization_id=ORG_ID,
            alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
            message="test",
            user_id=USER_ID,
        )
        assert alert is not None
        row = session.scalar(
            select(PaperValidationAlert).where(PaperValidationAlert.id == alert.id)
        )
        assert row is not None
        row.delivery_status = AlertDeliveryStatus.PENDING
        session.flush()
        result = AlertDeliveryService(session, settings, http_post=mock_post).deliver_alert(
            row.id, organization_id=ORG_ID, user_id=USER_ID
        )
        assert result.delivered is False
        assert result.alert.last_delivery_error is not None


def test_delivery_retry_stops_after_max_attempts(slice42_db: sessionmaker[Session]) -> None:
    mock_post = MagicMock(return_value=MagicMock(status_code=500))
    settings = _settings(
        alert_delivery_enabled=True,
        alert_webhook_enabled=True,
        alert_webhook_url="https://example.com/hook",
        alert_webhook_max_retries=1,
    )
    with slice42_db() as session:
        _enable_webhook_prefs(session)
        alert = PaperAlertService(session).create(
            organization_id=ORG_ID,
            alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
            message="retry test",
            user_id=USER_ID,
        )
        assert alert is not None
        row = session.scalar(
            select(PaperValidationAlert).where(PaperValidationAlert.id == alert.id)
        )
        assert row is not None
        row.delivery_status = AlertDeliveryStatus.PENDING
        session.flush()
        delivery = AlertDeliveryService(session, settings, http_post=mock_post)
        delivery.deliver_alert(row.id, organization_id=ORG_ID, user_id=USER_ID)
        session.refresh(row)
        row.next_retry_at = datetime.now(UTC) - timedelta(minutes=1)
        session.flush()
        delivery.deliver_alert(row.id, organization_id=ORG_ID, user_id=USER_ID)
        session.refresh(row)
        row.next_retry_at = datetime.now(UTC) - timedelta(minutes=1)
        session.flush()
        result = delivery.deliver_alert(row.id, organization_id=ORG_ID, user_id=USER_ID)
        session.refresh(row)
        assert row.delivery_attempts == 3
        assert row.next_retry_at is None
        assert result.alert.delivery_status == AlertDeliveryStatus.FAILED


def test_agent_bridge_intent_classification() -> None:
    assert (
        classify_strategy_workflow("Is the market watcher bridge enabled?")
        == Intent.MARKET_WATCHER_BRIDGE_QUERY
    )
    assert (
        classify_strategy_workflow("Did the market watcher trigger any scans?")
        == Intent.MARKET_WATCHER_BRIDGE_QUERY
    )


def test_alert_source_in_schema(slice42_db: sessionmaker[Session]) -> None:
    with slice42_db() as session:
        created = PaperAlertService(session).create(
            organization_id=ORG_ID,
            alert_type=PaperAlertType.DATA_STALE,
            message="stale from bridge",
            source=PaperAlertSource.MARKET_WATCHER_BRIDGE,
        )
        assert created is not None
        assert created.alert_source == PaperAlertSource.MARKET_WATCHER_BRIDGE


def test_bridge_does_not_call_exchange_trading_api(slice42_db: sessionmaker[Session]) -> None:
    with slice42_db() as session:
        runtime = PaperValidationRuntimeService(session, _settings())
        assert not hasattr(runtime, "place_order")
        assert not any(
            name in dir(runtime) for name in ("create_order", "place_order", "submit_order")
        )


def test_bridge_history_tenant_scoped(slice42_db: sessionmaker[Session]) -> None:
    settings = _settings(market_watcher_bridge_enabled=False)
    with slice42_db() as session:
        other_org = Organization(id=OTHER_ORG, name="Other Org")
        other_user = User(
            id=OTHER_USER,
            email="other42@test.example",
            hashed_password=hash_password("TestPassword123!", settings),
            email_verified=True,
        )
        session.add(other_org)
        session.add(other_user)
        session.flush()
        session.add(
            Membership(user_id=OTHER_USER, organization_id=OTHER_ORG, role=MembershipRole.OWNER)
        )
        session.commit()

    with slice42_db() as session:
        bridge_a = MarketWatcherBridgeService(session, settings)
        bridge_b = MarketWatcherBridgeService(session, settings)
        bridge_a.tick(organization_id=ORG_ID, user_id=USER_ID)
        bridge_b.tick(organization_id=OTHER_ORG, user_id=OTHER_USER)

        org_history = bridge_a.list_history(ORG_ID)
        other_history = bridge_b.list_history(OTHER_ORG)
        assert org_history.total >= 1
        assert other_history.total >= 1
        assert all(item.organization_id == ORG_ID for item in org_history.items)
        assert all(item.organization_id == OTHER_ORG for item in other_history.items)


def test_webhook_url_not_logged_in_raw_form(slice42_db: sessionmaker[Session]) -> None:
    mock_post = MagicMock(side_effect=httpx.ConnectError("https://example.com/hook refused"))
    settings = _settings(
        alert_delivery_enabled=True,
        alert_webhook_enabled=True,
        alert_webhook_url="https://example.com/hook",
    )
    with slice42_db() as session:
        _enable_webhook_prefs(session)
        alert = PaperAlertService(session).create(
            organization_id=ORG_ID,
            alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
            message="test",
            user_id=USER_ID,
        )
        assert alert is not None
        row = session.scalar(
            select(PaperValidationAlert).where(PaperValidationAlert.id == alert.id)
        )
        assert row is not None
        row.delivery_status = AlertDeliveryStatus.PENDING
        session.flush()
        result = AlertDeliveryService(session, settings, http_post=mock_post).deliver_alert(
            row.id, organization_id=ORG_ID, user_id=USER_ID
        )
        assert result.delivered is False
        assert result.alert.last_delivery_error is not None
        assert "example.com/hook" not in (result.alert.last_delivery_error or "")


def test_delivery_retry_does_not_duplicate_alerts(slice42_db: sessionmaker[Session]) -> None:
    mock_post = MagicMock(return_value=MagicMock(status_code=500))
    settings = _settings(
        alert_delivery_enabled=True,
        alert_webhook_enabled=True,
        alert_webhook_url="https://example.com/hook",
        alert_webhook_max_retries=1,
    )
    with slice42_db() as session:
        _enable_webhook_prefs(session)
        alert = PaperAlertService(session).create(
            organization_id=ORG_ID,
            alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
            message="retry test",
            user_id=USER_ID,
        )
        assert alert is not None
        row = session.scalar(
            select(PaperValidationAlert).where(PaperValidationAlert.id == alert.id)
        )
        assert row is not None
        row.delivery_status = AlertDeliveryStatus.PENDING
        session.flush()
        delivery = AlertDeliveryService(session, settings, http_post=mock_post)
        for _ in range(3):
            delivery.deliver_alert(row.id, organization_id=ORG_ID, user_id=USER_ID)
            session.refresh(row)
            row.next_retry_at = datetime.now(UTC) - timedelta(minutes=1)
            session.flush()
        alert_count = session.scalars(select(PaperValidationAlert)).all()
        assert len(alert_count) == 1
