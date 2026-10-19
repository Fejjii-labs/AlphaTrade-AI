"""Actual closing-fill protocol proof; no account mutations or network orders."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from app.providers.exchange.governed_blofin import DemoFill, DemoOrderEvidence
from tests.support.phase5_market import EVALUATED_AT
from tests.test_governed_blofin_quote_freshness import _plan
from tests.test_governed_demo_readiness import provider_for


def exit_world(category="normal", *, partial=False):
    plan = _plan(valid_for_ms=6000)
    quantity = plan.quantity.value / (2 if partial else 1)
    ts = str(int(EVALUATED_AT.timestamp() * 1000))
    later = str(int((EVALUATED_AT + timedelta(seconds=1)).timestamp() * 1000))
    entry = DemoOrderEvidence(
        "entry-1",
        "authorized-client-1",
        "canceled" if partial else "filled",
        (
            DemoFill(
                "entry-1:entry-fill-1", quantity, Decimal("100000"), EVALUATED_AT, Decimal("0.02")
            ),
        ),
        False,
        "missing",
    )
    parent = {
        "orderId": entry.order_id,
        "clientOrderId": entry.client_order_id,
        "instId": plan.execution_instrument,
        "size": str(plan.quantity.value),
        "filledSize": str(quantity),
        "side": "sell",
        "positionSide": "net",
        "marginMode": "cross",
        "state": entry.status,
        "createTime": ts,
    }
    close = {
        "orderId": "close-1",
        "clientOrderId": "venue-close-client",
        "instId": plan.execution_instrument,
        "size": str(quantity),
        "filledSize": str(quantity),
        "side": "buy",
        "positionSide": "net",
        "marginMode": "cross",
        "state": "filled",
        "createTime": later,
        "reduceOnly": "true",
        "orderCategory": category,
        "tpTriggerPrice": str(plan.risk_and_exits.targets[0].price.value),
        "slTriggerPrice": str(plan.risk_and_exits.stop.value),
    }
    fill = {
        "orderId": "close-1",
        "tradeId": "close-fill-1",
        "instId": plan.execution_instrument,
        "side": "buy",
        "positionSide": "net",
        "fillSize": str(quantity),
        "fillPrice": "99900",
        "fee": "0.03",
        "fillPnl": "0.2",
        "ts": later,
    }
    protection = {
        **close,
        "clientOrderId": entry.client_order_id,
        "tpslId": "protect-1",
        "state": "effective",
        "actualSize": str(quantity),
        "createTime": ts,
    }
    responses = {
        "query-apikey": {"readOnly": 0},
        "positions": [],
        "orders-pending": [],
        "orders-tpsl-pending": [],
        "orders-history": [parent, close],
        "fills-history": [fill],
        "orders-tpsl-history": [protection],
    }
    calls = []

    def handler(request):
        assert request.method == "GET", "Exit reconciliation cannot place or cancel orders"
        calls.append(request)
        endpoint = request.url.path.rsplit("/", 1)[1]
        if endpoint == "orders-history":
            assert "instId" not in request.url.params
        if endpoint == "fills-history":
            assert request.url.params["orderId"] == "close-1"
        if endpoint == "orders-tpsl-history":
            assert request.url.params["clientOrderId"] == entry.client_order_id
        return httpx.Response(200, json={"code": "0", "data": deepcopy(responses[endpoint])})

    provider = provider_for(handler)
    provider._clock = lambda: EVALUATED_AT + timedelta(seconds=2)
    return plan, entry, provider, responses, calls


@pytest.mark.parametrize("category,partial", [("normal", False), ("tp", False), ("sl", True)])
def test_verified_actual_close_and_partial_entry_keep_real_economics(category, partial):
    plan, entry, provider, _, calls = exit_world(category, partial=partial)
    closed = provider.reconcile_exit(plan=plan, entry=entry)
    assert closed is not None
    assert closed.entry_quantity == entry.fills[0].quantity
    assert closed.fills[0].reported_pnl == Decimal("0.2")
    assert closed.fills[0].fee == Decimal("0.03")
    assert closed.facts()["funding"] is None and closed.facts()["net_pnl"] is None
    assert closed.protection_ids == (() if category == "normal" else ("protect-1",))
    assert len(calls) <= 11  # UID/permission checks bracket the history reads.


@pytest.mark.parametrize(
    "reason", ["open", "pending", "live_entry", "missing_closing_fills", "partial_exit"]
)
def test_absence_or_partial_close_never_infers_closed(reason):
    plan, entry, provider, responses, _ = exit_world()
    if reason == "open":
        responses["positions"] = [
            {
                "positions": str(-plan.quantity.value),
                "instId": plan.execution_instrument,
                "positionSide": "net",
                "marginMode": "cross",
            }
        ]
    elif reason == "pending":
        responses["orders-pending"] = [{"orderId": "other-market-pending"}]
    elif reason == "live_entry":
        from dataclasses import replace

        entry = replace(entry, status="partially_filled")
    elif reason == "missing_closing_fills":
        responses["orders-history"] = responses["orders-history"][:1]
    else:
        responses["orders-history"][1]["filledSize"] = "1"
        responses["fills-history"][0]["fillSize"] = "1"
    assert provider.reconcile_exit(plan=plan, entry=entry) is None


@pytest.mark.parametrize("endpoint", ["positions", "orders-pending", "orders-tpsl-pending"])
def test_account_activity_arriving_during_history_reads_keeps_exit_held(endpoint):
    plan, entry, provider, responses, _ = exit_world()
    original = provider._client.request

    def request(method, path, **kwargs):
        reply = original(method, path, **kwargs)
        if path.endswith("fills-history"):
            responses[endpoint] = (
                [{"positions": "1", "instId": "ETH-USDT"}]
                if endpoint == "positions"
                else [{"orderId": "new-unrelated-order"}]
            )
        return reply

    provider._client.request = request
    assert provider.reconcile_exit(plan=plan, entry=entry) is None


@pytest.mark.parametrize(
    "reason",
    [
        "malformed_positions",
        "incomplete_page",
        "opening_activity",
        "other_market",
        "hedge",
        "wrong_parent",
        "duplicate_order",
        "duplicate_fill",
        "missing_fill",
        "future_fill",
        "before_entry",
        "wrong_trigger",
        "wrong_protection",
        "ambiguous_protection",
    ],
)
def test_unexplained_or_incomplete_exit_evidence_is_refused(reason):
    plan, entry, provider, responses, _ = exit_world("tp")
    if reason == "malformed_positions":
        responses["positions"] = [None]
    elif reason == "incomplete_page":
        responses["orders-history"] *= 50
    elif reason == "opening_activity":
        responses["orders-history"][1]["reduceOnly"] = "false"
    elif reason == "other_market":
        responses["orders-history"][1]["instId"] = "ETH-USDT"
    elif reason == "hedge":
        responses["fills-history"][0]["positionSide"] = "long"
    elif reason == "wrong_parent":
        responses["orders-history"][0]["clientOrderId"] = "foreign-parent"
    elif reason == "duplicate_order":
        responses["orders-history"].append(deepcopy(responses["orders-history"][1]))
    elif reason == "duplicate_fill":
        responses["fills-history"] *= 2
    elif reason == "missing_fill":
        responses["fills-history"] = []
    elif reason in {"future_fill", "before_entry"}:
        delta = 3 if reason == "future_fill" else -1
        responses["fills-history"][0]["ts"] = str(
            int((EVALUATED_AT + timedelta(seconds=delta)).timestamp() * 1000)
        )
    elif reason == "wrong_trigger":
        responses["orders-history"][1]["tpTriggerPrice"] = "1"
    elif reason == "wrong_protection":
        responses["orders-tpsl-history"][0]["clientOrderId"] = "foreign-protection"
    else:
        responses["orders-tpsl-history"] *= 2
    with pytest.raises((ValueError, ArithmeticError)):
        provider.reconcile_exit(plan=plan, entry=entry)


def test_missing_reported_realized_pnl_is_explicit_not_computed_from_prices():
    plan, entry, provider, responses, _ = exit_world()
    responses["fills-history"][0].pop("fillPnl")
    closed = provider.reconcile_exit(plan=plan, entry=entry)
    assert closed is not None and closed.fills[0].reported_pnl is None


@pytest.mark.parametrize("currency", [None, "BTC"])
def test_actual_exit_retains_fee_currency_without_inferred_monetary_basis(currency):
    plan, entry, provider, responses, _ = exit_world()
    responses["fills-history"][0]["feeCurrency"] = currency
    closed = provider.reconcile_exit(plan=plan, entry=entry)
    assert closed is not None and closed.fills[0].fee == Decimal("0.03")
    assert closed.fills[0].fee_currency == currency
    assert closed.facts()["exit_fills"][0]["fee_currency"] == currency
    assert closed.facts()["fee_convention"] is None
    assert closed.facts()["net_pnl"] is closed.facts()["funding"] is None


@pytest.mark.parametrize("problem", ["foreign", "oversized", "wrong_side", "duplicate", "hedge"])
def test_open_position_must_be_explained_by_owned_net_fills(problem):
    from app.providers.exchange.governed_blofin_exit import DemoPositionLineageError

    plan, entry, *_ = exit_world()
    row = {
        "instId": plan.execution_instrument,
        "positionSide": "net",
        "marginMode": "cross",
        "positions": str(-plan.quantity.value),
    }
    rows = [row]
    if problem == "foreign":
        row["instId"] = "ETH-USDT"
    elif problem == "oversized":
        row["positions"] = str(-plan.quantity.value * 2)
    elif problem == "wrong_side":
        row["positions"] = str(plan.quantity.value)
    elif problem == "duplicate":
        rows = [row, dict(row)]
    elif problem == "hedge":
        row["positionSide"] = "short"

    def handle(request):
        assert request.method == "GET"
        if request.url.path.endswith("query-apikey"):
            return httpx.Response(200, json={"code": "0", "data": {"readOnly": 0}})
        assert request.url.path.endswith("positions")
        return httpx.Response(200, json={"code": "0", "data": rows})

    with pytest.raises(DemoPositionLineageError):
        provider_for(handle).reconcile_exit(plan=plan, entry=entry)


def test_execution_connection_requires_the_experiment_owned_uid_before_entry_io():
    from app.providers.exchange.demo_preflight import DemoPreflightError

    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET" and request.url.path.endswith("query-apikey")
        return httpx.Response(
            200, json={"code": "0", "data": {"readOnly": 0, "uid": "foreign-uid"}}
        )

    provider = provider_for(handler)
    provider.require_account_identity("owned-uid")
    with pytest.raises(DemoPreflightError):
        provider.verify_permissions()
    assert requests == [("GET", "/api/v1/user/query-apikey")]
