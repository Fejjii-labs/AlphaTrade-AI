"""Authentication and tenant security tests."""

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
from app.db.session import get_session
from app.main import create_app
from app.security.rate_limit import reset_rate_limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    reset_rate_limiter()


@pytest.fixture
def auth_settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-secret-key-for-auth-slice-32b-min",
        access_token_expire_minutes=15,
        refresh_token_expire_days=7,
        access_token_denylist_use_redis=False,
    )


@pytest.fixture
def auth_client(auth_settings: Settings) -> Iterator[TestClient]:
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
    app = create_app(settings=auth_settings)
    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _register(client: TestClient, *, email: str, org: str = "Alpha Org") -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "secure-password-1",
            "organization_name": org,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_register_login_refresh_logout_flow(auth_client: TestClient) -> None:
    registered = _register(auth_client, email="trader@example.com")
    assert registered["tokens"]["access_token"]
    assert registered["tokens"]["refresh_token"]
    assert registered["user"]["email"] == "trader@example.com"

    login = auth_client.post(
        "/auth/login",
        json={"email": "trader@example.com", "password": "secure-password-1"},
    )
    assert login.status_code == 200
    refresh_token = login.json()["tokens"]["refresh_token"]

    refreshed = auth_client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200
    new_refresh = refreshed.json()["refresh_token"]
    assert new_refresh != refresh_token

    auth_client.post("/auth/logout", json={"refresh_token": new_refresh})
    revoked = auth_client.post("/auth/refresh", json={"refresh_token": new_refresh})
    assert revoked.status_code == 401


def test_duplicate_email_rejected(auth_client: TestClient) -> None:
    _register(auth_client, email="dup@example.com")
    duplicate = auth_client.post(
        "/auth/register",
        json={
            "email": "dup@example.com",
            "password": "secure-password-1",
            "organization_name": "Other Org",
        },
    )
    assert duplicate.status_code == 422


def test_login_failure(auth_client: TestClient) -> None:
    _register(auth_client, email="known@example.com")
    bad = auth_client.post(
        "/auth/login",
        json={"email": "known@example.com", "password": "wrong-password"},
    )
    assert bad.status_code == 401


def test_password_validation(auth_client: TestClient) -> None:
    short = auth_client.post(
        "/auth/register",
        json={
            "email": "short@example.com",
            "password": "short",
            "organization_name": "Org",
        },
    )
    assert short.status_code == 422

    too_long = auth_client.post(
        "/auth/register",
        json={
            "email": "long@example.com",
            "password": "x" * 129,
            "organization_name": "Org",
        },
    )
    assert too_long.status_code == 422


def test_protected_route_requires_token(auth_client: TestClient) -> None:
    response = auth_client.get("/audit/events")
    assert response.status_code == 401


def test_protected_route_with_token(auth_client: TestClient) -> None:
    registered = _register(auth_client, email="protected@example.com")
    token = registered["tokens"]["access_token"]
    response = auth_client.get(
        "/audit/events",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_me_endpoint(auth_client: TestClient) -> None:
    registered = _register(auth_client, email="me@example.com")
    token = registered["tokens"]["access_token"]
    response = auth_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["email"] == "me@example.com"
    assert body["organization"]["name"] == "Alpha Org"
    # The org owner switch keys on the membership role, not the user-level role.
    assert body["membership_role"] == "owner"
    assert body["user"]["role"] != "owner"
    assert registered["membership_role"] == "owner"


def test_tenant_isolation_on_watchlist(auth_client: TestClient) -> None:
    user_a = _register(auth_client, email="a@example.com", org="Org A")
    token_a = user_a["tokens"]["access_token"]

    create = auth_client.post(
        "/market/watchlist",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "symbol": "BTCUSDT",
            "exchange": "mock",
            "timeframes": ["1h"],
            "strategy_ids": ["htf_trend_pullback"],
            "enabled": True,
        },
    )
    assert create.status_code == 200
    item_id = create.json()["id"]

    user_b = _register(auth_client, email="b@example.com", org="Org B")
    token_b = user_b["tokens"]["access_token"]

    foreign_list = auth_client.get(
        "/market/watchlist",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert foreign_list.status_code == 200
    assert foreign_list.json() == []

    foreign_delete = auth_client.delete(
        f"/market/watchlist/{item_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert foreign_delete.status_code == 404


def test_audit_metadata_does_not_leak_tokens(
    auth_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    registered = _register(auth_client, email="audit@example.com")
    token = registered["tokens"]["access_token"]
    refresh = registered["tokens"]["refresh_token"]
    auth_client.get("/audit/events", headers={"Authorization": f"Bearer {token}"})
    assert "secure-password-1" not in caplog.text
    assert refresh not in caplog.text
    assert token not in caplog.text
