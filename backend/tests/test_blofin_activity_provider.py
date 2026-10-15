"""Official-shape deterministic fixtures and the restricted transport boundary."""

from datetime import UTC, datetime

import httpx
import pytest

from app.core.blofin_readonly_access import BloFinReadOnlyClient, get_readonly_client
from app.core.config import Settings
from app.core.errors import ExchangeDemoInactiveError
from app.providers.exchange.blofin_activity import (
    ActivityIdentityError,
    BloFinActivityProvider,
    parse_fact,
)
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.errors import ExchangeRequestError
from app.services.blofin_activity_config import BloFinActivitySettings
from app.services.blofin_activity_service import activity_provider

NOW = datetime(2026, 10, 10, 10, tzinfo=UTC)
MS = int(NOW.timestamp() * 1000)


def order(order_id="o1", **updates):
    return {
        "orderId": order_id,
        "clientOrderId": "",
        "instId": "BTC-USDT",
        "side": "buy",
        "positionSide": "net",
        "size": "0.100000000000000000",
        "filledSize": "0.05",
        "orderType": "market",
        "state": "partially_canceled",
        "price": "",
        "averagePrice": "82234.400000000000000000",
        "reduceOnly": "false",
        "fee": "-0.00000123456789123456789",
        "pnl": "",
        "createTime": str(MS - 10000),
        "updateTime": str(MS - 1000),
        **updates,
    }


def fill(trade_id="f1", order_id="o1", **updates):
    return {
        "tradeId": trade_id,
        "orderId": order_id,
        "instId": "BTC-USDT",
        "side": "buy",
        "positionSide": "net",
        "fillSize": "0.025000000000000000",
        "fillPrice": "82234.40",
        "fee": "0.00123456789123456789",
        "fillPnl": "",
        "ts": str(MS - 1000),
        **updates,
    }


class HistoryVenue:
    def __init__(self):
        self.uid = "demo-user-1"
        self.identity = {"uid": self.uid, "readOnly": 1, "parentUid": "main-user"}
        self.pages = {
            "order": {None: [order()], "o1": []},
            "fill": {None: [fill()], "f1": []},
        }
        self.requests = []
        self.fail_kind = None
        self.on_page = lambda: None

    def handle(self, request):
        self.requests.append(request)
        assert request.method == "GET"
        path = request.url.path
        if path.endswith("query-apikey"):
            data = self.identity
        elif path.endswith("instruments"):
            data = [
                {
                    "instId": "BTC-USDT",
                    "contractValue": "0.001000000000000000",
                    "contractType": "linear",
                    "baseCurrency": "BTC",
                    "settleCurrency": "USDT",
                }
            ]
        else:
            kind = "order" if path.endswith("orders-history") else "fill"
            if self.fail_kind == kind:
                return httpx.Response(429, json={"code": "429"})
            data = self.pages[kind].get(request.url.params.get("after"), [])
            self.on_page()
        return httpx.Response(200, json={"code": "0", "data": data})

    def provider(self):
        return BloFinActivityProvider(
            BloFinReadOnlyClient(
                base_url="https://demo-trading-openapi.blofin.com",
                api_key="fixture-key",
                api_secret="fixture-secret",
                api_passphrase="fixture-passphrase",
                transport=httpx.MockTransport(self.handle),
                sleeper=lambda _: None,
                max_retries=0,
            )
        )


def readonly_settings(**updates):
    return Settings(
        _env_file=None,
        environment="local",
        execution_mode="paper",
        enable_real_trading=False,
        exchange_mode="paper_internal",
        provider_mode="mock",
        blofin_readonly_sync_enabled=True,
        blofin_readonly_api_key="fixture-key",
        blofin_readonly_api_secret="fixture-secret",
        blofin_readonly_api_passphrase="fixture-passphrase",
        blofin_demo_rest_base_url="https://demo-trading-openapi.blofin.com",
        **updates,
    )


def test_original_decimals_partial_quantities_unknown_currency_and_funding():
    fact = parse_fact("fill", fill(fillPnl="-1.234567891234567891", feeCurrency="BTC"))
    assert fact.quantity == "0.025000000000000000"
    assert fact.realized_pnl == "-1.234567891234567891"
    assert fact.fee == "0.00123456789123456789" and fact.fee_currency == "BTC"
    assert fact.funding is None
    native_order = parse_fact("order", order())
    assert native_order.filled_quantity == "0.05"
    assert native_order.quantity == "0.100000000000000000"
    assert native_order.fee_currency is None and native_order.realized_pnl is None
    assert native_order.fee.startswith("-")


