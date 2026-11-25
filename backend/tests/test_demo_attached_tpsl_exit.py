"""A BloFin attached TP/SL close is proven through the closing order's algoId.

Staging returned this shape for the first governed demo take-profit: the closing
order has a blank clientOrderId, null trigger prices and ``algoId`` naming the
TPSL; the TPSL history row has a blank clientOrderId, so a lookup by the entry's
client id returns nothing. The exit must verify, and every row that does not
match the entry's attested terms must keep the exit held.
"""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from app.providers.exchange.governed_blofin import DemoFill, DemoOrderEvidence
from tests.support.phase5_market import EVALUATED_AT
from tests.test_governed_blofin_quote_freshness import _plan
from tests.test_governed_demo_readiness import provider_for


def _ms(delta: timedelta = timedelta(0)) -> str:
    return str(int((EVALUATED_AT + delta).timestamp() * 1000))


def attached_world(category: str = "tp"):
    plan = _plan(valid_for_ms=6000)
    quantity = plan.quantity.value
    stop = str(plan.risk_and_exits.stop.value)
    target = str(plan.risk_and_exits.targets[0].price.value)
    entry = DemoOrderEvidence(
        "entry-1",
        "ATentry",
        "filled",
        (
            DemoFill(
                "entry-1:entry-fill-1", quantity, Decimal("100000"), EVALUATED_AT, Decimal("0")
            ),
        ),
        False,
        "missing",
    )
    parent = {
        "orderId": "entry-1",
        "clientOrderId": "ATentry",
        "instId": plan.execution_instrument,
        "size": str(quantity),
        "filledSize": str(quantity),
        "side": "sell",
        "positionSide": "net",
        "marginMode": "cross",
        "state": "filled",
        "reduceOnly": "false",
        "orderCategory": "normal",
        "createTime": _ms(),
        "tpTriggerPrice": target,
        "slTriggerPrice": stop,
        "tpOrderPrice": "-1",
        "slOrderPrice": "-1",
        "algoId": "",
    }
    close = {
        "orderId": "close-1",
        "clientOrderId": "",
        "instId": plan.execution_instrument,
        "size": str(quantity),
        "filledSize": str(quantity),
        "side": "buy",
        "positionSide": "net",
        "marginMode": "cross",
        "state": "filled",
        "reduceOnly": "true",
        "orderCategory": category,
        "createTime": _ms(timedelta(seconds=1)),
        "tpTriggerPrice": None,
        "slTriggerPrice": None,
        "tpOrderPrice": None,
        "slOrderPrice": None,
        "algoClientOrderId": "",
        "algoId": "10004687495",
    }
    fill = {
        "orderId": "close-1",
        "tradeId": "close-fill-1",
        "instId": plan.execution_instrument,
        "side": "buy",
        "positionSide": "net",
        "fillSize": str(quantity),
        "fillPrice": "99900",
        "fillPnl": "777.6",
        "fee": "18.8352",
        "ts": _ms(timedelta(seconds=1)),
    }
    protection = {
        "tpslId": "10004687495",
        "clientOrderId": "",
        "instId": plan.execution_instrument,
        "marginMode": "cross",
        "positionSide": "net",
        "positionId": "",
        "side": "buy",
        "size": str(quantity),
        "reduceOnly": "true",
        "state": "effective",
        "actualSize": str(quantity),
        "triggerType": category,
        "orderCategory": "normal",
        "tpTriggerPrice": target,
        "tpOrderPrice": "-1",
        "slTriggerPrice": stop,
        "slOrderPrice": "-1",
        "createTime": _ms(timedelta(milliseconds=102)),
    }
    responses = {
        "query-apikey": {"readOnly": 0},
        "positions": [],
        "orders-pending": [],
        "orders-tpsl-pending": [],
        "orders-history": [close, parent],
        "fills-history": [fill],
        "orders-tpsl-history": [protection],
    }
    tpsl_params: list[dict[str, str]] = []

    def handler(request):
        assert request.method == "GET", "Exit reconciliation cannot place or cancel orders"
        endpoint = request.url.path.rsplit("/", 1)[1]
        if endpoint == "orders-tpsl-history":
            tpsl_params.append(dict(request.url.params))
        return httpx.Response(200, json={"code": "0", "data": deepcopy(responses[endpoint])})

    provider = provider_for(handler)
    provider._clock = lambda: EVALUATED_AT + timedelta(seconds=2)
    return plan, entry, provider, responses, tpsl_params


@pytest.mark.parametrize("category", ["tp", "sl"])
def test_blofin_attached_tpsl_close_is_verified_via_algo_id(category):
    plan, entry, provider, _, tpsl_params = attached_world(category)
    closed = provider.reconcile_exit(plan=plan, entry=entry)
    assert closed is not None
    assert closed.protection_ids == ("10004687495",)
    assert closed.exit_order_protection_ids == (("close-1", "10004687495"),)
    assert closed.fills[0].category == category
    assert closed.fills[0].reported_pnl == Decimal("777.6")
    assert tpsl_params == [{"tpslId": "10004687495", "limit": "100"}]


def _mutate(responses, reason):
    close, parent = responses["orders-history"]
    protection = responses["orders-tpsl-history"][0]
    if reason == "no_algo_id":
        close["algoId"] = ""
    elif reason == "parent_not_attested":
        parent["slTriggerPrice"] = None
    elif reason == "foreign_protection_client":
        protection["clientOrderId"] = "ATother"
    elif reason == "protection_not_reduce_only":
        protection["reduceOnly"] = "false"
    elif reason == "protection_too_late":
        protection["createTime"] = _ms(timedelta(seconds=10, milliseconds=1))
    elif reason == "protection_before_entry":
        protection["createTime"] = _ms(timedelta(milliseconds=-1))
    elif reason == "protection_wrong_target":
        protection["tpTriggerPrice"] = "1"
    elif reason == "protection_wrong_stop":
        protection["slTriggerPrice"] = "1"
    elif reason == "trigger_type_conflict":
        protection["triggerType"] = "sl"
    elif reason == "other_tpsl_id":
        protection["tpslId"] = "10004687496"
    elif reason == "two_effective":
        responses["orders-tpsl-history"].append(deepcopy(protection))
    elif reason == "close_wrong_trigger":
        close["tpTriggerPrice"] = "1"
    else:
        raise AssertionError(reason)


@pytest.mark.parametrize(
    "reason",
    [
        "no_algo_id",
        "parent_not_attested",
        "foreign_protection_client",
        "protection_not_reduce_only",
        "protection_too_late",
        "protection_before_entry",
        "protection_wrong_target",
        "protection_wrong_stop",
        "trigger_type_conflict",
        "other_tpsl_id",
        "two_effective",
        "close_wrong_trigger",
    ],
)
def test_unlinked_or_mismatched_attached_close_stays_held(reason):
    plan, entry, provider, responses, _ = attached_world("tp")
    _mutate(responses, reason)
    with pytest.raises((ValueError, ArithmeticError)):
        provider.reconcile_exit(plan=plan, entry=entry)
