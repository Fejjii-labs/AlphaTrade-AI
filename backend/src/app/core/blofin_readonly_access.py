"""Explicit demo account sync capability, independent of execution credentials."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.core.config import ExchangeMode, ExecutionMode, Settings
from app.core.errors import ExchangeDemoInactiveError
from app.core.exchange_safety import assert_demo_host
from app.providers.exchange.blofin_account import BloFinAccountProvider
from app.providers.exchange.blofin_client import BloFinClient

_ACCOUNT_PATHS = frozenset(
    {
        "/api/v1/account/balance",
        "/api/v1/account/positions",
        "/api/v1/user/query-apikey",
        "/api/v1/market/instruments",
        "/api/v1/trade/orders-history",
        "/api/v1/trade/fills-history",
    }
)


class BloFinReadOnlyClient(BloFinClient):
    """Transport with no order, withdrawal or transfer capability."""

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        signed: bool = False,
        before_send: Callable[[], None] | None = None,
    ) -> Any:
        if method.upper() != "GET" or path not in _ACCOUNT_PATHS or body is not None:
            raise ExchangeDemoInactiveError("BloFin sync permits account GET requests only.")
        if path == "/api/v1/market/instruments" and (signed or params):
            raise ExchangeDemoInactiveError("BloFin sync permits only public bulk metadata reads.")
        if path in {"/api/v1/trade/orders-history", "/api/v1/trade/fills-history"}:
            allowed = {"begin", "end", "after", "limit"}
            if not signed or not params or set(params) - allowed:
                raise ExchangeDemoInactiveError("BloFin history permits bounded signed GET only.")
            required = {"begin", "end", "limit"} if path.endswith("fills-history") else {"limit"}
            if not required <= set(params) or (("begin" in params) != ("end" in params)):
                raise ExchangeDemoInactiveError("BloFin history requires bounded pagination.")
            if any(
                not isinstance(params[k], str)
                or not params[k].isascii()
                or not params[k].isdecimal()
                or len(params[k]) > 15
                for k in params
                if k != "after"
            ) or not (
                1 <= int(params["limit"]) <= 100
                and (
                    "begin" not in params
                    or 0 <= int(params["begin"]) <= int(params["end"]) <= 253402300799999
                )
            ):
                raise ExchangeDemoInactiveError("BloFin history parameters are invalid.")
            if "after" in params and (
                not isinstance(params["after"], str)
                or not params["after"]
                or len(params["after"]) > 128
                or not params["after"].isascii()
                or any(not (c.isalnum() or c in "-_.") for c in params["after"])
            ):
                raise ExchangeDemoInactiveError("BloFin history cursor is invalid.")
        data = super().request(method, path, params=params, signed=signed, before_send=before_send)
        if not isinstance(data, list | dict) or (
            path
            in {
                "/api/v1/account/positions",
                "/api/v1/trade/orders-history",
                "/api/v1/trade/fills-history",
            }
            and not isinstance(data, list)
        ):
            raise ExchangeDemoInactiveError("BloFin account response has an invalid shape.")
        return data


def get_readonly_account_provider(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> BloFinAccountProvider:
    return BloFinAccountProvider(get_readonly_client(settings, transport=transport))


def get_readonly_client(
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
    activity_history: bool = False,
) -> BloFinReadOnlyClient:
    """Release only the separate sync secrets after an explicit safe opt-in."""
    allowed_modes = {ExchangeMode.PAPER_INTERNAL}
    if activity_history:
        allowed_modes.add(ExchangeMode.PAPER_EXCHANGE_DEMO)
    if (
        (not activity_history and not settings.blofin_readonly_sync_enabled)
        or settings.execution_mode is not ExecutionMode.PAPER
        or settings.enable_real_trading
        or settings.exchange_mode not in allowed_modes
    ):
        raise ExchangeDemoInactiveError("BloFin read-only access is not active in this paper mode.")
    credentials = (
        settings.blofin_readonly_api_key.strip(),
        settings.blofin_readonly_api_secret.strip(),
        settings.blofin_readonly_api_passphrase.strip(),
    )
    if not all(credentials):
        raise ExchangeDemoInactiveError("Dedicated BloFin read-only sync credentials are missing.")
    assert_demo_host(settings.blofin_demo_rest_base_url)
    url = httpx.URL(settings.blofin_demo_rest_base_url)
    if (
        url.scheme != "https"
        or url.userinfo
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ExchangeDemoInactiveError("BloFin sync requires a plain HTTPS demo origin.")
    return BloFinReadOnlyClient(
        base_url=settings.blofin_demo_rest_base_url,
        api_key=credentials[0],
        api_secret=credentials[1],
        api_passphrase=credentials[2],
        timeout_seconds=settings.blofin_request_timeout_seconds,
        max_retries=settings.blofin_max_retries,
        rate_limit_requests_per_second=settings.blofin_rate_limit_requests_per_second,
        transport=transport,
    )
