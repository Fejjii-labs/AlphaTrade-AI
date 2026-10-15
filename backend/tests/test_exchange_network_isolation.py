"""Guards that the test suite never reaches a real BloFin host at app startup."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, Settings
from app.core.exchange_readiness import run_exchange_demo_startup_check
from app.main import create_app
from app.providers.exchange.base import AccountPermissions
from app.providers.registry import ProviderRegistry
from tests.support.network_guard import OutboundNetworkBlockedError, block_outbound_network

_DEMO_HOST = "demo-trading-openapi.blofin.com"
_DEMO_SETTINGS = {
    "environment": "local",
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


@dataclass
class _StubAccount:
    name: str = "blofin-demo-account"
    calls: list[str] = field(default_factory=list)

    def get_account_permissions(self) -> AccountPermissions:
        self.calls.append("permissions")
        return AccountPermissions(
            can_read=True,
            can_trade=True,
            can_withdraw=True,
            can_transfer=False,
            raw_scopes=("read", "trade", "withdraw"),
        )


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


def test_suite_default_disables_startup_scope_probe() -> None:
    settings = Settings(_env_file=None, **_DEMO_SETTINGS)
    assert settings.exchange_demo_startup_scope_probe_enabled is False


def test_setting_defaults_on_outside_the_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXCHANGE_DEMO_STARTUP_SCOPE_PROBE_ENABLED", raising=False)
    assert Settings(_env_file=None).exchange_demo_startup_scope_probe_enabled is True


def test_demo_app_startup_opens_no_real_socket() -> None:
    """Building and starting a BloFin-demo app must not touch the network."""
    with block_outbound_network() as attempts:
        app = create_app(settings=Settings(_env_file=None, **_DEMO_SETTINGS))
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
    assert attempts == [], f"startup reached a real host: {attempts}"


def test_opted_in_probe_uses_mock_transport_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.exchange import factory as exchange_factory

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if "query-apikey" in request.url.path:
            return httpx.Response(
                200, json={"code": "0", "data": {"apiKey": "k", "readOnly": 0, "type": 2}}
            )
        return httpx.Response(200, json={"code": "0", "data": []})

    original = exchange_factory.build_blofin_client

    def _build(settings_obj, *, transport=None, **kwargs):
        return original(settings_obj, transport=transport or httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(exchange_factory, "build_blofin_client", _build)
    settings = Settings(
        _env_file=None, **{**_DEMO_SETTINGS, "exchange_demo_startup_scope_probe_enabled": True}
    )
    with block_outbound_network() as attempts, TestClient(create_app(settings=settings)):
        pass
    assert attempts == []
    assert any("query-apikey" in path for path in seen), seen


@pytest.mark.parametrize("environment", [Environment.STAGING, Environment.PRODUCTION])
def test_deployed_environments_ignore_the_disable_flag(environment: Environment) -> None:
    """Staging/production always run the scope probe (safety unchanged)."""
    base = Settings(
        _env_file=None, **{**_DEMO_SETTINGS, "exchange_demo_startup_scope_probe_enabled": False}
    )
    settings = base.model_copy(update={"environment": environment})
    account = _StubAccount()
    registry = ProviderRegistry()
    registry.register(account)
    with pytest.raises(ValueError, match="withdrawal/transfer scope"):
        run_exchange_demo_startup_check(settings, registry)
    assert account.calls == ["permissions"]


def test_local_disable_flag_skips_only_the_network_probe() -> None:
    settings = Settings(
        _env_file=None, **{**_DEMO_SETTINGS, "exchange_demo_startup_scope_probe_enabled": False}
    )
    account = _StubAccount()
    registry = ProviderRegistry()
    registry.register(account)
    run_exchange_demo_startup_check(settings, registry)
    assert account.calls == []
    # Local invariants still apply when the probe is skipped.
    unsafe = settings.model_copy(update={"blofin_demo_rest_base_url": "https://evil.example"})
    with pytest.raises(ValueError, match="allowlisted"):
        run_exchange_demo_startup_check(unsafe, registry)
