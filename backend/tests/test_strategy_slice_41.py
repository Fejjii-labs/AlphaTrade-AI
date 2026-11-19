"""Slice 41 — alert delivery foundation and market watcher prep tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.mutation_policy import mutation_allowed
from app.core.config import Settings
from app.db.base import Base
from app.db.models import Membership, Organization, PaperValidationAlert, User
from app.db.session import get_session
from app.main import create_app
from app.providers.alert_delivery.base import AlertDeliveryPayload
from app.providers.alert_delivery.webhook import WebhookAlertDeliveryProvider
from app.repositories.paper_scheduler import PaperAlertRepository
from app.schemas.common import (
    AlertDeliveryStatus,
    MembershipRole,
    PaperAlertType,
)
from app.security.passwords import hash_password
from app.services.alert_delivery_service import AlertDeliveryService
from app.services.market_data_service import MarketDataService
from app.services.market_watcher_service import MarketWatcherService
from app.services.paper_alert_service import PaperAlertService

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000400")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000401")
OTHER_ORG = uuid.UUID("00000000-0000-0000-0000-000000000402")
OTHER_USER = uuid.UUID("00000000-0000-0000-0000-000000000403")


@pytest.fixture
def slice41_db() -> Iterator[sessionmaker[Session]]:
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
        jwt_secret="slice41-test-secret-key-minimum",
    )
    with factory() as session:
        org = Organization(id=ORG_ID, name="Slice41 Org")
        other_org = Organization(id=OTHER_ORG, name="Other Org")
        owner = User(
            id=USER_ID,
            email="owner41@test.example",
            hashed_password=hash_password("TestPassword123!", settings),
            email_verified=True,
        )
        other = User(
            id=OTHER_USER,
            email="other41@test.example",
            hashed_password=hash_password("TestPassword123!", settings),
            email_verified=True,
        )
        session.add(org)
        session.add(other_org)
        session.add(owner)
        session.add(other)
        session.flush()
        session.add(Membership(user_id=USER_ID, organization_id=ORG_ID, role=MembershipRole.OWNER))
        session.add(
            Membership(user_id=OTHER_USER, organization_id=OTHER_ORG, role=MembershipRole.OWNER)
        )
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
        "jwt_secret": "slice41-test-secret-key-minimum",
        "rate_limit_use_redis": False,
        "access_token_denylist_use_redis": False,
        "provider_mode": "mock",
        "market_data_provider": "mock",
        "alert_delivery_enabled": False,
        "alert_webhook_enabled": False,
        "market_watcher_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def slice41_client(slice41_db: sessionmaker[Session]) -> Iterator[TestClient]:
    app = create_app(settings=_settings())

    def _override_session() -> Iterator[Session]:
        with slice41_db() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        login = client.post(
            "/auth/login",
            json={"email": "owner41@test.example", "password": "TestPassword123!"},
        )
        token = login.json()["tokens"]["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client
    app.dependency_overrides.clear()


def _create_alert(
    session: Session,
    *,
    org_id: uuid.UUID = ORG_ID,
    settings: Settings | None = None,
) -> PaperValidationAlert:
    svc = PaperAlertService(session)
    created = svc.create(
        organization_id=org_id,
        alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
        message="Test setup signal",
    )
    assert created is not None
    row = session.scalar(select(PaperValidationAlert).where(PaperValidationAlert.id == created.id))
    assert row is not None
    return row


def test_alert_delivery_disabled_by_default(slice41_db: sessionmaker[Session]) -> None:
    with slice41_db() as session:
        row = _create_alert(session)
        assert row.delivery_status == AlertDeliveryStatus.DISABLED
        status = AlertDeliveryService(session, _settings()).get_status()
        assert status.delivery_enabled is False
        assert status.effective_external_enabled is False


def test_in_app_alert_available_without_external_delivery(
    slice41_client: TestClient,
    slice41_db: sessionmaker[Session],
) -> None:
    with slice41_db() as session:
        _create_alert(session)
        session.commit()
    listing = slice41_client.get("/alerts")
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1


def test_webhook_not_called_when_disabled(slice41_db: sessionmaker[Session]) -> None:
    mock_post = MagicMock()
    settings = _settings(alert_delivery_enabled=True, alert_webhook_enabled=False)
    provider = WebhookAlertDeliveryProvider(settings, http_post=mock_post)
    result = provider.deliver(
        AlertDeliveryPayload(
            alert_id="a",
            organization_id=str(ORG_ID),
            alert_type="setup_signal_detected",
            severity="info",
            message="hello",
        )
    )
    assert result.skipped is True
    mock_post.assert_not_called()


def test_webhook_success_updates_delivery_status(slice41_db: sessionmaker[Session]) -> None:
    mock_response = MagicMock(status_code=200)
    mock_post = MagicMock(return_value=mock_response)
    settings = _settings(
        alert_delivery_enabled=True,
        alert_webhook_enabled=True,
        alert_webhook_url="https://example.com/hook",
    )
    with slice41_db() as session:
        from app.schemas.notifications import NotificationPreferencesUpdate
        from app.services.audit_service import AuditService
        from app.services.notifications.preferences_service import NotificationPreferencesService

        NotificationPreferencesService(session, AuditService(session)).update(
            NotificationPreferencesUpdate(webhook_enabled=True),
            organization_id=ORG_ID,
            user_id=USER_ID,
        )
        row = _create_alert(session, settings=settings)
        row.delivery_status = AlertDeliveryStatus.PENDING
        row.user_id = USER_ID
        session.flush()
        delivery = AlertDeliveryService(session, settings, http_post=mock_post)
        result = delivery.deliver_alert(row.id, organization_id=ORG_ID, user_id=USER_ID)
        assert result.delivered is True
        assert result.alert.delivery_status == AlertDeliveryStatus.DELIVERED
        mock_post.assert_called_once()


def test_webhook_failure_redacted_and_non_fatal(slice41_db: sessionmaker[Session]) -> None:
    mock_post = MagicMock(side_effect=httpx.ConnectError("secret-token-refused"))
    settings = _settings(
        alert_delivery_enabled=True,
        alert_webhook_enabled=True,
        alert_webhook_url="https://example.com/hook",
    )
    with slice41_db() as session:
        from app.schemas.notifications import NotificationPreferencesUpdate
        from app.services.audit_service import AuditService
        from app.services.notifications.preferences_service import NotificationPreferencesService

        NotificationPreferencesService(session, AuditService(session)).update(
            NotificationPreferencesUpdate(webhook_enabled=True),
            organization_id=ORG_ID,
            user_id=USER_ID,
        )
        row = _create_alert(session, settings=settings)
        row.delivery_status = AlertDeliveryStatus.PENDING
        row.user_id = USER_ID
        session.flush()
        delivery = AlertDeliveryService(session, settings, http_post=mock_post)
        result = delivery.deliver_alert(row.id, organization_id=ORG_ID, user_id=USER_ID)
    assert result.delivered is False
    assert result.alert.delivery_status == AlertDeliveryStatus.FAILED
    assert result.alert.last_delivery_error is not None


def test_deliver_pending_tenant_scoped(slice41_db: sessionmaker[Session]) -> None:
    settings = _settings(
        alert_delivery_enabled=True,
        alert_webhook_enabled=True,
        alert_webhook_url="https://example.com/hook",
    )
    mock_post = MagicMock(return_value=MagicMock(status_code=200))
    with slice41_db() as session:
        row_a = _create_alert(session, org_id=ORG_ID, settings=settings)
        row_b = _create_alert(session, org_id=OTHER_ORG, settings=settings)
        row_a.delivery_status = AlertDeliveryStatus.PENDING
        row_b.delivery_status = AlertDeliveryStatus.PENDING
        session.commit()
        delivery = AlertDeliveryService(session, settings, http_post=mock_post)
        result = delivery.deliver_pending(organization_id=ORG_ID)
        assert result.processed == 1
        session.refresh(row_b)
        assert row_b.delivery_status == AlertDeliveryStatus.PENDING


def test_duplicate_alert_does_not_duplicate_delivery(slice41_db: sessionmaker[Session]) -> None:
    with slice41_db() as session:
        svc = PaperAlertService(session)
        first = svc.create(
            organization_id=ORG_ID,
            alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
            message="dup",
        )
        second = svc.create(
            organization_id=ORG_ID,
            alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
            message="dup again",
        )
        assert first is not None
        assert second is None
        pending = PaperAlertRepository(session).list_pending_delivery(ORG_ID)
        assert len(pending) <= 1


def test_alert_read_does_not_affect_delivery_status(
    slice41_db: sessionmaker[Session],
) -> None:
    with slice41_db() as session:
        row = _create_alert(session)
        before = row.delivery_status
        svc = PaperAlertService(session)
        updated = svc.mark_read(row.id, organization_id=ORG_ID)
        assert updated.delivery_status == before
        assert updated.read_at is not None


def test_market_watcher_never_calls_trading_api(slice41_db: sessionmaker[Session]) -> None:
    from app.providers.market_data import MockMarketDataProvider
    from app.schemas.market_watcher import SCAN_CONFIRM_PHRASE, MarketWatcherScanRequest

    provider = MockMarketDataProvider()
    assert not hasattr(provider, "place_order")
    market_data = MarketDataService(provider)
    settings = _settings(market_watcher_enabled=True)
    with slice41_db() as session:
        svc = MarketWatcherService(session, settings, market_data=market_data)
        result = svc.scan(
            organization_id=ORG_ID,
            user_id=USER_ID,
            request=MarketWatcherScanRequest(
                confirm=SCAN_CONFIRM_PHRASE,
                symbols=["BTCUSDT"],
                timeframes=["15m"],
                dry_run=True,
            ),
        )
        assert result.observations_created >= 1
        assert not any(
            name in dir(provider) for name in ("create_order", "place_order", "submit_order")
        )


def test_state_changing_agent_action_requires_confirmation() -> None:
    assert not mutation_allowed("deliver pending alerts")
    assert mutation_allowed("I confirm deliver pending alerts")
    assert mutation_allowed("run market watcher scan") is False
    assert mutation_allowed("I confirm market watcher scan")


def test_alert_delivery_api_endpoints(
    slice41_client: TestClient, slice41_db: sessionmaker[Session]
) -> None:
    with slice41_db() as session:
        _create_alert(session)
        session.commit()
    status = slice41_client.get("/alerts/delivery-status")
    assert status.status_code == 200
    assert status.json()["paper_only"] is True
    summary = slice41_client.get("/alerts/delivery-summary")
    assert summary.status_code == 200
