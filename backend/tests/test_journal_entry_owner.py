"""Journal entry routes require owner (not only organization)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Membership, Organization, TradeJournal, User
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import MembershipRole
from app.security.passwords import hash_password
from app.security.rate_limit import reset_rate_limiter

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000009501")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000009502")
USER_A = uuid.UUID("00000000-0000-0000-0000-000000009511")
USER_B = uuid.UUID("00000000-0000-0000-0000-000000009512")
OTHER_TRADER_A = uuid.UUID("00000000-0000-0000-0000-000000009514")

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "journal-entry-owner-test-secret-32ch",
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
        session.add(Organization(id=ORG_A, name="Entry Org A"))
        session.add(Organization(id=ORG_B, name="Entry Org B"))
        for user_id, email in (
            (USER_A, "entry-a@test.example"),
            (USER_B, "entry-b@test.example"),
        ):
            session.add(
                User(
                    id=user_id,
                    email=email,
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


def _seed_other_trader_in_org_a(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        if session.get(User, OTHER_TRADER_A) is not None:
            return
        settings = Settings(**_BASE)
        session.add(
            User(
                id=OTHER_TRADER_A,
                email="entry-other-trader-a@test.example",
                hashed_password=hash_password("SecurePass123!", settings),
                email_verified=True,
            )
        )
        session.flush()
        session.add(
            Membership(
                user_id=OTHER_TRADER_A,
                organization_id=ORG_A,
                role=MembershipRole.TRADER,
            )
        )
        session.commit()


def _auth(client: TestClient, email: str, password: str = "SecurePass123!") -> dict[str, str]:
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _entry_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "direction": "long",
        "entry_rationale": "Pullback into support.",
        "lessons": "Wait for confirmation.",
        "tags": ["pullback"],
    }
    body.update(overrides)
    return body


def _create_entry(client: TestClient, headers: dict[str, str]) -> str:
    created = client.post("/journal/entries", json=_entry_body(), headers=headers)
    assert created.status_code == 200, created.text
    return created.json()["id"]


def test_journal_entry_routes_require_owner(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    _seed_other_trader_in_org_a(factory)
    headers_owner = _auth(test_client, "entry-a@test.example")
    headers_other_org = _auth(test_client, "entry-b@test.example")
    headers_other_user = _auth(test_client, "entry-other-trader-a@test.example")
    entry_id = _create_entry(test_client, headers_owner)

    assert test_client.get(f"/journal/entries/{entry_id}", headers=headers_owner).status_code == 200
    assert (
        test_client.get(f"/journal/entries/{entry_id}", headers=headers_other_org).status_code
        == 404
    )
    assert (
        test_client.get(f"/journal/entries/{entry_id}", headers=headers_other_user).status_code
        == 404
    )

    updated = test_client.patch(
        f"/journal/entries/{entry_id}",
        json={"lessons": "Owner lesson"},
        headers=headers_owner,
    )
    assert updated.status_code == 200
    denied_patch = test_client.patch(
        f"/journal/entries/{entry_id}",
        json={"lessons": "Intruder"},
        headers=headers_other_user,
    )
    assert denied_patch.status_code == 404
    with factory() as session:
        row = session.get(TradeJournal, uuid.UUID(entry_id))
        assert row is not None
        assert row.lessons == "Owner lesson"

    assert (
        test_client.delete(f"/journal/entries/{entry_id}", headers=headers_other_user).status_code
        == 404
    )
    with factory() as session:
        assert session.get(TradeJournal, uuid.UUID(entry_id)) is not None

    deleted = test_client.delete(f"/journal/entries/{entry_id}", headers=headers_owner)
    assert deleted.status_code == 204


def test_discipline_routes_require_entry_owner(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    _seed_other_trader_in_org_a(factory)
    headers_owner = _auth(test_client, "entry-a@test.example")
    headers_other_user = _auth(test_client, "entry-other-trader-a@test.example")
    entry_id = _create_entry(test_client, headers_owner)

    analysis = test_client.get(
        f"/journal/entries/{entry_id}/discipline-analysis", headers=headers_owner
    )
    assert analysis.status_code == 200

    denied_analysis = test_client.get(
        f"/journal/entries/{entry_id}/discipline-analysis", headers=headers_other_user
    )
    assert denied_analysis.status_code == 404

    denied_candidate = test_client.post(
        f"/journal/entries/{entry_id}/discipline-analysis/candidates",
        params={"category": "early_exit"},
        headers=headers_other_user,
    )
    assert denied_candidate.status_code == 404
