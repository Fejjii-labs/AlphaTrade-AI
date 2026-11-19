"""BloFin read-only safety cases using mocked transports."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from app.core.blofin_readonly_access import BloFinReadOnlyClient, get_readonly_account_provider
from app.core.config import Settings
from app.core.errors import ExchangeDemoInactiveError
from app.providers.exchange.errors import ExchangeError
from app.providers.exchange.factory import resolve_exchange_execution_provider
from tests.test_at037_tradingview_blofin import client as tv_client

__all__ = ["tv_client"]

SECRETS = ("opaque-sync-key-17", "opaque-sync-secret-19", "opaque-sync-pass-23")


def readonly_settings(**overrides: Any) -> Settings:
    values = {
        "_env_file": None,
        "environment": "local",
        "provider_mode": "mock",
        "rate_limit_use_redis": False,
        "market_data_cache_use_redis": False,
        "execution_mode": "paper",
        "enable_real_trading": False,
        "exchange_mode": "paper_internal",
        "blofin_demo_enabled": False,
        "blofin_readonly_sync_enabled": True,
        "blofin_readonly_api_key": SECRETS[0],
        "blofin_readonly_api_secret": SECRETS[1],
        "blofin_readonly_api_passphrase": SECRETS[2],
        "blofin_demo_rest_base_url": "https://demo-trading-openapi.blofin.com",
        "blofin_max_retries": 0,
    }
    values.update(overrides)
    return Settings(**values)


def account_transport(
    requests: list[httpx.Request], *, permissions: Any = None
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "demo-trading-openapi.blofin.com"
        data = {
            "/api/v1/user/query-apikey": {"readOnly": 1, "uid": "acceptance-native-uid"}
            if permissions is None
            else permissions,
            "/api/v1/account/balance": [
                {"currency": "USDT", "balance": "1000", "available": "900"}
            ],
            "/api/v1/account/positions": [
                {
                    "instId": "BTC-USDT",
                    "positions": "0.1",
                    "positionSide": "long",
                    "averagePrice": "65000",
                }
            ],
        }[request.url.path]
        return httpx.Response(200, json={"code": "0", "data": data})

    return httpx.MockTransport(handle)


def test_readonly_sync_in_paper_internal_keeps_execution_sealed(tv_client, monkeypatch) -> None:
    client, _factory, settings = tv_client
    configured = readonly_settings()
    for field in (
        "blofin_readonly_sync_enabled",
        "blofin_readonly_api_key",
        "blofin_readonly_api_secret",
        "blofin_readonly_api_passphrase",
        "blofin_demo_rest_base_url",
    ):
        setattr(settings, field, getattr(configured, field))
    seen: list[httpx.Request] = []
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_readonly_account_provider",
        lambda cfg: get_readonly_account_provider(cfg, transport=account_transport(seen)),
    )
    from tests.test_at037_tradingview_blofin import ORG_A as _PIN_ORG
    from tests.test_dashboard_demo_account import pin_activity

    pin_activity(monkeypatch, "acceptance-native-uid", _PIN_ORG)
    from tests.test_at037_tradingview_blofin import _login as tv_login

    client.headers.update(tv_login(client, "at037-a@test.example"))
    with capture_logs() as logs:
        response = client.post("/exchange/blofin/sync")
    assert response.status_code == 200
    report = response.json()
    assert report["snapshot"]["health_status"] == "ok"
    assert len(seen) == 3
    assert resolve_exchange_execution_provider(settings) is None
    assert client.get("/exchange/blofin/sync/latest").json()["exchange_mode"] == "paper_internal"
    serialized = json.dumps({"report": report, "logs": logs}, default=str) + repr(settings)
    assert all(value not in serialized for value in SECRETS)


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/api/v1/trade/order", {}),
        ("POST", "/api/v1/trade/cancel-order", {}),
        ("GET", "/api/v1/trade/order", None),
        ("GET", "/api/v1/asset/withdraw", None),
        ("GET", "/api/v1/asset/transfer", None),
        ("GET", "/api/v1/account/balance", {}),
        ("GET", "https://demo-trading-openapi.blofin.com/api/v1/account/balance", None),
    ],
)
def test_readonly_transport_refuses_all_other_capabilities(method, path, body) -> None:
    seen: list[httpx.Request] = []
    client = BloFinReadOnlyClient(
        base_url="https://demo-trading-openapi.blofin.com",
        api_key=SECRETS[0],
        api_secret=SECRETS[1],
        api_passphrase=SECRETS[2],
        transport=account_transport(seen),
    )
    with pytest.raises(ExchangeDemoInactiveError):
        client.request(method, path, body=body)
    assert not seen


@pytest.mark.parametrize(
    "overrides",
    [
        {"blofin_readonly_sync_enabled": False},
        {"blofin_readonly_api_key": ""},
        {"blofin_readonly_api_secret": ""},
        {"blofin_readonly_api_passphrase": ""},
    ],
)
def test_sync_does_not_use_stored_execution_credentials(overrides) -> None:
    settings = readonly_settings(
        blofin_api_key="execution-key",
        blofin_api_secret="execution-secret",
        blofin_api_passphrase="execution-pass",
        **overrides,
    )
    with pytest.raises(ExchangeDemoInactiveError):
        get_readonly_account_provider(settings, transport=account_transport([]))


def test_opaque_credentials_echoed_by_venue_are_redacted() -> None:
    def failed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "401", "msg": " ".join(SECRETS)})

    provider = get_readonly_account_provider(
        readonly_settings(), transport=httpx.MockTransport(failed)
    )
    with capture_logs() as logs, pytest.raises(ExchangeError) as caught:
        provider.get_account_permissions()
    diagnostic = json.dumps(logs, default=str) + str(caught.value) + repr(caught.value.details)
    assert all(secret not in diagnostic for secret in SECRETS)


@pytest.mark.parametrize(
    "permission",
    [{"readOnly": 0}, {"permissions": "read,withdraw"}, {"permissions": "read,transfer"}],
)
def test_readonly_sync_refuses_unsafe_permission_before_account_fetch(
    tv_client, monkeypatch, permission
) -> None:
    client, _factory, settings = tv_client
    configured = readonly_settings()
    for field in (
        "blofin_readonly_sync_enabled",
        "blofin_readonly_api_key",
        "blofin_readonly_api_secret",
        "blofin_readonly_api_passphrase",
        "blofin_demo_rest_base_url",
    ):
        setattr(settings, field, getattr(configured, field))
    seen = []
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_readonly_account_provider",
        lambda cfg: get_readonly_account_provider(
            cfg, transport=account_transport(seen, permissions=[permission])
        ),
    )
    from tests.test_at037_tradingview_blofin import ORG_A as _PIN_ORG
    from tests.test_dashboard_demo_account import pin_activity

    pin_activity(monkeypatch, "acceptance-native-uid", _PIN_ORG)
    from tests.test_at037_tradingview_blofin import _login as tv_login

    response = client.post(
        "/exchange/blofin/sync", headers=tv_login(client, "at037-a@test.example")
    )
    assert response.status_code == 200
    assert response.json()["snapshot"]["health_status"] == "unavailable"
    assert [r.url.path for r in seen] == ["/api/v1/user/query-apikey"]
