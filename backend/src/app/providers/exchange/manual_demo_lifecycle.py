"""Manual demo account and exit evidence. GET only; flatness never fabricates an exit."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.providers.exchange.demo_reconciliation import (
    FILLS,
    PROTECTION,
    diagnostic_for,
    history_rows,
    identity,
    number,
    occurrence,
    require,
    rows,
    stage,
)
from app.providers.exchange.governed_blofin import DemoOrderEvidence, GovernedBloFinDemoProvider
from app.schemas.manual_demo import DemoReconciliationDiagnostic
from app.schemas.trade_plan import TradePlanRevision

POSITIONS = "GET /api/v1/account/positions"
PENDING = "GET /api/v1/trade/orders-pending"
HISTORY = "GET /api/v1/trade/orders-history"
TPSL_HISTORY = "GET /api/v1/trade/orders-tpsl-history"
TERMINAL = {"filled", "canceled", "cancelled", "expired", "rejected"}


def decimal_text(value: Decimal) -> str:
    # Exact canonicalization for new lifecycle facts, not the shared entry hash.
    if value == 0:
        return "0"
    return (
        format(value, "f").rstrip("0").rstrip(".")
        if "." in format(value, "f")
        else format(value, "f")
    )


@dataclass(frozen=True)
class ManualLifecycleObservation:
    observed_at: datetime
    position_status: str
    protection_history: tuple[dict[str, Any], ...] = ()
    exit_fills: tuple[dict[str, Any], ...] = ()
    account_flat: bool = False
    account_idle: bool = False
    resolution_reason: str | None = None
    recovery_reason: str = (
        "Complete native lifecycle proof is unavailable; the account claim remains held."
    )
    diagnostics: tuple[DemoReconciliationDiagnostic, ...] = ()
    protection_diagnostics: tuple[DemoReconciliationDiagnostic, ...] = ()
    triggered_protection: str = "unverified"
    account_verified: bool = False

    def facts(self) -> dict[str, Any]:
        quantity = sum((Decimal(f["quantity"]) for f in self.exit_fills), Decimal("0"))
        price = (
            sum(
                (Decimal(f["quantity"]) * Decimal(f["price"]) for f in self.exit_fills),
                Decimal("0"),
            )
            / quantity
            if quantity
            else None
        )
        pnl = (
            sum((Decimal(f["fillPnl"]) for f in self.exit_fills), Decimal("0"))
            if self.exit_fills and all(f["fillPnl"] is not None for f in self.exit_fills)
            else None
        )
        currencies = {f.get("fee_currency") for f in self.exit_fills}
        fee_currency = next(iter(currencies)) if len(currencies) == 1 else None
        fees_available = (
            self.exit_fills
            and fee_currency is not None
            and all(f.get("fee") is not None for f in self.exit_fills)
        )
        return {
            "position_status": self.position_status,
            "account_verified": self.account_verified,
            "triggered_protection": self.triggered_protection,
            "protection_diagnostics": [
                d.model_dump(exclude_none=True) for d in self.protection_diagnostics
            ],
            "protection_history": list(self.protection_history),
            "exit_fills": list(self.exit_fills),
            "account_flat": self.account_flat,
            "account_idle": self.account_idle,
            "resolution_reason": self.resolution_reason,
            "resolution_eligible": bool(self.resolution_reason),
            "recovery_reason": self.recovery_reason,
            "exit_quantity": decimal_text(quantity),
            "exit_price": decimal_text(price) if price is not None else None,
            "exit_fees": decimal_text(
                sum((Decimal(f["fee"]) for f in self.exit_fills), Decimal("0"))
            )
            if fees_available
            else None,
            "exit_fee_currency": fee_currency if fees_available else None,
            "venue_reported_fill_pnl": decimal_text(pnl) if pnl is not None else None,
            "diagnostics": [d.model_dump(exclude_none=True) for d in self.diagnostics],
        }


def observe_positions(provider: GovernedBloFinDemoProvider) -> bool:
    with stage("account_lookup", POSITIONS):
        positions = rows(
            provider._client.request("GET", POSITIONS.removeprefix("GET "), signed=True)
        )
        for position in positions:
            require(
                isinstance(position.get("instId"), str) and bool(position["instId"]),
                "account_position_identity_missing",
            )
            require(
                position.get("positionSide") in {"net", "long", "short"},
                "account_position_mode_unknown",
            )
        flat = all(number(p.get("positions"), field="positions") == 0 for p in positions)
    return flat


def observe_account_idle(provider: GovernedBloFinDemoProvider) -> bool:
    pending = False
    for endpoint in (PENDING, PROTECTION):
        with stage("account_lookup", endpoint):
            pending |= bool(
                rows(
                    provider._client.request(
                        "GET", endpoint.removeprefix("GET "), params={"limit": "100"}, signed=True
                    )
                )
            )
    return not pending


def observe_lifecycle(
    provider: GovernedBloFinDemoProvider,
    *,
    plan: TradePlanRevision,
    entry: DemoOrderEvidence | None,
    proven_unsent: bool = False,
    known_protection_ids: tuple[str, ...] = (),
) -> ManualLifecycleObservation:
    flat, idle = False, False
    account_verified = False
    protect_facts: list[dict[str, Any]] = []
    exits: list[dict[str, Any]] = []
    protection_diagnostics: list[DemoReconciliationDiagnostic] = []
    triggered = "unverified"
    triggered_orders: set[str] = set()
    try:
        flat = observe_positions(provider)
        account_verified = True
        idle = observe_account_idle(provider)
        if proven_unsent:
            return ManualLifecycleObservation(
                provider._clock(),
                "flat_account" if flat else "account_position_present",
                account_flat=flat,
                account_idle=idle,
                account_verified=True,
                resolution_reason="proven_unsent" if flat and idle else None,
                recovery_reason=(
                    "Local dispatch never occurred. Recovery also requires a flat "
                    "account with no pending orders/protection."
                ),
            )
        if entry is None:
            return ManualLifecycleObservation(
                provider._clock(),
                "flat_exit_unverified" if flat else "account_position_present",
                account_flat=flat,
                account_idle=idle,
                account_verified=True,
                recovery_reason=(
                    "Submission remains unknown. Missing order lookup or flat account "
                    "alone cannot release this command; inspect its native client "
                    "order ID."
                ),
            )
        if not entry.fills:
            eligible = entry.status in TERMINAL and entry.status != "filled" and flat and idle
            return ManualLifecycleObservation(
                provider._clock(),
                "flat_account" if flat else "account_position_present",
                account_flat=flat,
                account_idle=idle,
                account_verified=True,
                resolution_reason="terminal_unfilled" if eligible else None,
                recovery_reason=(
                    "Unfilled entry requires its native canceled/rejected/expired "
                    "state and a flat, idle current account."
                ),
            )
        with stage("exit_lookup", HISTORY):
            history = history_rows(
                provider._client,
                endpoint=HISTORY,
                cursor_field="orderId",
                params={
                    "begin": str(int(plan.valid_from.timestamp() * 1000)),
                    "end": str(int(provider._clock().timestamp() * 1000)),
                },
            )
        opposite = "sell" if plan.side.value == "BUY" else "buy"
        linked: list[dict[str, Any]] = []
        # Protection is a separate confidence axis. Native documented TPSL rows
        # have no guaranteed parent orderId or generated child orderId. Never
        # synthesize lineage from matching prices, size, or account flatness.
        try:
            with stage("exit_lookup", TPSL_HISTORY):
                protection = history_rows(
                    provider._client,
                    endpoint=TPSL_HISTORY,
                    cursor_field="tpslId",
                    params={"instId": plan.execution_instrument},
                )
            with stage("exit_parse", TPSL_HISTORY):
                ids = {*known_protection_ids, *entry.protection_order_ids}
                if entry.native_tpsl_id:
                    ids.add(entry.native_tpsl_id)
                linked = [
                    p
                    for p in protection
                    if p.get("clientOrderId") == entry.client_order_id
                    or p.get("orderId") == entry.order_id
                    or p.get("tpslId") in ids
                ]
                require(len(linked) <= 1, "protection_history_ambiguous")
                for p in linked:
                    pid = identity(p.get("tpslId"))
                    require(
                        p.get("state") in {"live", "effective", "canceled", "order_failed"},
                        "protection_history_state_unknown",
                    )
                    require(
                        p.get("instId") == plan.execution_instrument
                        and p.get("positionSide") == "net"
                        and p.get("marginMode") == "cross"
                        and p.get("side") == opposite,
                        "protection_history_terms_mismatch",
                    )
                    if p.get("createTime") not in {None, ""}:
                        occurrence(p["createTime"], earliest=plan.valid_from, now=provider._clock())
                    protect_facts.append({"tpsl_id": pid, "state": p["state"]})
                require(bool(linked), "protection_history_link_unavailable")
        except Exception as exc:
            linked = []
            protection_diagnostics.append(
                diagnostic_for(exc, stage="exit_lookup", endpoint=TPSL_HISTORY)
            )
        with stage("exit_parse", HISTORY):
            require(len(history) <= 1000, "exit_history_budget_exhausted")
            seen = set()
            candidates = []
            closure_conflict = False
            quantity = sum((f.quantity for f in entry.fills), Decimal("0"))
            for order in history:
                oid = identity(order.get("orderId"))
                require(oid not in seen, "exit_order_identity_duplicate")
                seen.add(oid)
                total = number(order.get("filledSize"), field="filledSize")
                require(total >= 0, "exit_order_quantity_invalid")
                if oid == entry.order_id:
                    require(
                        order.get("clientOrderId") == entry.client_order_id
                        and order.get("instId") == plan.execution_instrument
                        and order.get("side") == ("buy" if plan.side.value == "BUY" else "sell")
                        and order.get("positionSide") == "net"
                        and order.get("marginMode") == "cross"
                        and number(order.get("size"), field="size", positive=True)
                        == plan.quantity.value
                        and total == quantity
                        and order.get("state") == entry.status,
                        "entry_history_identity_mismatch",
                    )
                    continue
                if total == 0 and order.get("state") in {
                    "canceled",
                    "cancelled",
                    "expired",
                    "rejected",
                }:
                    continue
                # Complete account-wide history must contain no second opening trade.
                # Only this isolated entry and matching native reduce-only exits can
                # establish a sequence; a symbol/price/time resemblance cannot.
                require(
                    order.get("instId") == plan.execution_instrument
                    and order.get("side") == opposite
                    and order.get("positionSide") == "net"
                    and order.get("marginMode") == "cross"
                    and order.get("reduceOnly") in {True, "true"}
                    and total > 0
                    and order.get("state") in TERMINAL,
                    "unexplained_account_activity",
                )
                category = order.get("orderCategory")
                require(category in {"normal", "nomal", "tp", "sl"}, "exit_category_unsupported")
                created = occurrence(
                    order.get("createTime"),
                    earliest=max(f.occurred_at for f in entry.fills),
                    now=provider._clock(),
                )
                if category in {"tp", "sl"}:
                    # Exact order/fill identity and the isolated account sequence
                    # establish the exit independently of missing TPSL ancestry.
                    # Only direct native parent/child linkage establishes *which*
                    # protection triggered. A matching trigger price is insufficient.
                    if linked and linked[0]["state"] != "effective":
                        closure_conflict = True
                    if linked and (
                        order.get("tpslId") == linked[0].get("tpslId")
                        or linked[0].get("orderId") == oid
                    ):
                        expected = (
                            plan.risk_and_exits.stop.value
                            if category == "sl"
                            else plan.risk_and_exits.targets[0].price.value
                        )
                        require(
                            number(
                                linked[0].get(f"{category}TriggerPrice"),
                                field=f"{category}TriggerPrice",
                            )
                            == expected,
                            "exit_trigger_plan_mismatch",
                        )
                        require(
                            number(linked[0].get("actualSize"), field="actualSize", positive=True)
                            == total,
                            "effective_protection_quantity_mismatch",
                        )
                        triggered_orders.add(oid)
                candidates.append((oid, total, created, category))
            if entry.status in TERMINAL:
                require(entry.order_id in seen, "entry_history_missing")
        require(len(candidates) <= 100, "exit_history_budget_exhausted")
        for oid, total, created, category in candidates:
            with stage("exit_lookup", FILLS):
                native_fills = history_rows(
                    provider._client,
                    endpoint=FILLS,
                    cursor_field="tradeId",
                    params={"instId": plan.execution_instrument, "orderId": oid},
                )
            with stage("exit_parse", FILLS):
                per_order: list[dict[str, Any]] = []
                for f in native_fills:
                    require(
                        f.get("orderId") == oid
                        and f.get("instId") == plan.execution_instrument
                        and f.get("side") == opposite
                        and f.get("positionSide") == "net",
                        "exit_fill_identity_mismatch",
                    )
                    fee_currency = f.get("feeCurrency")
                    require(
                        fee_currency in (None, "")
                        or (isinstance(fee_currency, str) and 0 < len(fee_currency) <= 128),
                        "exit_fee_currency_invalid",
                    )
                    tid = identity(f.get("tradeId"))
                    ts = f.get("ts", f.get("fillTime"))
                    require(
                        f.get("ts") is None
                        or f.get("fillTime") is None
                        or f["ts"] == f["fillTime"],
                        "exit_fill_timestamp_conflict",
                    )
                    at = occurrence(ts, earliest=created, now=provider._clock())
                    per_order.append(
                        {
                            "identity": f"{oid}:{tid}",
                            "order_id": oid,
                            "trade_id": tid,
                            "quantity": decimal_text(
                                number(f.get("fillSize"), field="fillSize", positive=True)
                            ),
                            "price": decimal_text(
                                number(f.get("fillPrice"), field="fillPrice", positive=True)
                            ),
                            "occurred_at": at.isoformat(),
                            "fee": decimal_text(number(f.get("fee"), field="fee"))
                            if f.get("fee") not in (None, "")
                            else None,
                            "fee_currency": fee_currency or None,
                            "fillPnl": decimal_text(number(f["fillPnl"], field="fillPnl"))
                            if f.get("fillPnl") not in {None, ""}
                            else None,
                            "category": category,
                        }
                    )
                require(
                    sum((Decimal(f["quantity"]) for f in per_order), Decimal("0")) == total,
                    "exit_fill_totals_incomplete",
                )
                require(
                    sum((Decimal(f["quantity"]) for f in exits + per_order), Decimal("0"))
                    <= quantity,
                    "exit_quantity_exceeds_entry",
                )
                exits.extend(per_order)
                if oid in triggered_orders:
                    triggered = "verified"
        with stage("exit_parse", FILLS):
            require(
                len({f["identity"] for f in exits}) == len(exits),
                "exit_fill_identity_duplicate",
            )
            total = sum((Decimal(f["quantity"]) for f in exits), Decimal("0"))
            require(total <= quantity, "exit_quantity_exceeds_entry")
        with stage("exit_parse", TPSL_HISTORY):
            require(not closure_conflict, "protection_history_trigger_conflict")
        # Recheck account after history IO before any release decision.
        account_verified = False
        flat = observe_positions(provider)
        account_verified = True
        idle = observe_account_idle(provider)
        closed = total == quantity and flat and idle and entry.status in TERMINAL
        return ManualLifecycleObservation(
            provider._clock(),
            "closed_verified"
            if closed
            else "flat_exit_unverified"
            if flat
            else "account_position_present",
            tuple(protect_facts),
            tuple(sorted(exits, key=lambda f: (f["occurred_at"], f["identity"]))),
            flat,
            idle,
            "verified_exit" if closed else None,
            (
                "Verified isolated entry/exit sequence and flat, idle account "
                "support scoped resolution."
            )
            if closed
            else (
                "Entry/exit or current account evidence is incomplete. Flatness "
                "and absent pending protection do not prove closure; inspect "
                "native history."
            ),
            protection_diagnostics=tuple(protection_diagnostics),
            triggered_protection=triggered,
            account_verified=True,
        )
    except Exception as exc:
        return ManualLifecycleObservation(
            provider._clock(),
            "flat_exit_unverified"
            if account_verified and flat
            else "account_position_present"
            if account_verified
            else "unknown",
            protection_history=tuple(protect_facts),
            exit_fills=tuple(exits),
            account_flat=flat,
            account_idle=idle,
            account_verified=account_verified,
            protection_diagnostics=tuple(protection_diagnostics),
            triggered_protection=triggered,
            diagnostics=(diagnostic_for(exc, stage="exit_lookup", endpoint=HISTORY),),
            recovery_reason=(
                "Lifecycle evidence read failed. Inspect the safe diagnostic and "
                "refresh this exact command; its claim remains held."
            ),
        )
