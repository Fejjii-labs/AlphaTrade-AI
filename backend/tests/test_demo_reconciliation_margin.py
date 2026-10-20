"""Entry reconciliation follows the margin mode the order already posted.

Profile plans post isolated. Non-profile plans stay cross. Neither path writes
leverage or margin mode.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.demo_reconciliation import (
    DemoReconciliationError,
    reconcile_native,
)
from app.providers.exchange.governed_blofin import demo_order_margin_mode
from app.schemas.trade_plan import MarginMode
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase7_trade_plan import make_world, plan_command, plan_terms

_TS = str(int(EVALUATED_AT.timestamp() * 1000))


def _plan(assumption: str):
    world = make_world()
    terms = plan_terms(world.candidate)
    profile = assumption == "verified-demo-read-leverage"
    terms = terms.model_copy(
        update={
            "margin_mode": MarginMode.ISOLATED if profile else MarginMode.CROSS,
            "risk_and_exits": terms.risk_and_exits.model_copy(
                update={"margin_assumption_id": assumption, "leverage": Decimal("1")}
            ),
        }
    )
    return world.plans.create(plan_command(world, terms=terms)).plan


def _client(plan, *, venue_mode: str) -> tuple[BloFinClient, list[str]]:
    methods: list[str] = []
    side = "sell" if plan.side.value == "SELL" else "buy"
    protection_side = "buy" if side == "sell" else "sell"
    order = {
        "orderId": "demo-1",
        "clientOrderId": "simulated-entry",
        "instId": plan.execution_instrument,
        "side": side,
        "size": format(plan.quantity.value, "f"),
        "positionSide": "net",
        "marginMode": venue_mode,
        "orderType": "market",
        "createTime": _TS,
        "filledSize": format(plan.quantity.value, "f"),
        "state": "filled",
        "tpslId": "protect-1",
        "slTriggerPrice": format(plan.risk_and_exits.stop.value, "f"),
        "tpTriggerPrice": format(plan.risk_and_exits.targets[0].price.value, "f"),
        "slOrderPrice": "-1",
        "tpOrderPrice": "-1",
    }

    def handle(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        assert request.method == "GET"
        path = request.url.path
        if path.endswith("order-detail"):
            data: object = [order]
        elif path.endswith("fills-history"):
            data = [
                {
                    "orderId": "demo-1",
                    "tradeId": "fill-1",
                    "instId": plan.execution_instrument,
                    "side": side,
                    "positionSide": "net",
                    "fillPrice": "100000",
                    "fillSize": format(plan.quantity.value, "f"),
                    "fee": "0",
                    "feeCurrency": "USDT",
                    "ts": _TS,
                }
            ]
        elif path.endswith("orders-tpsl-pending"):
            data = [
                {
                    **order,
                    "side": protection_side,
                    "state": "live",
                    "clientOrderId": "simulated-entry",
                }
            ]
        else:
            raise AssertionError(f"Unexpected endpoint: {path}")
        return httpx.Response(200, json={"code": "0", "data": data})

    client = BloFinClient(
        base_url="https://demo-trading-openapi.blofin.com",
        api_key="simulated-key",
        api_secret="simulated-secret",
        api_passphrase="simulated-pass",
        transport=httpx.MockTransport(handle),
        sleeper=lambda _: None,
    )
    return client, methods


def test_isolated_profile_order_reconciles_with_matching_protection() -> None:
    plan = _plan("verified-demo-read-leverage")
    assert demo_order_margin_mode(plan.risk_and_exits.margin_assumption_id) == "isolated"
    client, methods = _client(plan, venue_mode="isolated")
    evidence = reconcile_native(
        client,
        plan=plan,
        client_order_id="simulated-entry",
        clock=lambda: EVALUATED_AT + timedelta(seconds=1),
    )
    assert evidence is not None
    assert evidence.order_id == "demo-1"
    assert evidence.protected is True
    assert evidence.protection_status == "verified"
    assert evidence.diagnostics == ()
    assert methods == ["GET", "GET", "GET"]


def test_cross_order_is_refused_when_the_venue_mode_differs() -> None:
    plan = _plan("verified-demo-existing-1x")
    assert plan.risk_and_exits.leverage == Decimal("1")
    assert demo_order_margin_mode(plan.risk_and_exits.margin_assumption_id) == "cross"
    client, methods = _client(plan, venue_mode="isolated")
    with pytest.raises(DemoReconciliationError) as caught:
        reconcile_native(
            client,
            plan=plan,
            client_order_id="simulated-entry",
            clock=lambda: EVALUATED_AT + timedelta(seconds=1),
        )
    assert caught.value.diagnostic.reason_code == "order_plan_mismatch"
    assert methods == ["GET"]
