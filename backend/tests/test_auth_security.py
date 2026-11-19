"""Slice 22 — httpOnly cookie auth, denylist, and refresh reuse tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models import AuditLog
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import AuditEventType
from app.security.rate_limit import reset_rate_limiter
from app.security.token_denylist import reset_access_token_denylist
from tests.test_deployment_safety import _PRODUCTION_BASE


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    reset_rate_limiter()
    reset_access_token_denylist()


@pytest.fixture
def cookie_settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-secret-key-for-auth-slice-32b-min",
        access_token_expire_minutes=15,
        refresh_token_expire_days=7,
        auth_refresh_cookie_enabled=True,
        auth_omit_refresh_from_body=True,
        auth_cookie_secure=False,
        auth_cookie_samesite="lax",
        access_token_denylist_enabled=True,
        access_token_denylist_use_redis=False,
        rate_limit_use_redis=False,
    )


@pytest.fixture
def cookie_session_factory() -> Iterator[sessionmaker[Session]]:
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

    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def cookie_client(
    cookie_settings: Settings, cookie_session_factory: sessionmaker[Session]
) -> Iterator[TestClient]:
    def _override_session() -> Iterator[Session]:
        with cookie_session_factory() as session:
            yield session

    get_settings.cache_clear()
    app = create_app(settings=cookie_settings)
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def _register(client: TestClient, *, email: str) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "secure-password-1",
            "organization_name": "Cookie Org",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_login_sets_httponly_refresh_cookie(cookie_client: TestClient) -> None:
    cookie_client.post(
        "/auth/register",
        json={
            "email": "cookie-login@example.com",
            "password": "secure-password-1",
            "organization_name": "Cookie Org",
        },
    )
    response = cookie_client.post(
        "/auth/login",
        json={"email": "cookie-login@example.com", "password": "secure-password-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tokens"]["access_token"]
    assert body["tokens"]["refresh_token"] == ""
    cookie_header = response.headers.get("set-cookie", "")
    assert "alphatrade_refresh=" in cookie_header
    assert "httponly" in cookie_header.lower()


def test_refresh_via_cookie_without_body(cookie_client: TestClient) -> None:
    _register(cookie_client, email="cookie-refresh@example.com")
    login = cookie_client.post(
        "/auth/login",
        json={"email": "cookie-refresh@example.com", "password": "secure-password-1"},
    )
    assert login.status_code == 200
    refreshed = cookie_client.post("/auth/refresh", json={})
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]
    assert refreshed.json()["refresh_token"] == ""

    me = cookie_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"},
    )
    assert me.status_code == 200


def test_logout_clears_cookie(cookie_client: TestClient) -> None:
    registered = _register(cookie_client, email="logout-cookie@example.com")
    access = registered["tokens"]["access_token"]
    login = cookie_client.post(
        "/auth/login",
        json={"email": "logout-cookie@example.com", "password": "secure-password-1"},
    )
    assert login.status_code == 200
    logout = cookie_client.post(
        "/auth/logout",
        json={},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert logout.status_code == 200
    cleared = logout.headers.get("set-cookie", "").lower()
    assert "max-age=0" in cleared or 'alphatrade_refresh=""' in cleared or "expires=" in cleared


def test_access_token_denylist_on_logout(cookie_client: TestClient) -> None:
    registered = _register(cookie_client, email="deny@example.com")
    access = registered["tokens"]["access_token"]
    login = cookie_client.post(
        "/auth/login",
        json={"email": "deny@example.com", "password": "secure-password-1"},
    )
    refresh_cookie = login.cookies.get("alphatrade_refresh")
    assert refresh_cookie
    logout = cookie_client.post(
        "/auth/logout",
        json={},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert logout.status_code == 200
    blocked = cookie_client.get(
        "/usage/summary",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert blocked.status_code == 401


def test_refresh_reuse_revokes_family(
    cookie_client: TestClient, cookie_session_factory: sessionmaker[Session]
) -> None:
    _register(cookie_client, email="reuse@example.com")
    login = cookie_client.post(
        "/auth/login",
        json={"email": "reuse@example.com", "password": "secure-password-1"},
    )
    old_refresh = login.cookies.get("alphatrade_refresh")
    assert old_refresh
    first = cookie_client.post("/auth/refresh", json={})
    assert first.status_code == 200
    reuse = cookie_client.post(
        "/auth/refresh",
        json={"refresh_token": old_refresh},
    )
    assert reuse.status_code == 401
    with cookie_session_factory() as session:
        assert session.scalar(
            select(AuditLog).where(AuditLog.action == AuditEventType.AUTH_REFRESH_REUSE)
        )


def test_no_token_leak_in_logs(
    cookie_client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    registered = _register(cookie_client, email="nolog@example.com")
    token = registered["tokens"]["access_token"]
    cookie_client.get("/usage/summary", headers={"Authorization": f"Bearer {token}"})
    assert token not in caplog.text
    assert "secure-password-1" not in caplog.text


def test_docker_safe_cookie_defaults(cookie_settings: Settings) -> None:
    prod = Settings(**{**_PRODUCTION_BASE, "auth_cookie_secure": None})
    assert prod.auth_cookie_secure is None
    from app.security.cookies import refresh_cookie_secure

    assert refresh_cookie_secure(prod) is True

    local = Settings(
        environment="local",
        auth_refresh_cookie_enabled=True,
        auth_cookie_secure=None,
    )
    assert refresh_cookie_secure(local) is False
