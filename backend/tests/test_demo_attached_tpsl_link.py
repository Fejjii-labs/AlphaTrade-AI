"""BloFin's attached TP/SL row carries no parent or client id.

The staging demo returned this shape for a filled, protected entry: the TPSL row
has a blank clientOrderId, no orderId and a blank positionId, and order-detail
has no tpslId. Reconciliation must still verify that protection, and must keep
refusing every row that does not match the parent's attested terms exactly.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.demo_reconciliation import reconcile_native
from app.schemas.trade_plan import MarginMode
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase7_trade_plan import make_world, plan_command, plan_terms

_CREATED_MS = int(EVALUATED_AT.timestamp() * 1000)
_COID = "ATentry"


def _plan():
    world = make_world()
    terms = plan_terms(world.candidate)
    terms = terms.model_copy(
        update={
            "margin_mode": MarginMode.ISOLATED,
            "risk_and_exits": terms.risk_and_exits.model_copy(
                update={
                    "margin_assumption_id": "verified-demo-read-leverage",
                    "leverage": Decimal("1"),
                }
            ),
        }
    )
    return world.plans.create(plan_command(world, terms=terms)).plan


def _order(plan) -> dict[str, Any]:
    side = "sell" if plan.side.value == "SELL" else "buy"
    return {
        "orderId": "1000146253533",
        "clientOrderId": _COID,
        "instId": plan.execution_instrument,
        "marginMode": "isolated",
        "positionSide": "net",
        "positionId": "1000000988841",
        "side": side,
        "orderType": "market",
        "price": "0",
        "size": format(plan.quantity.value, "f"),
        "reduceOnly": "false",
        "state": "filled",
        "filledSize": format(plan.quantity.value, "f"),
        "averagePrice": "100000",
        "createTime": str(_CREATED_MS),
        "updateTime": str(_CREATED_MS + 13),
        "tpTriggerPrice": format(plan.risk_and_exits.targets[0].price.value, "f"),
        "slTriggerPrice": format(plan.risk_and_exits.stop.value, "f"),
        "slOrderPrice": "-1",
        "tpOrderPrice": "-1",
        "algoClientOrderId": "",
        "algoId": "",
    }


def _tpsl(plan, **overrides: Any) -> dict[str, Any]:
    row = {
        "tpslId": "10004687495",
        "instId": plan.execution_instrument,
        "marginMode": "isolated",
        "positionSide": "net",
        "positionId": "",
        "side": "buy" if plan.side.value == "SELL" else "sell",
        "tpTriggerPrice": format(plan.risk_and_exits.targets[0].price.value, "f"),
        "tpOrderPrice": "-1",
        "slTriggerPrice": format(plan.risk_and_exits.stop.value, "f"),
        "slOrderPrice": "-1",
        "size": format(plan.quantity.value, "f"),
        "state": "live",
        "reduceOnly": "true",
        "clientOrderId": "",
        "createTime": str(_CREATED_MS + 102),
    }
    row.update(overrides)
    return {k: v for k, v in row.items() if v is not None}


def _client(plan, protection: list[dict[str, Any]], order: dict[str, Any] | None = None):
    order = order or _order(plan)

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("order-detail"):
            data: object = [order]
        elif path.endswith("fills-history"):
            data = [
                {
                    "orderId": order["orderId"],
                    "tradeId": "fill-1",
                    "instId": plan.execution_instrument,
                    "side": order["side"],
                    "positionSide": "net",
                    "fillPrice": "100000",
                    "fillSize": format(plan.quantity.value, "f"),
                    "fee": "0",
                    "feeCurrency": "USDT",
                    "ts": str(_CREATED_MS + 13),
                }
            ]
        elif path.endswith("orders-tpsl-pending"):
            data = protection
        else:
            raise AssertionError(f"Unexpected endpoint: {path}")
        return httpx.Response(200, json={"code": "0", "data": data})

    return BloFinClient(
        base_url="https://demo-trading-openapi.blofin.com",
        api_key="simulated-key",
        api_secret="simulated-secret",
        api_passphrase="simulated-pass",
        transport=httpx.MockTransport(handle),
        sleeper=lambda _: None,
    )


def _reconcile(plan, protection, order=None):
    evidence = reconcile_native(
        _client(plan, protection, order),
        plan=plan,
        client_order_id=_COID,
        clock=lambda: EVALUATED_AT + timedelta(minutes=1),
    )
    assert evidence is not None
    return evidence


def test_blofin_attached_tpsl_without_ids_is_verified() -> None:
    plan = _plan()
    evidence = _reconcile(plan, [_tpsl(plan)])
    assert evidence.protected is True
    assert evidence.protection_status == "verified"
    assert evidence.protection_order_ids == ("10004687495",)
    assert evidence.diagnostics == ()


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"createTime": str(_CREATED_MS + 10_001)}, "created too long after the entry"),
        ({"createTime": str(_CREATED_MS - 1)}, "created before the entry"),
        ({"reduceOnly": "false"}, "not reduce-only"),
        ({"clientOrderId": "ATother"}, "belongs to another client order"),
        ({"orderId": "other-parent"}, "names another parent order"),
        ({"slTriggerPrice": "1"}, "stop differs from the plan"),
        ({"tpTriggerPrice": "1"}, "target differs from the plan"),
    ],
)
def test_unlinked_tpsl_stays_missing(overrides: dict[str, Any], reason: str) -> None:
    plan = _plan()
    evidence = _reconcile(plan, [_tpsl(plan, **overrides)])
    assert evidence.protected is False, reason
    assert evidence.protection_status == "missing", reason


def test_parent_without_attested_terms_never_links_by_terms() -> None:
    plan = _plan()
    order = _order(plan)
    order["slTriggerPrice"] = ""
    evidence = _reconcile(plan, [_tpsl(plan)], order)
    assert evidence.protected is False
    assert evidence.protection_status == "missing"


def test_two_attached_candidates_are_ambiguous() -> None:
    plan = _plan()
    evidence = _reconcile(
        plan,
        [_tpsl(plan), _tpsl(plan, tpslId="10004687496", createTime=str(_CREATED_MS + 500))],
    )
    assert evidence.protected is False
    assert evidence.protection_status == "ambiguous"
