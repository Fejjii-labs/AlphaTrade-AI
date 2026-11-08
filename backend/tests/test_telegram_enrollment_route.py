"""L16: enrollment start is OWNER/TRADER only and rate limited across requests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import telegram_enrollment
from app.core.auth import get_current_tenant
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import MembershipRole
from app.security.rate_limit import reset_rate_limiter
from app.security.tenant import TenantContext
from app.telegram_security.memory import InMemoryTelegramSecurityStore

_PATH = "/telegram-paper/enrollment/start"


@pytest.fixture
def store_calls(monkeypatch: pytest.MonkeyPatch) -> list[InMemoryTelegramSecurityStore]:
    calls: list[InMemoryTelegramSecurityStore] = []

    def _store(_factory: object) -> InMemoryTelegramSecurityStore:
        store = InMemoryTelegramSecurityStore()
        calls.append(store)
        return store

    monkeypatch.setattr(telegram_enrollment, "build_postgres_telegram_security_store", _store)
    monkeypatch.setattr(telegram_enrollment, "get_session_factory", lambda: None)
    return calls


def _client(role: MembershipRole) -> Iterator[TestClient]:
    settings = Settings(
        environment="local",
        log_json=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-telegram-enrollment-secret-32bytes",
        rate_limit_use_redis=False,
        rate_limit_allow_in_memory_fallback=True,
        telegram_bot_id="enrollment-test-bot",
    )
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _session() -> Iterator[Session]:
        with factory() as session:
            yield session

    tenant = TenantContext(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="member@example.com",
        membership_role=role,
    )
    reset_rate_limiter()
    get_settings.cache_clear()
    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_current_tenant] = lambda: tenant
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        reset_rate_limiter()
        engine.dispose()


@pytest.fixture
def viewer_client() -> Iterator[TestClient]:
    yield from _client(MembershipRole.VIEWER)


@pytest.fixture
def trader_client() -> Iterator[TestClient]:
    yield from _client(MembershipRole.TRADER)


def test_viewer_cannot_start_enrollment(
    viewer_client: TestClient, store_calls: list[InMemoryTelegramSecurityStore]
) -> None:
    response = viewer_client.post(_PATH)
    assert response.status_code == 403, response.text
    assert "token" not in response.json()
    assert store_calls == []


def test_trader_enrollment_is_limited_across_requests(
    trader_client: TestClient, store_calls: list[InMemoryTelegramSecurityStore]
) -> None:
    for _ in range(10):
        response = trader_client.post(_PATH)
        assert response.status_code == 200, response.text
        assert response.json()["bot_id"] == "enrollment-test-bot"
    blocked = trader_client.post(_PATH)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limit_exceeded"
    # Each request built its own protocol limiter; only the shared one tripped.
    assert len(store_calls) == 10
