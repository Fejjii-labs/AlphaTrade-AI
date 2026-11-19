"""Slice 77 — setup alert review endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import AuditLog, Membership, Organization, User
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import MembershipRole, PaperAlertSource, PaperAlertType
from app.security.passwords import hash_password
from app.security.rate_limit import reset_rate_limiter
from app.services.paper_alert_service import PaperAlertService

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000007701")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000007702")
USER_A = uuid.UUID("00000000-0000-0000-0000-000000007711")
USER_B = uuid.UUID("00000000-0000-0000-0000-000000007712")

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "setup-alert-review-secret-min-32",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
    "alert_delivery_enabled": False,
    "telegram_alerts_enabled": False,
    "worker_enabled": False,
    "market_watcher_enabled": False,
}


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


@pytest.fixture
def client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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
    settings = Settings(**_BASE)

    with factory() as session:
        session.add(Organization(id=ORG_A, name="Review Org A"))
        session.add(Organization(id=ORG_B, name="Review Org B"))
        session.add(
            User(
                id=USER_A,
                email="review-a@test.example",
                hashed_password=hash_password("SecurePass123!", settings),
                email_verified=True,
            )
        )
        session.add(
            User(
                id=USER_B,
                email="review-b@test.example",
                hashed_password=hash_password("SecurePass123!", settings),
                email_verified=True,
            )
        )
        session.flush()
        session.add(Membership(user_id=USER_A, organization_id=ORG_A, role=MembershipRole.OWNER))
        session.add(Membership(user_id=USER_B, organization_id=ORG_B, role=MembershipRole.OWNER))
        session.commit()

    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as test_client:
        yield test_client, factory

    app.dependency_overrides.clear()
    engine.dispose()


def _auth(client: TestClient, email: str, password: str = "SecurePass123!") -> dict[str, str]:
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_market_watcher_alert(
    factory: sessionmaker[Session],
    *,
    organization_id: uuid.UUID = ORG_A,
    user_id: uuid.UUID = USER_A,
    condition: str = "order_block",
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    direction: str = "long",
    confidence: float = 0.85,
) -> uuid.UUID:
    with factory() as session:
        service = PaperAlertService(session)
        created = service.create(
            organization_id=organization_id,
            user_id=user_id,
            alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
            message=f"{condition} on {symbol} {timeframe}",
            metadata={
                "source": PaperAlertSource.MARKET_WATCHER.value,
                "condition": condition,
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction,
                "confidence": confidence,
                "reason": "Clean retest setup.",
                "trigger_level": 65000.0,
                "invalidation_level": 64000.0,
                "metrics": {"latest_price": 65100.0},
            },
            dedup_key=f"test:{condition}:{symbol}:{timeframe}:{uuid.uuid4()}",
            skip_dedup=True,
            source=PaperAlertSource.MARKET_WATCHER,
        )
        assert created is not None
        alert_id = created.id
        session.commit()
        return alert_id


def _create_runtime_alert(factory: sessionmaker[Session]) -> uuid.UUID:
    with factory() as session:
        service = PaperAlertService(session)
        created = service.create(
            organization_id=ORG_A,
            user_id=USER_A,
            alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
            message="Runtime setup signal",
            metadata={"source": PaperAlertSource.PAPER_VALIDATION_RUNTIME.value},
            skip_dedup=True,
        )
        assert created is not None
        alert_id = created.id
        session.commit()
        return alert_id


def test_setup_review_lists_only_market_watcher_alerts(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "review-a@test.example")
    mw_id = _create_market_watcher_alert(factory)
    _create_runtime_alert(factory)

    response = test_client.get("/alerts/setup-review", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["alert_id"] == str(mw_id)
    assert body["items"][0]["condition"] == "order_block"


def test_setup_review_tenant_isolation(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, factory = client
    headers_a = _auth(test_client, "review-a@test.example")
    headers_b = _auth(test_client, "review-b@test.example")
    alert_id = _create_market_watcher_alert(factory, organization_id=ORG_A)

    own = test_client.get("/alerts/setup-review", headers=headers_a)
    assert own.status_code == 200
    assert own.json()["total"] == 1

    other = test_client.get("/alerts/setup-review", headers=headers_b)
    assert other.status_code == 200
    assert other.json()["total"] == 0

    patch_resp = test_client.patch(
        f"/alerts/setup-review/{alert_id}",
        headers=headers_b,
        json={"review_status": "watching"},
    )
    assert patch_resp.status_code == 404


def test_setup_review_filters(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "review-a@test.example")
    _create_market_watcher_alert(factory, condition="order_block", symbol="BTCUSDT", confidence=0.9)
    _create_market_watcher_alert(
        factory,
        condition="breakout_retest",
        symbol="ETHUSDT",
        timeframe="1h",
        direction="short",
        confidence=0.6,
    )

    by_symbol = test_client.get("/alerts/setup-review?symbol=ETHUSDT", headers=headers)
    assert by_symbol.status_code == 200
    assert by_symbol.json()["total"] == 1
    assert by_symbol.json()["items"][0]["symbol"] == "ETHUSDT"

    by_condition = test_client.get("/alerts/setup-review?condition=order_block", headers=headers)
    assert by_condition.json()["total"] == 1

    by_timeframe = test_client.get("/alerts/setup-review?timeframe=1h", headers=headers)
    assert by_timeframe.json()["total"] == 1

    by_direction = test_client.get("/alerts/setup-review?direction=short", headers=headers)
    assert by_direction.json()["total"] == 1

    by_confidence = test_client.get("/alerts/setup-review?min_confidence=0.8", headers=headers)
    assert by_confidence.json()["total"] == 1
    assert by_confidence.json()["items"][0]["confidence"] >= 0.8


def test_setup_review_update_works(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, factory = client
    headers = _auth(test_client, "review-a@test.example")
    alert_id = _create_market_watcher_alert(factory)

    response = test_client.patch(
        f"/alerts/setup-review/{alert_id}",
        headers=headers,
        json={
            "review_status": "watching",
            "review_notes": "Clean retest, wait for confirmation",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["review_status"] == "watching"
    assert body["review_notes"] == "Clean retest, wait for confirmation"
    assert body["reviewed_at"] is not None


def test_setup_review_invalid_status_rejected(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "review-a@test.example")
    alert_id = _create_market_watcher_alert(factory)

    response = test_client.patch(
        f"/alerts/setup-review/{alert_id}",
        headers=headers,
        json={"review_status": "accepted"},
    )
    assert response.status_code == 422


def test_setup_review_non_market_watcher_update_rejected(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "review-a@test.example")
    alert_id = _create_runtime_alert(factory)

    response = test_client.patch(
        f"/alerts/setup-review/{alert_id}",
        headers=headers,
        json={"review_status": "ignored"},
    )
    assert response.status_code == 422
    assert "scanner-created" in response.json()["error"]["message"].lower()


def test_setup_review_notes_length_enforced(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "review-a@test.example")
    alert_id = _create_market_watcher_alert(factory)

    response = test_client.patch(
        f"/alerts/setup-review/{alert_id}",
        headers=headers,
        json={"review_status": "important", "review_notes": "x" * 4001},
    )
    assert response.status_code == 422


def test_setup_review_update_emits_audit(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "review-a@test.example")
    alert_id = _create_market_watcher_alert(factory)

    patch_resp = test_client.patch(
        f"/alerts/setup-review/{alert_id}",
        headers=headers,
        json={"review_status": "important"},
    )
    assert patch_resp.status_code == 200

    with factory() as session:
        events = session.scalars(select(AuditLog).where(AuditLog.resource_id == str(alert_id)))
        assert any(
            (row.redacted_metadata or {}).get("action") == "setup_alert_review"
            and (row.redacted_metadata or {}).get("review_status") == "important"
            for row in events
        )


def test_setup_review_summary(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, factory = client
    headers = _auth(test_client, "review-a@test.example")
    alert_id = _create_market_watcher_alert(factory, confidence=0.92)

    summary = test_client.get("/alerts/setup-review/summary", headers=headers)
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["total_unreviewed"] == 1
    assert body["by_condition"]["order_block"] == 1
    assert body["by_symbol"]["BTCUSDT"] == 1
    assert body["latest_created_at"] is not None
    assert body["highest_confidence_alerts"][0]["alert_id"] == str(alert_id)

    test_client.patch(
        f"/alerts/setup-review/{alert_id}",
        headers=headers,
        json={"review_status": "watching"},
    )
    summary_after = test_client.get("/alerts/setup-review/summary", headers=headers)
    assert summary_after.json()["total_watching"] == 1
    assert summary_after.json()["total_unreviewed"] == 0


@patch("app.services.telegram_alert_delivery_service.TelegramAlertDeliveryService.deliver_alert")
@patch("app.services.alert_delivery_service.AlertDeliveryService.deliver_alert")
def test_setup_review_does_not_call_delivery_or_telegram(
    mock_deliver: object,
    mock_telegram: object,
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "review-a@test.example")
    alert_id = _create_market_watcher_alert(factory)

    response = test_client.patch(
        f"/alerts/setup-review/{alert_id}",
        headers=headers,
        json={"review_status": "watching"},
    )
    assert response.status_code == 200
    mock_deliver.assert_not_called()
    mock_telegram.assert_not_called()


def test_setup_review_metadata_redacted(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "review-a@test.example")
    with factory() as session:
        service = PaperAlertService(session)
        created = service.create(
            organization_id=ORG_A,
            user_id=USER_A,
            alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
            message="Sensitive metadata test",
            metadata={
                "source": PaperAlertSource.MARKET_WATCHER.value,
                "condition": "sfp",
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "direction": "long",
                "confidence": 0.7,
                "api_key": "secret-key-value",
            },
            dedup_key=f"test:redact:{uuid.uuid4()}",
            skip_dedup=True,
            source=PaperAlertSource.MARKET_WATCHER,
        )
        assert created is not None
        session.commit()

    response = test_client.get("/alerts/setup-review", headers=headers)
    assert response.status_code == 200
    metadata = response.json()["items"][0]["metadata"]
    assert metadata["api_key"] == "***REDACTED***"


def test_setup_review_real_trading_enabled_still_review_only() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="ENABLE_REAL_TRADING=true"):
        Settings(
            **{
                **_BASE,
                "execution_mode": "trade",
                "enable_real_trading": True,
            }
        )
