"""Tests for the BloFin demo exchange provider (Slice 57, read-only).

All network access is mocked via ``httpx.MockTransport`` so no real venue is
contacted. Covers signing headers, retry/rate-limit/error handling, secret
redaction, market-data mapping with fallback, and the withdrawal-scope refusal.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from app.core.config import Settings
from app.providers.exchange.blofin_account import (
    BloFinAccountProvider,
    parse_account_permissions,
)
from app.providers.exchange.blofin_client import BloFinClient, _signed_request_path
from app.providers.exchange.blofin_market_data import BloFinMarketDataProvider
from app.providers.exchange.errors import (
    ExchangeAuthError,
    ExchangeRateLimitError,
    ExchangeRequestError,
    ExchangeUnavailableError,
)
from app.providers.exchange.factory import resolve_exchange_provider
from app.providers.exchange.mapping import (
    from_blofin_inst_id,
    timeframe_to_bar,
    to_blofin_inst_id,
)
from app.schemas.common import Timeframe

_DEMO_URL = "https://demo-trading-openapi.blofin.com"

_DEMO_SETTINGS = {
    "exchange_mode": "paper_exchange_demo",
    "blofin_demo_enabled": True,
    "blofin_api_key": "demo-key",
    "blofin_api_secret": "demo-secret",
    "blofin_api_passphrase": "demo-pass",
    "blofin_demo_rest_base_url": _DEMO_URL,
}


def _client(handler, **kwargs) -> BloFinClient:
    return BloFinClient(
        base_url=_DEMO_URL,
        api_key="demo-key",
        api_secret="demo-secret",
        api_passphrase="demo-pass",
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,  # no real sleeping in tests
        max_retries=2,
        **kwargs,
    )


# --- mapping ---------------------------------------------------------------


def test_symbol_mapping_round_trip() -> None:
    assert to_blofin_inst_id("BTCUSDT") == "BTC-USDT"
    assert to_blofin_inst_id("ETH/USDT") == "ETH-USDT"
    assert to_blofin_inst_id("SOL-USDC") == "SOL-USDC"
    assert from_blofin_inst_id("BTC-USDT") == "BTCUSDT"


def test_timeframe_to_bar() -> None:
    assert timeframe_to_bar(Timeframe.M1) == "1m"
    assert timeframe_to_bar(Timeframe.H1) == "1H"
    assert timeframe_to_bar(Timeframe.D1) == "1D"


# --- client safety ---------------------------------------------------------


def test_client_refuses_non_demo_host() -> None:
    with pytest.raises(ValueError, match="production host"):
        BloFinClient(
            base_url="https://openapi.blofin.com",
            api_key="k",
            api_secret="s",
            api_passphrase="p",
        )


def test_signed_request_sends_auth_headers() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={"code": "0", "data": {"ok": True}})

    data = _client(handler).request("GET", "/api/v1/account/balance", signed=True)
    assert data == {"ok": True}
    assert captured["access-key"] == "demo-key"
    assert captured["access-sign"]
    assert captured["access-passphrase"] == "demo-pass"
    assert "access-timestamp" in captured
    assert "access-nonce" in captured


def test_signature_is_deterministic_for_fixed_inputs() -> None:
    client = _client(lambda r: httpx.Response(200, json={"code": "0", "data": None}))
    sig_a = client._sign(method="GET", path="/x", timestamp="1", nonce="n", body="")
    sig_b = client._sign(method="GET", path="/x", timestamp="1", nonce="n", body="")
    assert sig_a == sig_b
    assert "demo-secret" not in sig_a


def test_signed_request_path_is_stable_for_param_order() -> None:
    path_a = _signed_request_path(
        "/api/v1/account/leverage-info",
        {"instId": "BTC-USDT", "marginMode": "cross"},
    )
    path_b = _signed_request_path(
        "/api/v1/account/leverage-info",
        {"marginMode": "cross", "instId": "BTC-USDT"},
    )
    assert path_a == path_b == "/api/v1/account/leverage-info?instId=BTC-USDT&marginMode=cross"


def test_signed_request_path_unchanged_without_params() -> None:
    path = "/api/v1/account/position-mode"
    assert _signed_request_path(path, None) == path
    assert _signed_request_path(path, {}) == path


def test_signed_get_uses_query_path_in_signature() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["sign"] = request.headers["access-sign"]
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": {"instId": "BTC-USDT", "marginMode": "cross", "leverage": "20"},
            },
        )

    client = BloFinClient(
        base_url=_DEMO_URL,
        api_key="demo-key",
        api_secret="demo-secret",
        api_passphrase="demo-pass",
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
        max_retries=0,
        clock=lambda: "1000",
        nonce_factory=lambda: "fixednonce",
    )
    client.request(
        "GET",
        "/api/v1/account/leverage-info",
        params={"instId": "BTC-USDT", "marginMode": "cross"},
        signed=True,
    )
    expected = client._sign(
        method="GET",
        path="/api/v1/account/leverage-info?instId=BTC-USDT&marginMode=cross",
        timestamp="1000",
        nonce="fixednonce",
        body="",
    )
    assert captured["sign"] == expected
    assert captured["url"].endswith("instId=BTC-USDT&marginMode=cross")


def test_signed_get_without_params_unchanged() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["sign"] = request.headers["access-sign"]
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"code": "0", "data": {"positionMode": "net_mode"}})

    client = BloFinClient(
        base_url=_DEMO_URL,
        api_key="demo-key",
        api_secret="demo-secret",
        api_passphrase="demo-pass",
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
        max_retries=0,
        clock=lambda: "1000",
        nonce_factory=lambda: "fixednonce",
    )
    client.request("GET", "/api/v1/account/position-mode", signed=True)
    expected = client._sign(
        method="GET",
        path="/api/v1/account/position-mode",
        timestamp="1000",
        nonce="fixednonce",
        body="",
    )
    assert captured["sign"] == expected
    assert captured["url"].endswith("/api/v1/account/position-mode")


def test_signed_get_failure_does_not_log_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": "152409", "msg": "api_key=supersecret", "data": None},
        )

    client = BloFinClient(
        base_url=_DEMO_URL,
        api_key="demo-key",
        api_secret="demo-secret",
        api_passphrase="demo-pass",
        transport=httpx.MockTransport(handler),
        sleeper=lambda _seconds: None,
        max_retries=0,
    )
    with pytest.raises(ExchangeRequestError):
        client.request(
            "GET",
            "/api/v1/account/leverage-info",
            params={"instId": "BTC-USDT", "marginMode": "cross"},
            signed=True,
        )
    assert "supersecret" not in (client.last_error or "")
    assert "demo-secret" not in (client.last_error or "")


def test_unsigned_request_omits_auth_headers() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.headers)
        return httpx.Response(200, json={"code": "0", "data": []})

    _client(handler).request("GET", "/api/v1/market/tickers", params={"instId": "BTC-USDT"})
    assert "access-key" not in captured


# --- client error handling -------------------------------------------------


def test_auth_error_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"code": "401", "msg": "bad key"})

    with pytest.raises(ExchangeAuthError):
        _client(handler).request("GET", "/api/v1/account/balance", signed=True)
    assert calls["n"] == 1


def test_rate_limit_is_retried_then_raised() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, text="too many")

    with pytest.raises(ExchangeRateLimitError):
        _client(handler).request("GET", "/api/v1/market/tickers")
    assert calls["n"] == 3  # initial + 2 retries


def test_server_error_is_retried_then_raised() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, text="unavailable")

    with pytest.raises(ExchangeUnavailableError):
        _client(handler).request("GET", "/api/v1/market/tickers")
    assert calls["n"] == 3


def test_venue_error_code_raises_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "1001", "msg": "bad param", "data": None})

    with pytest.raises(ExchangeRequestError, match="1001"):
        _client(handler).request("GET", "/api/v1/market/tickers")


def test_last_error_is_redacted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "1", "msg": "api_key=leakedsecret", "data": None})

    client = _client(handler)
    with pytest.raises(ExchangeRequestError):
        client.request("GET", "/api/v1/market/tickers")
    assert "leakedsecret" not in (client.last_error or "")
    assert "***REDACTED***" in (client.last_error or "")


def test_nested_order_error_prefers_data_reason_over_envelope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": "1",
                "msg": "All operations failed",
                "data": [{"code": "51020", "msg": "Position side mismatch"}],
            },
        )

    with pytest.raises(ExchangeRequestError) as exc_info:
        _client(handler).request("POST", "/api/v1/trade/order", body={}, signed=True)
    details = exc_info.value.details
    assert details is not None
    assert details.venue_error_code == "51020"
    assert details.venue_error_message == "Position side mismatch"
    assert details.http_status == 200


def test_nested_order_error_redacts_data_msg() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": "1",
                "msg": "All operations failed",
                "data": [{"code": "51000", "msg": "api_key=leakedsecret"}],
            },
        )

    with pytest.raises(ExchangeRequestError) as exc_info:
        _client(handler).request("POST", "/api/v1/trade/order", body={}, signed=True)
    details_msg = exc_info.value.details.venue_error_message if exc_info.value.details else ""
    assert "leakedsecret" not in (details_msg or "")


def test_position_mode_parses_net_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": "0", "data": {"positionMode": "net_mode"}},
        )

    mode = BloFinAccountProvider(_client(handler)).get_position_mode()
    assert mode.position_mode == "net_mode"


def test_position_mode_parses_long_short_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": "0", "data": {"positionMode": "long_short_mode"}},
        )

    mode = BloFinAccountProvider(_client(handler)).get_position_mode()
    assert mode.position_mode == "long_short_mode"


def test_leverage_info_parses_cross_margin() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/account/batch-leverage-info"
        assert request.url.params["instId"] == "BTC-USDT"
        assert request.url.params["marginMode"] == "cross"
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "marginMode": "cross",
                        "leverage": "50",
                        "positionSide": "net",
                    }
                ],
            },
        )

    info = BloFinAccountProvider(_client(handler)).get_leverage_info(
        inst_id="BTC-USDT",
        margin_mode="cross",
    )
    assert info.inst_id == "BTC-USDT"
    assert info.margin_mode == "cross"
    assert info.leverage == Decimal("50")
    assert info.position_side == "net"


# --- market data -----------------------------------------------------------


def test_market_data_parses_candles_oldest_first() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # BloFin returns newest-first rows: [ts, o, h, l, c, vol, ...]
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    ["1700000060000", "11", "12", "10", "11.5", "100"],
                    ["1700000000000", "10", "11", "9", "10.5", "120"],
                ],
            },
        )

    provider = BloFinMarketDataProvider(_client(handler))
    result = provider.get_ohlcv("BTCUSDT", Timeframe.M1, limit=2)
    assert [b.close for b in result.bars] == [Decimal("10.5"), Decimal("11.5")]
    assert result.envelope.source == "blofin-demo"


def test_market_data_falls_back_to_mock_on_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    provider = BloFinMarketDataProvider(_client(handler))
    result = provider.get_ticker("BTCUSDT")
    assert result.envelope.source == "mock"
    assert provider.status().using_fallback is True


# --- account & permissions -------------------------------------------------


def test_permissions_without_withdraw_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "data": {"permissions": "read,trade"}})

    perms = BloFinAccountProvider(_client(handler)).get_account_permissions()
    assert perms.can_trade is True
    assert perms.can_withdraw is False
    assert perms.can_transfer is False


def test_permissions_detects_withdraw_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": "0", "data": {"permissions": "read,trade,withdraw"}}
        )

    perms = BloFinAccountProvider(_client(handler)).get_account_permissions()
    assert perms.can_withdraw is True


def test_permissions_accepts_real_blofin_read_only_zero() -> None:
    """Canonical BloFin shape: readOnly=0 means read+trade (no permissions string)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": "0",
                "msg": "success",
                "data": {
                    "uid": "uid",
                    "apiName": "demo",
                    "apiKey": "k",
                    "readOnly": 0,
                    "ips": [],
                    "type": 2,
                },
            },
        )

    perms = BloFinAccountProvider(_client(handler)).get_account_permissions()
    assert perms.can_trade is True
    assert perms.can_read is True
    assert perms.can_withdraw is False
    assert perms.can_transfer is False
    # Diagnostics expose field names only, never values.
    assert "readOnly" in perms.response_keys
    assert "apiKey" in perms.response_keys