@pytest.mark.parametrize(
    "field,value",
    [
        ("fillSize", 0.1),
        ("fillSize", "NaN"),
        ("fillSize", "-1"),
        ("fillSize", "0"),
        ("fillPrice", "Infinity"),
        ("fillPrice", "0"),
        ("fee", "bogus"),
        ("ts", "not-time"),
        ("ts", "99999999999999999999999999"),
        ("tradeId", ""),
        ("orderId", None),
        ("positionSide", "unknown"),
        ("side", "BUY"),
        ("side", ["buy"]),
        ("positionSide", {"net": True}),
    ],
)
def test_malformed_native_facts_refuse(field, value):
    with pytest.raises(ExchangeRequestError):
        parse_fact("fill", fill(**{field: value}))


@pytest.mark.parametrize(
    "identity",
    [
        {},
        {"uid": "different", "readOnly": 1},
        {"parentUid": "demo-user-1", "readOnly": 1},
        {"uid": "demo-user-1"},
        {"uid": "demo-user-1", "readOnly": 0},
        {"uid": "demo-user-1", "readOnly": 1, "transfer": True},
    ],
)
def test_uid_must_be_the_authenticated_account_not_parent(identity):
    venue = HistoryVenue()
    venue.identity = identity
    with pytest.raises(ActivityIdentityError):
        venue.provider().verify_identity(venue.uid, lambda: None)


def test_signed_history_uses_correct_native_cursor_and_bounded_window():
    venue = HistoryVenue()
    facts = venue.provider().page(
        "fill",
        begin_ms=MS - 100000,
        end_ms=MS,
        after="f1",
        limit=100,
        expected_uid=venue.uid,
        before_send=lambda: None,
    )
    assert facts == ()
    req = venue.requests[-1]
    assert req.url.path == "/api/v1/trade/fills-history"
    assert dict(req.url.params) == {
        "after": "f1",
        "begin": str(MS - 100000),
        "end": str(MS),
        "limit": "100",
    }
    assert req.headers.get("ACCESS-SIGN")


@pytest.mark.parametrize(
    "method,path,params,body,signed",
    [
        ("POST", "/api/v1/trade/order", None, {}, True),
        ("GET", "/api/v1/trade/cancel-order", None, None, True),
        ("POST", "/api/v1/asset/transfer", None, {}, True),
        ("GET", "/api/v1/account/set-leverage", None, None, True),
        ("DELETE", "/api/v1/trade/orders-history", None, None, True),
        ("GET", "/api/v1/trade/orders-history", None, None, True),
        (
            "GET",
            "/api/v1/trade/fills-history",
            {"begin": "0", "end": "100", "limit": "101"},
            None,
            True,
        ),
        (
            "GET",
            "/api/v1/trade/fills-history",
            {"begin": "0", "end": "100", "limit": "10"},
            None,
            False,
        ),
        (
            "GET",
            "/api/v1/trade/fills-history",
            {"begin": "0", "end": "100", "limit": "10", "before": "1"},
            None,
            True,
        ),
        (
            "GET",
            "/api/v1/trade/fills-history",
            {"begin": "0", "end": "100", "limit": "10", "after": "../order"},
            None,
            True,
        ),
        (
            "GET",
            "/api/v1/trade/fills-history",
            {"begin": "100", "end": "0", "limit": "10"},
            None,
            True,
        ),
        (
            "GET",
            "/api/v1/trade/fills-history",
            {"begin": "0", "end": "100", "limit": "10"},
            {},
            True,
        ),
    ],
)
def test_transport_rejects_unsupported_operations_before_network(
    method, path, params, body, signed
):
    venue = HistoryVenue()
    with pytest.raises(ExchangeDemoInactiveError):
        venue.provider().client.request(method, path, params=params, body=body, signed=signed)
    assert venue.requests == []


def test_default_disarmed_factory_and_sealed_execution_credentials():
    assert not BloFinActivitySettings(_env_file=None).enabled
    safe = readonly_settings()
    provider = activity_provider(safe)
    assert isinstance(provider.client, BloFinReadOnlyClient)
    assert provider.client._min_interval == 1
    with pytest.raises(ExchangeDemoInactiveError):
        get_readonly_client(safe.model_copy(update={"blofin_readonly_sync_enabled": False}))
    with pytest.raises(TypeError):
        BloFinActivityProvider(
            BloFinClient(
                base_url="https://demo-trading-openapi.blofin.com",
                api_key="x",
                api_secret="x",
                api_passphrase="x",
            )
        )


def test_history_uses_dedicated_read_key_in_active_demo_without_changing_existing_sync_gate():
    from app.core.config import ExchangeMode

    settings = readonly_settings().model_copy(
        update={
            "exchange_mode": ExchangeMode.PAPER_EXCHANGE_DEMO,
            "blofin_readonly_sync_enabled": False,
            "blofin_api_key": "sealed-execution-key",
        }
    )
    provider = activity_provider(settings)
    assert provider.client._api_key == "fixture-key"
    with pytest.raises(ExchangeDemoInactiveError):
        get_readonly_client(settings)
