"""Tests for POST /alerts/{alert_id}/deliver-telegram (Slice 70)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models import AuditLog, Membership, PaperValidationAlert
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import (
    AlertDeliveryChannel,
    AlertDeliveryStatus,
    AuditEventType,
    MembershipRole,
    PaperAlertType,
)
from app.schemas.telegram_alert_delivery import TELEGRAM_ALERT_DELIVERY_CONFIRM_PHRASE
from app.security.rate_limit import reset_rate_limiter
from app.services.paper_alert_service import PaperAlertService

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "telegram-alert-delivery-secret-min-32",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
    "alert_delivery_enabled": False,
    "telegram_alerts_enabled": False,
    "alert_webhook_enabled": False,
    "worker_enabled": False,
    "market_watcher_enabled": False,
    "market_watcher_bridge_enabled": False,
}


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


def _build_client(settings: Settings) -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    get_settings.cache_clear()
    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as client:
        yield client, factory

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    engine.dispose()


@pytest.fixture
def client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    yield from _build_client(Settings(**_BASE))


@pytest.fixture
def telegram_configured_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    settings = {
        **_BASE,
        "telegram_bot_token": "bot123456789:TESTTOKEN_secret_value",
        "telegram_chat_id": "999888777",
    }
    yield from _build_client(Settings(**settings))


def _register_owner(
    client: TestClient,
    email: str = "owner@example.com",
    *,
    organization_name: str = "Telegram Delivery Org",
) -> tuple[dict[str, str], uuid.UUID, uuid.UUID]:
    reg = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "SecurePass123!",
            "organization_name": organization_name,
        },
    )
    assert reg.status_code == 201, reg.text
    login = client.post(
        "/auth/login",
        json={"email": email, "password": "SecurePass123!"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["tokens"]["access_token"]
    org_id = uuid.UUID(reg.json()["organization"]["id"])
    user_id = uuid.UUID(reg.json()["user"]["id"])
    return {"Authorization": f"Bearer {token}"}, org_id, user_id


def _set_membership_role(
    factory: sessionmaker[Session],
    *,
    user_id: uuid.UUID,
    role: MembershipRole,
) -> None:
    with factory() as session:
        membership = session.query(Membership).filter(Membership.user_id == user_id).one()
        membership.role = role
        session.commit()


def _create_alert(
    factory: sessionmaker[Session],
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
) -> uuid.UUID:
    with factory() as session:
        created = PaperAlertService(session).create(
            organization_id=org_id,
            alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
            message="In-app setup signal for Telegram delivery test.",
            user_id=user_id,
        )
        assert created is not None
        session.commit()
        return created.id


def _post_deliver(
    client: TestClient,
    alert_id: uuid.UUID,
    headers: dict[str, str],
    *,
    confirm: str = TELEGRAM_ALERT_DELIVERY_CONFIRM_PHRASE,
):
    return client.post(
        f"/alerts/{alert_id}/deliver-telegram",
        headers=headers,
        json={"confirm": confirm},
    )


def test_deliver_telegram_requires_owner(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, factory = client
    _headers, org_id, user_id = _register_owner(test_client, email="owner-only@example.com")
    alert_id = _create_alert(factory, org_id=org_id, user_id=user_id)
    response = test_client.post(
        f"/alerts/{alert_id}/deliver-telegram",
        json={"confirm": TELEGRAM_ALERT_DELIVERY_CONFIRM_PHRASE},
    )
    assert response.status_code == 401


def test_deliver_telegram_non_owner_forbidden(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers, org_id, user_id = _register_owner(test_client, email="owner-trader@example.com")
    alert_id = _create_alert(factory, org_id=org_id, user_id=user_id)
    _set_membership_role(factory, user_id=user_id, role=MembershipRole.TRADER)
    response = _post_deliver(test_client, alert_id, headers)
    assert response.status_code == 403


def test_deliver_telegram_missing_confirmation_blocked(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers, org_id, user_id = _register_owner(test_client, email="bad-confirm@example.com")
    alert_id = _create_alert(factory, org_id=org_id, user_id=user_id)
    response = _post_deliver(test_client, alert_id, headers, confirm="WRONG")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "blocked"
    assert body["error_code"] == "confirmation_required"


def test_deliver_telegram_missing_alert_404(
    telegram_configured_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, _ = telegram_configured_client
    headers, _, _ = _register_owner(test_client, email="missing-alert@example.com")
    response = _post_deliver(test_client, uuid.uuid4(), headers)
    assert response.status_code == 404


def test_deliver_telegram_other_tenant_forbidden(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    _headers_a, org_a, user_a = _register_owner(
        test_client,
        email="tenant-a@example.com",
        organization_name="Tenant A Org",
    )
    alert_id = _create_alert(factory, org_id=org_a, user_id=user_a)
    headers_b, _, _ = _register_owner(
        test_client,
        email="tenant-b@example.com",
        organization_name="Tenant B Org",
    )
    response = _post_deliver(test_client, alert_id, headers_b)
    assert response.status_code == 404


def test_deliver_telegram_real_trading_blocked() -> None:
    with pytest.raises(ValidationError, match="permanently rejected"):
        Settings(
            **{
                **_BASE,
                "execution_mode": "trade",
                "enable_real_trading": True,
            }
        )


def test_deliver_telegram_missing_config_skipped(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers, org_id, user_id = _register_owner(test_client, email="no-config@example.com")
    alert_id = _create_alert(factory, org_id=org_id, user_id=user_id)
    response = _post_deliver(test_client, alert_id, headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "skipped_not_configured"
    raw = json.dumps(body).lower()
    assert "testtoken" not in raw


def test_deliver_telegram_success_mocked(
    telegram_configured_client: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, factory = telegram_configured_client
    mock_response = MagicMock()
    mock_response.status_code = 200
    monkeypatch.setattr(
        "app.providers.alert_delivery.telegram.httpx.post",
        MagicMock(return_value=mock_response),
    )
    headers, org_id, user_id = _register_owner(test_client, email="success@example.com")
    alert_id = _create_alert(factory, org_id=org_id, user_id=user_id)
    response = _post_deliver(test_client, alert_id, headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "sent"
    assert body["channel"] == "telegram"
    assert body["sent_at"] is not None
    assert body["delivery_id"] is not None
    raw = json.dumps(body).lower()
    assert "testtoken" not in raw
    assert "999888777" not in raw


def test_manual_delivery_applies_policy_before_any_transport(
    telegram_configured_client,
    monkeypatch,
):
    test_client, factory = telegram_configured_client
    post = MagicMock()
    monkeypatch.setattr("app.providers.alert_delivery.telegram.httpx.post", post)
    headers, org_id, user_id = _register_owner(test_client, email="policy-filter@example.com")
    alert_id = _create_alert(factory, org_id=org_id, user_id=user_id)
    update = test_client.patch(
        "/notifications/preferences",
        headers=headers,
        json={"telegram_policy": {"schema_version": 2, "minimum_quality": "0"}},
    )
    assert update.status_code == 200
    assert update.json()["telegram_enabled"] is False
    result = _post_deliver(test_client, alert_id, headers)
    assert result.json()["status"] == "blocked"
    assert result.json()["error_code"] == "POLICY_QUALITY_MISSING"
    post.assert_not_called()
    with factory() as session:
        row = session.get(PaperValidationAlert, alert_id)
        assert row.delivery_attempts == 0


def test_deliver_telegram_already_delivered(
    telegram_configured_client: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, factory = telegram_configured_client
    mock_response = MagicMock()
    mock_response.status_code = 200
    monkeypatch.setattr(
        "app.providers.alert_delivery.telegram.httpx.post",
        MagicMock(return_value=mock_response),
    )
    headers, org_id, user_id = _register_owner(test_client, email="dedupe@example.com")
    alert_id = _create_alert(factory, org_id=org_id, user_id=user_id)
    first = _post_deliver(test_client, alert_id, headers)
    assert first.json()["status"] == "sent"
    second = _post_deliver(test_client, alert_id, headers)
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "already_delivered"


def test_deliver_telegram_api_failure_redacted(
    telegram_configured_client: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, factory = telegram_configured_client
    mock_response = MagicMock()
    mock_response.status_code = 403
    monkeypatch.setattr(
        "app.providers.alert_delivery.telegram.httpx.post",
        MagicMock(return_value=mock_response),
    )
    headers, org_id, user_id = _register_owner(test_client, email="fail@example.com")
    alert_id = _create_alert(factory, org_id=org_id, user_id=user_id)
    response = _post_deliver(test_client, alert_id, headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed_redacted"
    assert body["error_message"] is not None
    raw = json.dumps(body).lower()
    assert "testtoken" not in raw


def test_deliver_telegram_no_execution_call(
    telegram_configured_client: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, factory = telegram_configured_client
    mock_response = MagicMock()
    mock_response.status_code = 200
    monkeypatch.setattr(
        "app.providers.alert_delivery.telegram.httpx.post",
        MagicMock(return_value=mock_response),
    )
    headers, org_id, user_id = _register_owner(test_client, email="no-exec@example.com")
    alert_id = _create_alert(factory, org_id=org_id, user_id=user_id)
    blocked = test_client.post(
        "/execution/paper",
        headers=headers,
        json={
            "proposal_id": str(uuid.uuid4()),
            "approval_id": str(uuid.uuid4()),
            "symbol": "BTCUSDT",
            "side": "buy",
            "type": "market",
            "size": "1",
            "idempotency_key": "should-not-be-used-12345678",
        },
    )
    assert blocked.status_code in {403, 404, 422}
    _post_deliver(test_client, alert_id, headers)


def test_deliver_telegram_audit_redacted(
    telegram_configured_client: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, factory = telegram_configured_client
    mock_response = MagicMock()
    mock_response.status_code = 200
    monkeypatch.setattr(
        "app.providers.alert_delivery.telegram.httpx.post",
        MagicMock(return_value=mock_response),
    )
    headers, org_id, user_id = _register_owner(test_client, email="audit@example.com")
    alert_id = _create_alert(factory, org_id=org_id, user_id=user_id)
    _post_deliver(test_client, alert_id, headers)
    with factory() as session:
        rows = list(
            session.scalars(select(AuditLog).where(AuditLog.organization_id == org_id)).all()
        )
    raw = json.dumps(
        [{"action": r.action.value, "meta": r.redacted_metadata} for r in rows]
    ).lower()
    assert AuditEventType.ALERT_TELEGRAM_DELIVERY_REQUESTED.value in raw
    assert AuditEventType.ALERT_TELEGRAM_DELIVERY_SENT.value in raw
    assert "testtoken" not in raw
    assert "999888777" not in raw


def test_routing_summary_telegram_delivery_available(
    telegram_configured_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, _ = telegram_configured_client
    headers, _, _ = _register_owner(test_client, email="routing@example.com")
    response = test_client.get("/alerts/routing/summary", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["telegram_alert_delivery_available"] is True
    assert body["telegram_delivered_count"] == 0
    assert body["telegram_failed_count"] == 0


def test_paper_only_false_blocked(
    monkeypatch: pytest.MonkeyPatch,
    telegram_configured_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = telegram_configured_client
    from app.services import telegram_alert_delivery_service as svc

    def _fake_context(*_args: object, **_kwargs: object) -> svc.TelegramDeliverySafetyContext:
        return svc.TelegramDeliverySafetyContext(
            execution_mode=Settings(**_BASE).execution_mode,
            real_trading_enabled=False,
            paper_only=False,
            telegram_configured=True,
            chat_configured=True,
        )

    monkeypatch.setattr(svc, "_safety_context", _fake_context)
    headers, org_id, user_id = _register_owner(test_client, email="paper-only@example.com")
    alert_id = _create_alert(factory, org_id=org_id, user_id=user_id)
    response = _post_deliver(test_client, alert_id, headers)
    assert response.json()["status"] == "blocked"
    assert response.json()["error_code"] == "paper_only_required"


def test_telegram_outbox_delivers_once_after_parking(
    telegram_configured_client: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_client, factory = telegram_configured_client
    mock_response = MagicMock()
    mock_response.status_code = 200
    post = MagicMock(return_value=mock_response)
    monkeypatch.setattr("app.providers.alert_delivery.telegram.httpx.post", post)
    headers, org_id, user_id = _register_owner(test_client, email="row-update@example.com")
    alert_id = _create_alert(factory, org_id=org_id, user_id=user_id)
    assert _post_deliver(test_client, alert_id, headers).json()["status"] == "sent"
    assert _post_deliver(test_client, alert_id, headers).json()["status"] == "already_delivered"
    post.assert_called_once()
    with factory() as session:
        row = session.scalar(
            select(PaperValidationAlert).where(PaperValidationAlert.id == alert_id)
        )
    assert row is not None
    assert row.delivery_status is AlertDeliveryStatus.DELIVERED
    assert row.delivery_channel is AlertDeliveryChannel.TELEGRAM
    assert row.metadata_json.get("telegram_manual_delivered") is True
