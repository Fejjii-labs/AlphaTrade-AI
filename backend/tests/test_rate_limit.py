"""Rate limiting tests (Redis-backed with in-memory fallback, proxy trust)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.security.rate_limit import (
    InMemoryRateLimiter,
    RateLimitExceededError,
    RedisRateLimiter,
    client_ip,
    get_rate_limiter,
    reset_rate_limiter,
)


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


@pytest.fixture
def limit_settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="test-rate-limit-secret-key-32bytes-min",
        rate_limit_use_redis=False,
        rate_limit_allow_in_memory_fallback=True,
    )


@contextmanager
def _client_for(settings: Settings) -> Iterator[TestClient]:
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

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def limit_client(limit_settings: Settings) -> Iterator[TestClient]:
    with _client_for(limit_settings) as client:
        yield client


def test_in_memory_limiter_allows_under_limit() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(3):
        limiter.check("test:key", limit=3, window_seconds=60)


def test_in_memory_limiter_blocks_over_limit() -> None:
    limiter = InMemoryRateLimiter()
    for _ in range(2):
        limiter.check("test:block", limit=2, window_seconds=60)
    with pytest.raises(RateLimitExceededError, match="Too many requests"):
        limiter.check("test:block", limit=2, window_seconds=60)


def test_register_rate_limit_returns_429(limit_client: TestClient) -> None:
    for index in range(10):
        response = limit_client.post(
            "/auth/register",
            json={
                "email": f"user{index}@example.com",
                "password": "secure-password-1",
                "organization_name": f"Blocked Org {index}",
            },
        )
        assert response.status_code == 201, response.text

    blocked = limit_client.post(
        "/auth/register",
        json={
            "email": "one-too-many@example.com",
            "password": "secure-password-1",
            "organization_name": "Blocked Org overflow",
        },
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limit_exceeded"


def test_refresh_endpoint_is_rate_limited(limit_client: TestClient) -> None:
    invalid_token = "x" * 32
    for _ in range(60):
        response = limit_client.post("/auth/refresh", json={"refresh_token": invalid_token})
        assert response.status_code in {401, 429}, response.text

    blocked = limit_client.post("/auth/refresh", json={"refresh_token": invalid_token})
    assert blocked.status_code == 429


def test_reset_rate_limiter_clears_shared_in_memory_state(limit_client: TestClient) -> None:
    """Regression: global limiter singleton must not leak counts across reset boundaries."""
    for index in range(10):
        response = limit_client.post(
            "/auth/register",
            json={
                "email": f"reset-leak-{index}@example.com",
                "password": "secure-password-1",
                "organization_name": f"Reset Org {index}",
            },
        )
        assert response.status_code == 201, response.text

    blocked = limit_client.post(
        "/auth/register",
        json={
            "email": "reset-leak-overflow@example.com",
            "password": "secure-password-1",
            "organization_name": "Reset Org overflow",
        },
    )
    assert blocked.status_code == 429

    reset_rate_limiter()

    recovered = limit_client.post(
        "/auth/register",
        json={
            "email": "reset-leak-recovered@example.com",
            "password": "secure-password-1",
            "organization_name": "Reset Org recovered",
        },
    )
    assert recovered.status_code == 201, recovered.text


def test_register_succeeds_in_isolated_test_after_global_exhaustion(
    limit_client: TestClient,
) -> None:
    """Regression: conftest autouse reset prevents cross-test 429 flakes in full pytest runs."""
    response = limit_client.post(
        "/auth/register",
        json={
            "email": "isolated-register@example.com",
            "password": "secure-password-1",
            "organization_name": "Isolated Org",
        },
    )
    assert response.status_code == 201, response.text


def test_redis_limiter_falls_back_when_redis_unavailable(limit_settings: Settings) -> None:
    settings = limit_settings.model_copy(
        update={
            "rate_limit_use_redis": True,
            "redis_url": "redis://127.0.0.1:59999/0",
            "rate_limit_allow_in_memory_fallback": True,
        }
    )
    reset_rate_limiter()
    limiter = get_rate_limiter(settings)
    assert isinstance(limiter, RedisRateLimiter)
    assert limiter.using_redis is False
    limiter.check("fallback:key", limit=5, window_seconds=60)


def test_redis_required_without_fallback_raises(limit_settings: Settings) -> None:
    settings = limit_settings.model_copy(
        update={
            "rate_limit_use_redis": True,
            "redis_url": "redis://127.0.0.1:59999/0",
            "rate_limit_allow_in_memory_fallback": False,
        }
    )
    reset_rate_limiter()
    with pytest.raises(RuntimeError, match="Redis rate limiting is required"):
        get_rate_limiter(settings)


# --- AT-018: proxy trust for client IP resolution ---

_PEER = "203.0.113.10"


def _make_request(
    *,
    client_host: str | None = _PEER,
    forwarded_for: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "query_string": b"",
        "client": (client_host, 40000) if client_host else None,
    }
    return Request(scope)


def test_client_ip_ignores_spoofed_xff_with_zero_trusted_hops(limit_settings: Settings) -> None:
    request = _make_request(forwarded_for="6.6.6.6, 7.7.7.7")
    assert client_ip(request, limit_settings) == _PEER


def test_client_ip_trusts_rightmost_entry_with_one_hop(limit_settings: Settings) -> None:
    settings = limit_settings.model_copy(update={"trusted_proxy_hops": 1})
    request = _make_request(forwarded_for="6.6.6.6, 198.51.100.7")
    assert client_ip(request, settings) == "198.51.100.7"


def test_client_ip_selects_entry_by_trusted_hop_count(limit_settings: Settings) -> None:
    settings = limit_settings.model_copy(update={"trusted_proxy_hops": 2})
    request = _make_request(forwarded_for="6.6.6.6, 198.51.100.7, 192.0.2.1")
    assert client_ip(request, settings) == "198.51.100.7"


def test_client_ip_falls_back_to_peer_when_header_shorter_than_hops(
    limit_settings: Settings,
) -> None:
    settings = limit_settings.model_copy(update={"trusted_proxy_hops": 2})
    request = _make_request(forwarded_for="198.51.100.7")
    assert client_ip(request, settings) == _PEER


def test_client_ip_falls_back_to_peer_on_malformed_entry(limit_settings: Settings) -> None:
    settings = limit_settings.model_copy(update={"trusted_proxy_hops": 1})
    request = _make_request(forwarded_for="6.6.6.6, not-an-ip")
    assert client_ip(request, settings) == _PEER


def test_client_ip_without_header_uses_peer(limit_settings: Settings) -> None:
    settings = limit_settings.model_copy(update={"trusted_proxy_hops": 1})
    request = _make_request()
    assert client_ip(request, settings) == _PEER


def test_client_ip_accepts_ipv6_forwarded_entry(limit_settings: Settings) -> None:
    settings = limit_settings.model_copy(update={"trusted_proxy_hops": 1})
    request = _make_request(forwarded_for="2001:db8::1")
    assert client_ip(request, settings) == "2001:db8::1"


def test_client_ip_unknown_when_no_peer(limit_settings: Settings) -> None:
    request = _make_request(client_host=None)
    assert client_ip(request, limit_settings) == "unknown"


def test_spoofed_xff_does_not_bypass_register_rate_limit(limit_client: TestClient) -> None:
    """With zero trusted hops (default), unique spoofed XFF values share one bucket."""
    for index in range(10):
        response = limit_client.post(
            "/auth/register",
            json={
                "email": f"spoof-{index}@example.com",
                "password": "secure-password-1",
                "organization_name": f"Spoof Org {index}",
            },
            headers={"X-Forwarded-For": f"6.6.6.{index}"},
        )
        assert response.status_code == 201, response.text

    blocked = limit_client.post(
        "/auth/register",
        json={
            "email": "spoof-overflow@example.com",
            "password": "secure-password-1",
            "organization_name": "Spoof Org overflow",
        },
        headers={"X-Forwarded-For": "6.6.6.255"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limit_exceeded"


def test_trusted_proxy_hop_separates_clients_by_forwarded_ip(limit_settings: Settings) -> None:
    """With one trusted hop, distinct proxy-appended client IPs get distinct buckets."""
    settings = limit_settings.model_copy(update={"trusted_proxy_hops": 1})
    with _client_for(settings) as client:
        for index in range(11):
            response = client.post(
                "/auth/register",
                json={
                    "email": f"proxied-{index}@example.com",
                    "password": "secure-password-1",
                    "organization_name": f"Proxied Org {index}",
                },
                headers={"X-Forwarded-For": f"198.51.100.{index}"},
            )
            assert response.status_code == 201, response.text


# --- L13: per-email failed-login throttle ---


def _register(client: TestClient, email: str) -> None:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "secure-password-1",
            "organization_name": f"Login Throttle {email}",
        },
    )
    assert response.status_code == 201, response.text


def _login(client: TestClient, email: str, password: str, *, ip: str) -> int:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers={"X-Forwarded-For": ip},
    )
    return response.status_code


@pytest.fixture
def email_limit_client(limit_settings: Settings) -> Iterator[TestClient]:
    settings = limit_settings.model_copy(
        update={"trusted_proxy_hops": 1, "auth_login_email_failure_limit": 3}
    )
    with _client_for(settings) as client:
        yield client


def test_login_email_lockout_spans_ips_and_blocks_correct_password(
    email_limit_client: TestClient,
) -> None:
    _register(email_limit_client, "victim@example.com")
    for index in range(3):
        status = _login(
            email_limit_client, "victim@example.com", "wrong-password-1", ip=f"198.51.100.{index}"
        )
        assert status == 401

    # A fresh IP with the right password is refused before the password is checked.
    assert (
        _login(email_limit_client, "Victim@Example.com", "secure-password-1", ip="192.0.2.9") == 429
    )
    # Other accounts are unaffected.
    _register(email_limit_client, "bystander@example.com")
    assert (
        _login(email_limit_client, "bystander@example.com", "secure-password-1", ip="192.0.2.9")
        == 200
    )


def test_login_email_lockout_treats_unknown_email_the_same(email_limit_client: TestClient) -> None:
    for _ in range(3):
        assert (
            _login(email_limit_client, "nobody@example.com", "wrong-password-1", ip="192.0.2.1")
            == 401
        )
    assert (
        _login(email_limit_client, "nobody@example.com", "wrong-password-1", ip="192.0.2.2") == 429
    )


def test_successful_logins_do_not_count_toward_email_lockout(
    email_limit_client: TestClient,
) -> None:
    _register(email_limit_client, "regular@example.com")
    for index in range(5):
        status = _login(
            email_limit_client, "regular@example.com", "secure-password-1", ip=f"192.0.2.{index}"
        )
        assert status == 200


def test_login_email_limiter_keys_are_hashed_and_peek_does_not_count() -> None:
    from app.security.rate_limit import _login_email_key

    key = _login_email_key(" Victim@Example.com ")
    assert key == _login_email_key("victim@example.com")
    assert "victim" not in key and "@" not in key

    limiter = InMemoryRateLimiter()
    for _ in range(5):
        assert limiter.is_limited(key, limit=2, window_seconds=60) is False
    limiter.check(key, limit=2, window_seconds=60)
    limiter.check(key, limit=2, window_seconds=60)
    assert limiter.is_limited(key, limit=2, window_seconds=60) is True


def test_redis_is_limited_fails_closed_without_fallback(limit_settings: Settings) -> None:
    class _BrokenRedis:
        def get(self, _key: str) -> None:
            raise ConnectionError("down")

    settings = limit_settings.model_copy(update={"rate_limit_allow_in_memory_fallback": False})
    limiter = RedisRateLimiter(settings, InMemoryRateLimiter())
    limiter._redis = _BrokenRedis()
    assert limiter.is_limited("auth:login:email:x", limit=10, window_seconds=60) is True
