"""Black-box safety tests for exchange demo connectivity (Slice 64).

Consolidates critical safety scenarios required before staging demo API keys.
Each test is independent and uses mocks — no real venue access.
"""

from __future__ import annotations

import contextlib
import inspect
from dataclasses import dataclass

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exchange_readiness import run_exchange_demo_startup_check
from app.core.exchange_safety import BLOFIN_DEMO_HOST_ALLOWLIST, is_allowlisted_demo_host
from app.main import create_app
from app.providers.alert_delivery.telegram import TelegramAlertDeliveryProvider
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.providers.exchange.base import AccountPermissions
from app.providers.exchange.blofin_account import BloFinAccountProvider
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.factory import resolve_exchange_provider
from app.providers.registry import ProviderRegistry
from app.tools.registry import build_default_registry as build_tool_registry
from app.workers.scanner import build_market_scan_scanner

_ALLOWED_OUTBOUND_TELEGRAM_ROUTE_PATHS = {
    "/alerts/test-telegram",
    "/alerts/{alert_id}/deliver-telegram",
    # Read-only posture. create_app does not mount /webhooks/telegram/paper.
    "/health/telegram-paper-activation",
    # Authenticated challenge start. Completion is the Telegram process poll.
    "/telegram-paper/enrollment/start",
}

_DEMO_HOST = next(iter(BLOFIN_DEMO_HOST_ALLOWLIST))
_DEMO_REST = f"https://{_DEMO_HOST}"
_DEMO_OK = {
    "exchange_mode": "paper_exchange_demo",
    "blofin_demo_enabled": True,
    "blofin_api_key": "demo-key",
    "blofin_api_secret": "demo-secret",
    "blofin_api_passphrase": "demo-pass",
    "blofin_demo_rest_base_url": _DEMO_REST,
}


@dataclass
class _StubBlofinAccount:
    """Minimal account stub for startup scope checks."""

    name: str = "blofin-demo-account"
    kind: ProviderKind = ProviderKind.EXCHANGE
    _permissions: AccountPermissions | None = None

    def get_account_permissions(self) -> AccountPermissions:
        assert self._permissions is not None
        return self._permissions

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            kind=self.kind,
            health=ProviderHealth.HEALTHY,
        )


def test_blackbox_trade_live_blocked() -> None:
    with pytest.raises(ValidationError, match="trade_live is permanently disabled"):
        Settings(exchange_mode="trade_live")


def test_blackbox_production_blofin_host_blocked() -> None:
    with pytest.raises(ValidationError, match=r"production(/live)? host"):
        Settings(**{**_DEMO_OK, "blofin_demo_rest_base_url": "https://openapi.blofin.com"})


def test_blackbox_http_non_tls_blocked() -> None:
    with pytest.raises(ValidationError, match="allowlisted BloFin demo host"):
        Settings(**{**_DEMO_OK, "blofin_demo_rest_base_url": f"http://{_DEMO_HOST}"})


def test_blackbox_missing_credential_blocked() -> None:
    with pytest.raises(ValidationError, match="BloFin demo credentials"):
        Settings(**{**_DEMO_OK, "blofin_api_key": ""})


def test_blackbox_withdraw_scope_refused_at_resolve() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "query-apikey" in request.url.path:
            return httpx.Response(
                200,
                json={"code": "0", "data": [{"permissions": "read withdraw trade"}]},
            )
        return httpx.Response(200, json={"code": "0", "data": []})

    settings = Settings(**_DEMO_OK)
    with pytest.raises(ValueError, match="withdrawal/transfer scope"):
        resolve_exchange_provider(settings, transport=httpx.MockTransport(handler))


def test_blackbox_transfer_scope_refused_at_startup() -> None:
    settings = Settings(**_DEMO_OK)
    registry = ProviderRegistry()
    registry.register(
        _StubBlofinAccount(
            _permissions=AccountPermissions(
                can_read=True,
                can_trade=True,
                can_withdraw=False,
                can_transfer=True,
                raw_scopes=("read", "trade", "transfer"),
            )
        )
    )
    with pytest.raises(ValueError, match="withdrawal/transfer scope"):
        run_exchange_demo_startup_check(settings, registry)


def test_blackbox_read_only_key_refused_when_scope_probe_succeeds() -> None:
    settings = Settings(**_DEMO_OK)
    registry = ProviderRegistry()
    registry.register(
        _StubBlofinAccount(
            _permissions=AccountPermissions(
                can_read=True,
                can_trade=False,
                can_withdraw=False,
                can_transfer=False,
                raw_scopes=("read",),
            )
        )
    )
    with pytest.raises(ValueError, match="trade scope is required"):
        run_exchange_demo_startup_check(settings, registry)


def test_blackbox_real_read_trade_key_passes_startup() -> None:
    """Success criterion: a real BloFin read+trade key (readOnly=0) is accepted."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "query-apikey" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "msg": "success",
                    "data": {"apiKey": "k", "readOnly": 0, "type": 2},
                },
            )
        return httpx.Response(200, json={"code": "0", "data": []})

    settings = Settings(**_DEMO_OK)
    registry = ProviderRegistry()
    client = BloFinClient(
        base_url=_DEMO_REST,
        api_key="demo-key",
        api_secret="demo-secret",
        api_passphrase="demo-pass",
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
    )
    registry.register(BloFinAccountProvider(client))
    # Must not raise: read+trade is the supported demo posture.
    run_exchange_demo_startup_check(settings, registry)


def test_blackbox_provider_status_redacts_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "query-apikey" in request.url.path:
            return httpx.Response(
                200,
                json={"code": "0", "data": [{"permissions": "read trade"}]},
            )
        return httpx.Response(
            401,
            json={"code": "50113", "msg": "Invalid key demo-key secret demo-secret passphrase x"},
        )

    client = BloFinClient(
        base_url=_DEMO_REST,
        api_key="demo-key",
        api_secret="demo-secret",
        api_passphrase="demo-pass",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    provider = BloFinAccountProvider(client)
    with contextlib.suppress(Exception):
        provider.get_account_permissions()
    status = provider.status()
    combined = f"{status.detail or ''} {status.error_message or ''}".lower()
    assert "demo-key" not in combined
    assert "demo-secret" not in combined
    assert "demo-pass" not in combined


def test_blackbox_worker_scanner_does_not_place_orders() -> None:
    source = inspect.getsource(build_market_scan_scanner)
    assert "place_order" not in source
    assert "ExecutionService" not in source
    assert "ExchangeExecutionProvider" not in source


def test_blackbox_telegram_cannot_place_orders() -> None:
    source = inspect.getsource(TelegramAlertDeliveryProvider)
    assert "place_order" not in source
    assert "getUpdates" not in source
    assert "setWebhook" not in source
    app = create_app(settings=Settings())
    paths = {getattr(route, "path", "") for route in app.routes}
    telegram_paths = {path for path in paths if "telegram" in path.lower()}
    assert telegram_paths <= _ALLOWED_OUTBOUND_TELEGRAM_ROUTE_PATHS


def test_blackbox_ai_workspace_cannot_bypass_confirmation() -> None:
    tool = build_tool_registry().get("paper_execution")
    assert tool is not None
    assert tool.requires_approval is True
    assert Settings().real_trading_enabled is False


def test_blackbox_demo_host_is_official_allowlist() -> None:
    assert _DEMO_HOST == "demo-trading-openapi.blofin.com"
    assert is_allowlisted_demo_host(_DEMO_REST) is True
    assert is_allowlisted_demo_host(f"wss://{_DEMO_HOST}/ws/public") is True
