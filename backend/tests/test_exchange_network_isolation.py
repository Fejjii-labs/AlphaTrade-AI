"""Guards that the test suite never reaches a real BloFin host at app startup.

The money-movement scope check stays on. Tests install a MockTransport on
``build_blofin_client`` so ``_verify_no_money_movement`` runs against that mock
instead of a real demo host.
"""

from __future__ import annotations

import socket
from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.main import create_app
from app.providers.exchange import factory as exchange_factory
from app.providers.registry import build_default_registry
from tests.support.network_guard import OutboundNetworkBlockedError, block_outbound_network

_DEMO_HOST = "demo-trading-openapi.blofin.com"
_DEMO_SETTINGS = {
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "exchange-isolation-test-secret-min-32-bytes",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
    "exchange_mode": "paper_exchange_demo",
    "blofin_demo_enabled": True,
    "blofin_api_key": "demo-key",
    "blofin_api_secret": "demo-secret",
    "blofin_api_passphrase": "demo-pass",
    "blofin_demo_rest_base_url": f"https://{_DEMO_HOST}",
    "blofin_max_retries": 0,
}

# Staging must pass Settings validation. paper_exchange_demo is allowed there.
_STAGING_DEMO_SETTINGS = {
    "environment": "staging",
    "jwt_secret": "x" * 32,
    "database_url": "postgresql+psycopg://user:pass@db.example.com:5432/alphatrade",
    "redis_url": "redis://redis.example.com:6379/0",
    "qdrant_url": "https://qdrant.example.com",
    "openai_api_key": "sk-test-not-a-real-key",
    "cors_origins": "https://app.example.com",
    "auth_refresh_cookie_enabled": True,
    "auth_cookie_secure": True,
    "auth_cookie_samesite": "none",
    "enable_real_trading": False,
    "execution_mode": "paper",
    "provider_mode": "fallback",
    "rate_limit_use_redis": True,
    "rate_limit_allow_in_memory_fallback": False,
    "trusted_proxy_hops": 1,
    "debug": False,
    "log_json": False,
    "exchange_mode": "paper_exchange_demo",
    "blofin_demo_enabled": True,
    "blofin_api_key": "demo-key",
    "blofin_api_secret": "demo-secret",
    "blofin_api_passphrase": "demo-pass",
    "blofin_demo_rest_base_url": f"https://{_DEMO_HOST}",
    "blofin_max_retries": 0,
}


def _install_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Route BloFin client construction through a MockTransport.

    The real ``_verify_no_money_movement`` check still runs; only the socket is fake.
    """
    original = exchange_factory.build_blofin_client

    def _build(settings_obj: Settings, *, transport: httpx.BaseTransport | None = None, **kwargs):
        return original(
            settings_obj,
            transport=transport or httpx.MockTransport(handler),
            **kwargs,
        )

    monkeypatch.setattr(exchange_factory, "build_blofin_client", _build)


def _read_trade_handler(request: httpx.Request) -> httpx.Response:
    if "query-apikey" in request.url.path:
        return httpx.Response(
            200, json={"code": "0", "data": {"apiKey": "k", "readOnly": 0, "type": 2}}
        )
    return httpx.Response(200, json={"code": "0", "data": []})


def _withdraw_handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"code": "0", "data": {"permissions": "read,trade,withdraw"}})


@pytest.mark.parametrize(
    ("host", "port"),
    [(_DEMO_HOST, 443), ("172.64.146.160", 443), ("104.18.41.96", 443)],
)
def test_guard_refuses_and_records_exchange_hosts(host: str, port: int) -> None:
    with block_outbound_network() as attempts, pytest.raises(OutboundNetworkBlockedError):
        socket.create_connection((host, port), timeout=1)
    assert attempts, "guard must record the refused attempt"


def test_guard_allows_loopback() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        with block_outbound_network() as attempts:
            client = socket.create_connection(server.getsockname(), timeout=1)
            client.close()
        assert attempts == []
    finally:
        server.close()


def test_demo_app_startup_opens_no_real_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """Building and starting a BloFin-demo app must not touch the network.

    The startup scope probe still runs, against the mock transport.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return _read_trade_handler(request)

    _install_mock_transport(monkeypatch, handler)
    settings = Settings(_env_file=None, environment="local", **_DEMO_SETTINGS)
    with block_outbound_network() as attempts:
        app = create_app(settings=settings)
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
    assert attempts == [], f"startup reached a real host: {attempts}"
    assert any("query-apikey" in path for path in seen), seen


@pytest.mark.parametrize("environment", [None, "local"])
def test_local_registry_refuses_withdraw_scope(
    monkeypatch: pytest.MonkeyPatch, environment: str | None
) -> None:
    """Unset ENVIRONMENT defaults to local and still refuses a withdraw key."""
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    values = dict(_DEMO_SETTINGS)
    if environment is not None:
        values["environment"] = environment
    settings = Settings(_env_file=None, **values)
    assert settings.environment is Environment.LOCAL
    _install_mock_transport(monkeypatch, _withdraw_handler)
    with (
        block_outbound_network(),
        pytest.raises(ValueError, match="withdrawal/transfer scope"),
    ):
        build_default_registry(settings)


def test_staging_registry_refuses_withdraw_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Staging Settings validation still refuses a withdraw-scoped demo key.

    Registry construction would otherwise open a Qdrant client. That client is
    not part of the scope check, and a live one keeps retrying ``qdrant.example.com``
    after this test. The lookup itself belongs to a separate follow-up.
    """
    monkeypatch.setattr("app.providers.qdrant.QdrantVectorStore._connect", lambda self: None)
    settings = Settings(_env_file=None, **_STAGING_DEMO_SETTINGS)
    assert settings.environment is Environment.STAGING
    _install_mock_transport(monkeypatch, _withdraw_handler)
    with (
        block_outbound_network(),
        pytest.raises(ValueError, match="withdrawal/transfer scope"),
    ):
        build_default_registry(settings)
