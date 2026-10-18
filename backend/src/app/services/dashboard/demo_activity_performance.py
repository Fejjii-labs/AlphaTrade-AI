"""Attach demo-account win rate from activity-worker history.

Numbers stay empty when the activity worker is off. Manual AlphaTrade demo
counts are not a substitute for the whole demo account.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.blofin_activity import BloFinActivityAccount, BloFinActivityCursor, BloFinActivityFact
from app.schemas.blofin_activity import NativeActivityFact
from app.schemas.dashboard_demo_account import DashboardDemoAccount
from app.security.tenant import TenantContext
from app.services.blofin_activity_config import BloFinActivitySettings, get_activity_settings
from app.services.dashboard.activity_round_trips import (
    METHOD,
    ActivityFill,
    ActivityOrder,
    account_round_trips,
)

_ROW_LIMIT = 5000
_DISABLED = (
    "Activity sync is disabled. Win rate and realized P&L for the whole demo "
    "account are withheld. Manual AlphaTrade trades are not a substitute."
)


def present_demo_account(
    account: DashboardDemoAccount,
    *,
    session: Session,
    settings: Settings,
    tenant: TenantContext,
) -> DashboardDemoAccount:
    """Stamp the configured refresh interval and activity performance."""
    account.refresh_interval_seconds = settings.blofin_demo_snapshot_sync_seconds
    apply_activity_performance(account, session=session, tenant=tenant)
    return account


def apply_activity_performance(
    account: DashboardDemoAccount,
    *,
    session: Session,
    tenant: TenantContext,
    config: BloFinActivitySettings | None = None,
) -> None:
    """Fill activity fields. Does not change manual AlphaTrade coverage."""
    settings = config if config is not None else get_activity_settings()
    performance = account.performance
    performance.pnl_method = METHOD
    if not settings.enabled:
        performance.activity_sync_status = "disabled"
        performance.activity_coverage = _DISABLED
        performance.win_rate = None
        performance.trade_count = None
        performance.wins = None
        performance.losses = None
        performance.realized_pnl = None
        performance.window_start = None
        performance.window_end = None
        performance.unresolved_round_trips = None
        return
    if settings.organization_id is None or not settings.expected_uid:
        _unavailable(account, "Activity sync is enabled without an organization and UID pin.")
        return
    if settings.organization_id != tenant.organization_id:
        _unavailable(account, "Activity sync is pinned to a different organization.")
        return
    uid = settings.expected_uid
    proof = session.get(BloFinActivityAccount, (tenant.organization_id, "demo", uid))
    if proof is None or proof.identity_verified_at is None or proof.identity_error:
        _unavailable(account, "Activity sync has not verified this demo account.")
        return
    cursor = session.get(BloFinActivityCursor, (tenant.organization_id, "demo", uid, "fill"))
    if cursor is None or cursor.last_successful_sync is None:
        _unavailable(account, "Activity sync has not stored fill history yet.")
        return
    rows = list(
        session.scalars(
            select(BloFinActivityFact)
            .where(
                BloFinActivityFact.organization_id == tenant.organization_id,
                BloFinActivityFact.environment == "demo",
                BloFinActivityFact.account_uid == uid,
            )
            .order_by(BloFinActivityFact.occurred_at_ms.desc(), BloFinActivityFact.native_id.desc())
            .limit(_ROW_LIMIT + 1)
        )
    )
    truncated = len(rows) > _ROW_LIMIT
    fills: list[ActivityFill] = []
    orders: list[ActivityOrder] = []
    dropped = False
    for row in rows[:_ROW_LIMIT]:
        parsed = _fact(row)
        if parsed is None:
            dropped = True
            continue
        kind, fill, order = parsed
        if kind == "fill" and fill is not None:
            fills.append(fill)
        elif order is not None:
            orders.append(order)
    report = account_round_trips(fills, orders)
    window_start = _millis(cursor.covered_begin_ms) or report.window_start
    window_end = _millis(cursor.covered_end_ms) or report.window_end
    partial = (
        truncated
        or dropped
        or cursor.gap_detected
        or report.missing_orders
        or report.order_mismatches > 0
        or report.unresolved > 0
        or not cursor.window_complete
    )
    performance.activity_sync_status = "partial" if partial else "ready"
    performance.trade_count = report.trade_count
    performance.wins = report.wins
    performance.losses = report.losses
    performance.win_rate = report.win_rate
    if report.trade_count:
        performance.realized_pnl = report.realized_pnl
    elif partial:
        performance.realized_pnl = None
    else:
        performance.realized_pnl = Decimal(0)
    performance.window_start = window_start
    performance.window_end = window_end
    performance.unresolved_round_trips = report.unresolved
    performance.activity_coverage = _coverage(
        partial=partial,
        gap=cursor.gap_detected,
        truncated=truncated,
        missing_orders=report.missing_orders,
        unresolved=report.unresolved,
        mismatches=report.order_mismatches,
    )


def _unavailable(account: DashboardDemoAccount, message: str) -> None:
    performance = account.performance
    performance.activity_sync_status = "unavailable"
    performance.activity_coverage = message
    performance.win_rate = None
    performance.trade_count = None
    performance.wins = None
    performance.losses = None
    performance.realized_pnl = None
    performance.window_start = None
    performance.window_end = None
    performance.unresolved_round_trips = None


def _coverage(
    *,
    partial: bool,
    gap: bool,
    truncated: bool,
    missing_orders: bool,
    unresolved: int,
    mismatches: int,
) -> str:
    if not partial:
        return (
            "Closed round-trips from stored BloFin demo fills. "
            "Realized P&L is native fillPnl plus the original signed fee "
            "when the fee currency matches the stored settlement currency. "
            "Open positions are excluded."
        )
    reasons: list[str] = []
    if gap:
        reasons.append("the fill window has a gap")
    if truncated:
        reasons.append(f"only the latest {_ROW_LIMIT} activity rows were used")
    if missing_orders:
        reasons.append("some fills have no stored order")
    if mismatches:
        reasons.append("an order filled size disagrees with its fills")
    if unresolved:
        reasons.append(f"{unresolved} closed round-trips were excluded for incomplete fees or size")
    detail = "; ".join(reasons) if reasons else "coverage is incomplete"
    detail = detail[0].upper() + detail[1:]
    return (
        "Partial activity history. Win rate uses only closed round-trips with "
        f"complete fillPnl and signed fees. {detail}."
    )


def _millis(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _fact(
    row: BloFinActivityFact,
) -> tuple[str, ActivityFill | None, ActivityOrder | None] | None:
    try:
        fact = NativeActivityFact.model_validate(row.payload)
    except ValueError:
        return None
    settlement = None
    if isinstance(row.instrument_metadata, dict):
        raw = row.instrument_metadata.get("settlement_currency")
        if isinstance(raw, str) and raw:
            settlement = raw
    if fact.kind == "order":
        if fact.filled_quantity is None:
            return None
        try:
            filled = Decimal(fact.filled_quantity)
        except InvalidOperation:
            return None
        return "order", None, ActivityOrder(fact.order_id, filled)
    try:
        quantity = Decimal(fact.quantity)
        fee = Decimal(fact.fee) if fact.fee is not None else None
        pnl = Decimal(fact.realized_pnl) if fact.realized_pnl is not None else None
    except InvalidOperation:
        return None
    if fact.side not in {"buy", "sell"} or fact.position_side not in {"net", "long", "short"}:
        return None
    side = cast(Literal["buy", "sell"], fact.side)
    position_side = cast(Literal["net", "long", "short"], fact.position_side)
    return (
        "fill",
        ActivityFill(
            native_id=fact.native_id,
            order_id=fact.order_id,
            instrument=fact.instrument,
            side=side,
            position_side=position_side,
            quantity=quantity,
            fee=fee,
            fee_currency=fact.fee_currency,
            realized_pnl=pnl,
            occurred_at_ms=int(fact.occurred_at_ms),
            settlement_currency=settlement,
        ),
        None,
    )
