"""Bounded closing-fill proof for the exclusively governed NET demo account.

No order mutations. Flat balances, acknowledgments and trigger acknowledgments
are insufficient. Unexplained activity or incomplete pages remain operator holds.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.demo_reconciliation import (
    ATTACHED_TPSL_WINDOW,
    order_attests_plan_exits,
)
from app.providers.exchange.governed_blofin import (
    DemoOrderEvidence,
    _account_rows,
    _positive,
    _time,
    demo_order_margin_mode,
)
from app.schemas.trade_plan import EntrySide, TradePlanRevision


class DemoPositionLineageError(ValueError):
    """Account inventory cannot be explained by this exclusive owned entry."""


@dataclass(frozen=True)
class DemoExitFill:
    order_id: str
    trade_id: str
    quantity: Decimal
    price: Decimal
    occurred_at: datetime
    fee: Decimal | None
    reported_pnl: Decimal | None
    category: str
    fee_currency: str | None = None
    funding: Decimal | None = None


@dataclass(frozen=True)
class VerifiedDemoExit:
    entry_order_id: str
    entry_client_order_id: str
    entry_fill_identities: tuple[str, ...]
    entry_quantity: Decimal
    fills: tuple[DemoExitFill, ...]
    protection_ids: tuple[str, ...]
    observed_at: datetime
    entry_protection_ids: tuple[str, ...] = ()
    native_entry_tpsl_id: str | None = None
    # Raw provider pointers are retained without assuming an undocumented edge.
    exit_order_protection_ids: tuple[tuple[str, str], ...] = ()
    native_account_uid: str | None = None
    funding: Decimal | None = None

    def facts(self) -> dict[str, Any]:
        """Stable economic evidence; receipt clock is persisted separately."""
        return {
            "entry_order_id": self.entry_order_id,
            "native_account_uid": self.native_account_uid,
            "entry_client_order_id": self.entry_client_order_id,
            "entry_fill_identities": list(self.entry_fill_identities),
            "entry_quantity": str(self.entry_quantity),
            "exit_fills": [
                {
                    "order_id": fill.order_id,
                    "trade_id": fill.trade_id,
                    "quantity": str(fill.quantity),
                    "price": str(fill.price),
                    "occurred_at": fill.occurred_at.isoformat(),
                    "fee": str(fill.fee) if fill.fee is not None else None,
                    "fee_currency": fill.fee_currency,
                    "fillPnl": str(fill.reported_pnl) if fill.reported_pnl is not None else None,
                    "category": fill.category,
                }
                for fill in self.fills
            ],
            "protection_ids": list(self.protection_ids),
            "entry_protection_ids": list(self.entry_protection_ids),
            "native_entry_tpsl_id": self.native_entry_tpsl_id,
            "exit_order_protection_ids": [
                {"order_id": order_id, "tpsl_id": tpsl_id}
                for order_id, tpsl_id in self.exit_order_protection_ids
            ],
            "protective_parent_child_verified": None,
            "fee_convention": None,
            "pnl_excludes_fees": None,
            "monetary_methodology_version": None,
            "positions": "flat_account_wide",
            "pending_orders": "none_account_wide",
            "funding": (
                format(self.funding, "f")
                if isinstance(self.funding, Decimal) and self.funding.is_finite()
                else None
            ),
            "net_pnl": None,
        }


def reconcile_demo_exit(
    client: BloFinClient,
    *,
    plan: TradePlanRevision,
    entry: DemoOrderEvidence,
    clock: Callable[[], datetime],
) -> VerifiedDemoExit | None:
    expected_margin = demo_order_margin_mode(
        plan.risk_and_exits.margin_assumption_id, str(plan.margin_mode.value)
    )
    if not entry.fills or entry.status not in {"filled", "canceled", "cancelled", "expired"}:
        return None  # A live entry remainder can still add exposure.
    positions = _account_rows(client.request("GET", "/api/v1/account/positions", signed=True))
    open_positions = []
    for row in positions:
        quantity = Decimal(str(row.get("positions")))
        if not quantity.is_finite():
            raise ValueError("Unreadable demo position during exit reconciliation.")
        if quantity != 0:
            open_positions.append((row, quantity))
    if open_positions:
        entry_quantity = sum((f.quantity for f in entry.fills), Decimal(0))
        if len(open_positions) != 1:
            raise DemoPositionLineageError("Unrelated demo account inventory.")
        row, quantity = open_positions[0]
        signed_quantity = quantity if plan.side is EntrySide.BUY else -quantity
        if (
            row.get("instId") != plan.execution_instrument
            or row.get("positionSide") != "net"
            or row.get("marginMode") != expected_margin
            or not 0 < signed_quantity <= entry_quantity
        ):
            raise DemoPositionLineageError("Demo position exceeds or differs from owned fills.")
        return None  # Partial exits retain capacity until all closing facts arrive.
    for endpoint in ("orders-pending", "orders-tpsl-pending"):
        if _account_rows(
            client.request("GET", f"/api/v1/trade/{endpoint}", params={"limit": "100"}, signed=True)
        ):
            return None
    last_entry = max(fill.occurred_at for fill in entry.fills)
    begin = str(int(plan.valid_from.timestamp() * 1000))
    orders = _account_rows(
        client.request(
            "GET",
            "/api/v1/trade/orders-history",
            params={"begin": begin, "limit": "100"},
            signed=True,
        )
    )
    if len(orders) > 20:
        raise ValueError("Demo exit history exceeds bounded reconciliation budget.")
    opposite = "sell" if plan.side is EntrySide.BUY else "buy"
    fills: list[DemoExitFill] = []
    order_ids: set[str] = set()
    trigger_categories: set[str] = set()
    exit_order_protection_ids: list[tuple[str, str]] = []
    entry_created: datetime | None = None
    entry_attests_exits = False
    for row in orders:
        order_id = str(row.get("orderId", ""))
        if not order_id or order_id in order_ids:
            raise ValueError("Incomplete or duplicate demo order history.")
        order_ids.add(order_id)
        if order_id == entry.order_id:
            if (
                row.get("clientOrderId") != entry.client_order_id
                or row.get("instId") != plan.execution_instrument
                or row.get("side") != ("buy" if plan.side is EntrySide.BUY else "sell")
                or row.get("positionSide") != "net"
                or row.get("marginMode") != expected_margin
                or row.get("state") != entry.status
                or Decimal(str(row.get("size"))) != plan.quantity.value
                or Decimal(str(row.get("filledSize")))
                != sum((f.quantity for f in entry.fills), Decimal("0"))
            ):
                raise ValueError("Demo entry history identity mismatch.")
            # Only the blank-client-id TPSL lineage needs these; absent, it is refused.
            if row.get("createTime") not in (None, ""):
                entry_created = _time(row.get("createTime"))
            entry_attests_exits = order_attests_plan_exits(row, plan)
            continue
        filled = Decimal(str(row.get("filledSize")))
        if not filled.is_finite() or filled < 0:
            raise ValueError("Unreadable demo order fill total.")
        if filled == 0 and row.get("state") in {"canceled", "cancelled", "expired"}:
            continue
        if (
            row.get("instId") != plan.execution_instrument
            or row.get("side") != opposite
            or row.get("positionSide") != "net"
            or row.get("marginMode") != expected_margin
            or not (row.get("reduceOnly") is True or row.get("reduceOnly") == "true")
            or row.get("state") not in {"filled", "canceled", "cancelled", "expired"}
            or filled <= 0
            or _time(row.get("createTime")) < plan.valid_from
        ):
            raise ValueError("Unexplained demo account activity; exit remains held.")
        category = str(row.get("orderCategory", ""))
        if category not in {"normal", "tp", "sl"}:
            raise ValueError("Unsupported demo exit category.")
        if len(order_ids) > 11:
            raise ValueError("Demo closing-order budget exhausted.")
        # BloFin names the triggering TPSL as algoId on the closing order.
        tpsl_id = row.get("tpslId") or row.get("algoId")
        if tpsl_id not in (None, ""):
            if not isinstance(tpsl_id, str) or len(tpsl_id) > 128:
                raise ValueError("Unreadable demo closing protection identity.")
            exit_order_protection_ids.append((order_id, tpsl_id))
        if category in {"tp", "sl"}:
            expected = (
                plan.risk_and_exits.stop.value
                if category == "sl"
                else plan.risk_and_exits.targets[0].price.value
            )
            trigger = row.get(f"{category}TriggerPrice")
            if trigger in (None, ""):
                # BloFin omits trigger prices on the closing order; they are then
                # proven on the effective TPSL row named by its algoId below.
                if tpsl_id in (None, ""):
                    raise ValueError("Demo trigger exit names no protection.")
            elif Decimal(str(trigger)) != expected:
                raise ValueError("Demo trigger exit differs from approved plan.")
            trigger_categories.add(category)
        rows = _account_rows(
            client.request(
                "GET",
                "/api/v1/trade/fills-history",
                params={"instId": plan.execution_instrument, "orderId": order_id, "limit": "100"},
                signed=True,
            )
        )
        order_fills: list[DemoExitFill] = []
        for fill in rows:
            if (
                fill.get("orderId") != order_id
                or fill.get("instId") != plan.execution_instrument
                or fill.get("side") != opposite
                or fill.get("positionSide") != "net"
                or not fill.get("tradeId")
            ):
                raise ValueError("Demo closing fill identity mismatch.")
            fee_currency = fill.get("feeCurrency")
            if fee_currency not in (None, "") and (
                not isinstance(fee_currency, str) or not 0 < len(fee_currency) <= 128
            ):
                raise ValueError("Unreadable demo closing fee currency.")
            fee = Decimal(str(fill["fee"])) if fill.get("fee") not in (None, "") else None
            gross = Decimal(str(fill["fillPnl"])) if fill.get("fillPnl") not in (None, "") else None
            at = _time(fill.get("ts"))
            if (
                (fee is not None and not fee.is_finite())
                or (gross is not None and not gross.is_finite())
                or at < last_entry
            ):
                raise ValueError("Invalid demo closing fill economics or chronology.")
            order_fills.append(
                DemoExitFill(
                    order_id,
                    str(fill["tradeId"]),
                    _positive(fill.get("fillSize")),
                    _positive(fill.get("fillPrice")),
                    at,
                    fee,
                    gross,
                    category,
                    fee_currency or None,
                    # TODO: BloFin GET /api/v1/trade/fills-history does not document
                    # a funding field or its sign (https://docs.blofin.com/index.html).
                    # fundingFee on GET /api/v1/account/funding-fees is a separate
                    # bill (positive means received). Leave fill funding unverified.
                    None,
                )
            )
        if sum((f.quantity for f in order_fills), Decimal("0")) != filled:
            raise ValueError("Incomplete demo closing fills.")
        fills.extend(order_fills)
        if len(fills) > 100:
            raise ValueError("Demo closing-fill budget exhausted.")
    if entry.order_id not in order_ids:
        raise ValueError("Demo entry absent from bounded account history.")
    quantity = sum((fill.quantity for fill in entry.fills), Decimal("0"))
    if sum((fill.quantity for fill in fills), Decimal("0")) != quantity:
        return None  # Flat account alone is never a close proof.
    if len({(f.order_id, f.trade_id) for f in fills}) != len(fills):
        raise ValueError("Duplicate demo exit fill identity.")
    protection_ids: list[str] = []
    if trigger_categories:
        named = {tpsl_id for _, tpsl_id in exit_order_protection_ids}

        def tpsl_history(params: dict[str, str]) -> list[dict[str, Any]]:
            return _account_rows(
                client.request(
                    "GET",
                    "/api/v1/trade/orders-tpsl-history",
                    params={**params, "limit": "100"},
                    signed=True,
                )
            )

        history = tpsl_history({"clientOrderId": entry.client_order_id})
        effective = [r for r in history if r.get("state") == "effective"]
        via_named = False
        if not effective and len(named) == 1:
            # BloFin's attached TPSL has a blank clientOrderId, so the lookup by
            # the entry's client id finds nothing; the closing order's algoId names it.
            via_named = True
            effective = [
                r
                for r in tpsl_history({"tpslId": next(iter(named))})
                if r.get("state") == "effective" and r.get("tpslId") in named
            ]
        if len(effective) != 1:
            raise ValueError("Ambiguous effective protection history.")
        row = effective[0]
        client_lineage = row.get("clientOrderId") == entry.client_order_id
        # Without a client id, lineage needs the entry's own attested TP/SL, a
        # closing order naming this TPSL, and creation just after the entry.
        attached_lineage = (
            via_named
            and entry_attests_exits
            and entry_created is not None
            and row.get("clientOrderId") in (None, "")
            and str(row.get("reduceOnly")).lower() == "true"
            and row.get("slTriggerPrice") not in (None, "")
            and row.get("tpTriggerPrice") not in (None, "")
            and Decimal(str(row["slTriggerPrice"])) == plan.risk_and_exits.stop.value
            and Decimal(str(row["tpTriggerPrice"])) == plan.risk_and_exits.targets[0].price.value
            and entry_created
            <= _time(row.get("createTime"))
            <= entry_created + ATTACHED_TPSL_WINDOW
        )
        trigger_type = row.get("triggerType")
        if (
            not row.get("tpslId")
            or not (client_lineage or attached_lineage)
            or (trigger_type not in (None, "") and trigger_type not in trigger_categories)
            or row.get("instId") != plan.execution_instrument
            or row.get("side") != opposite
            or row.get("positionSide") != "net"
            or row.get("marginMode") != expected_margin
            or _positive(row.get("actualSize")) != quantity
            or _time(row.get("createTime")) < plan.valid_from
        ):
            raise ValueError("Effective protection lineage mismatch.")
        for category in trigger_categories:
            expected = (
                plan.risk_and_exits.stop.value
                if category == "sl"
                else plan.risk_and_exits.targets[0].price.value
            )
            if Decimal(str(row.get(f"{category}TriggerPrice"))) != expected:
                raise ValueError("Effective protection differs from approved plan.")
        protection_ids.append(str(row["tpslId"]))
    # Recheck after the history reads; the first account snapshot alone cannot
    # establish the final account state during a long provider round trip.
    positions = _account_rows(client.request("GET", "/api/v1/account/positions", signed=True))
    for row in positions:
        amount = Decimal(str(row.get("positions")))
        if not amount.is_finite():
            raise ValueError("Unreadable final demo position.")
        if amount != 0:
            return None
    for endpoint in ("orders-pending", "orders-tpsl-pending"):
        if _account_rows(
            client.request("GET", f"/api/v1/trade/{endpoint}", params={"limit": "100"}, signed=True)
        ):
            return None
    observed = clock()
    if any(fill.occurred_at > observed for fill in fills):
        raise ValueError("Future-dated demo closing fill.")
    return VerifiedDemoExit(
        entry.order_id,
        entry.client_order_id,
        tuple(sorted(f.identity for f in entry.fills)),
        quantity,
        tuple(sorted(fills, key=lambda f: (f.occurred_at, f.order_id, f.trade_id))),
        tuple(protection_ids),
        observed,
        entry.protection_order_ids,
        entry.native_tpsl_id,
        tuple(sorted(exit_order_protection_ids)),
        entry.native_account_uid,
        None,
    )
