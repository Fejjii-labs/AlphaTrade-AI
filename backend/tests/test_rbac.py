"""RBAC enforcement tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models import Membership
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import MembershipRole
from app.security.rate_limit import reset_rate_limiter


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


@pytest.fixture
def rbac_settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-rbac-secret-key-32-bytes-minimum",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
    )


@pytest.fixture
def rbac_client(rbac_settings: Settings) -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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
    app = create_app(settings=rbac_settings)
    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as client:
        yield client, factory

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _register(client: TestClient, *, email: str) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "secure-password-1",
            "organization_name": f"RBAC Org {email}",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


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


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_viewer_can_read_proposals(rbac_client: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, factory = rbac_client
    registered = _register(client, email="viewer@example.com")
    _set_membership_role(
        factory,
        user_id=uuid.UUID(registered["user"]["id"]),
        role=MembershipRole.VIEWER,
    )
    token = registered["tokens"]["access_token"]

    listed = client.get("/proposals", headers=_auth_headers(token))
    assert listed.status_code == 200


def test_viewer_cannot_create_proposal(
    rbac_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = rbac_client
    registered = _register(client, email="viewer-block@example.com")
    _set_membership_role(
        factory, user_id=uuid.UUID(registered["user"]["id"]), role=MembershipRole.VIEWER
    )
    token = registered["tokens"]["access_token"]

    response = client.post(
        "/proposals",
        headers=_auth_headers(token),
        json={
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "strategy_id": "htf_trend_pullback",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "direction": "long",
            "entry_price": "60000",
            "position_size": "0.01",
            "leverage": "3",
            "exit": {
                "invalidation": "Close below stop.",
                "stop_loss": "58000",
                "take_profits": [{"price": "62000", "size_fraction": 0.5}],
            },
            "confidence": 0.7,
            "risk_level": "medium",
            "rationale": "test",
            "approval_required": True,
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_owner_can_mutate_journal(rbac_client: tuple[TestClient, sessionmaker[Session]]) -> None:
    client, _factory = rbac_client
    registered = _register(client, email="owner@example.com")
    token = registered["tokens"]["access_token"]

    response = client.post(
        "/journal/entries",
        headers=_auth_headers(token),
        json={
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "direction": "long",
            "entry_rationale": "Owner note",
            "lessons": "Allowed",
            "tags": ["review"],
        },
    )
    assert response.status_code == 200


def test_pending_invitation_cannot_grant_membership(
    rbac_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.db.models import OrganizationInvitation
    from app.security.account_tokens import hash_account_token

    client, factory = rbac_client
    owner = _register(client, email="invite-owner@example.com")
    viewer = _register(client, email="invite-viewer@example.com")
    owner_org = uuid.UUID(owner["organization"]["id"])
    viewer_id = uuid.UUID(viewer["user"]["id"])
    _set_membership_role(factory, user_id=viewer_id, role=MembershipRole.VIEWER)
    raw_token = "parked-invitation-test-token"
    with factory() as session:
        invitation = OrganizationInvitation(
            organization_id=owner_org,
            email=viewer["user"]["email"],
            role=MembershipRole.TRADER,
            invited_by_user_id=uuid.UUID(owner["user"]["id"]),
            token_hash=hash_account_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        session.add(invitation)
        session.commit()
        invitation_id = invitation.id

    response = client.post(
        f"/organizations/invitations/{invitation_id}/accept",
        headers=_auth_headers(viewer["tokens"]["access_token"]),
        json={"token": raw_token},
    )
    assert response.status_code == 404
    with factory() as session:
        assert (
            session.scalar(
                select(Membership).where(
                    Membership.user_id == viewer_id, Membership.organization_id == owner_org
                )
            )
            is None
        )
        assert session.get(OrganizationInvitation, invitation_id).accepted_at is None