def test_permissions_read_only_one_is_read_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "data": {"readOnly": 1}})

    perms = BloFinAccountProvider(_client(handler)).get_account_permissions()
    assert perms.can_read is True
    assert perms.can_trade is False


# --- permission parser shapes (pure, no network) ---------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"permissions": ["READ", "TRADE"]},
        {"permissions": ["read", "trade"]},
        {"permissions": "read,trade"},
        {"permission": "read,trade"},
        {"read": True, "trade": True},
        {"scopes": ["Read", "Trade"]},
        {"authorities": "READ TRADE"},
        {"readOnly": 0},
        {"readOnly": "0"},
        # A list-wrapped data payload, as some BloFin responses return.
        [{"permissions": "READ,TRADE"}],
    ],
)
def test_parser_accepts_read_and_trade_shapes(payload: object) -> None:
    perms = parse_account_permissions(payload)
    assert perms.can_trade is True
    assert perms.can_read is True
    assert perms.can_withdraw is False
    assert perms.can_transfer is False


def test_parser_trade_only_implies_read() -> None:
    """BloFin docs: TRADE can also request/view account info, so it implies read."""
    perms = parse_account_permissions({"permissions": ["TRADE"]})
    assert perms.can_trade is True
    assert perms.can_read is True


def test_parser_transfer_scope_is_flagged() -> None:
    perms = parse_account_permissions({"permissions": "read,trade,transfer"})
    assert perms.can_transfer is True
    assert perms.can_withdraw is False


def test_parser_withdraw_scope_is_flagged() -> None:
    perms = parse_account_permissions({"permissions": ["READ", "TRADE", "WITHDRAW"]})
    assert perms.can_withdraw is True


def test_parser_boolean_transfer_flag_is_flagged() -> None:
    perms = parse_account_permissions({"read": True, "trade": True, "transfer": True})
    assert perms.can_transfer is True


def test_parser_missing_trade_fails() -> None:
    perms = parse_account_permissions({"permissions": "read"})
    assert perms.can_trade is False
    assert perms.can_read is True


@pytest.mark.parametrize("payload", [{}, [], None, "", {"unexpected": "shape"}, {"readOnly": "?"}])
def test_parser_unexpected_payload_fails_safe(payload: object) -> None:
    perms = parse_account_permissions(payload)
    assert perms.can_trade is False
    assert perms.can_withdraw is False
    assert perms.can_transfer is False


# --- factory ---------------------------------------------------------------


def test_factory_returns_mock_by_default() -> None:
    resolved = resolve_exchange_provider(Settings())
    assert resolved.is_demo is False
    assert resolved.account is None
    assert resolved.status_provider.name == "mock-exchange"


def test_factory_builds_demo_providers_for_safe_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "data": {"permissions": "read,trade"}})

    resolved = resolve_exchange_provider(
        Settings(**_DEMO_SETTINGS), transport=httpx.MockTransport(handler)
    )
    assert resolved.is_demo is True
    assert resolved.account is not None
    assert resolved.market_data is not None
    assert resolved.status_provider.name == "blofin-demo-account"


def test_factory_refuses_key_with_withdrawal_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": "0", "data": {"permissions": "read,trade,withdraw"}}
        )

    with pytest.raises(ValueError, match="withdrawal/transfer scope"):
        resolve_exchange_provider(
            Settings(**_DEMO_SETTINGS), transport=httpx.MockTransport(handler)
        )


def test_factory_tolerates_permission_probe_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    resolved = resolve_exchange_provider(
        Settings(**_DEMO_SETTINGS), transport=httpx.MockTransport(handler)
    )
    assert resolved.is_demo is True  # read-only continues; execution re-verifies later
