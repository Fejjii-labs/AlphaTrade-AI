"""Risk checks exclusive to the governed BloFin demo account."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import AccountRiskAccountingState, BotRiskSettings
from app.providers.exchange.demo_order_book import MAXIMUM_QUOTE_BPS
from app.schemas.trade_plan import TradePlanRevision
from app.services.risk_policy.equity_stop import _arm, evaluate_equity_stop
from app.services.risk_policy.plan_sizing import _GRADE_BY_CODE, PROFILE_SIZING_GRADE
from app.services.risk_policy.profile import Grade

if TYPE_CHECKING:
    from app.providers.exchange.governed_blofin import DemoVenueSnapshot


def valid_demo_equity(equity: Decimal | None) -> bool:
    return equity is not None and equity.is_finite() and equity > 0


def demo_grade_risk(config: BotRiskSettings, grade: Grade | None) -> Decimal | None:
    if config.grades_gate_entries and grade in {None, Grade.C}:
        return None
    try:
        risk = (
            Decimal(config.grade_risk_overrides.get(grade.value, str(config.risk_per_trade_pct)))
            if grade is not None
            else config.risk_per_trade_pct
        )
    except (AttributeError, TypeError, InvalidOperation):
        return None
    return risk if risk.is_finite() and 0 < risk <= config.daily_stop_pct else None


def demo_v2_claim_refusal(
    session: Session,
    accounting: AccountRiskAccountingState,
    plan: TradePlanRevision,
    equity: Decimal | None,
) -> str | None:
    if equity is None or not valid_demo_equity(equity):
        return "demo_equity_unverified"
    config = session.get(BotRiskSettings, 1, populate_existing=True, with_for_update=True)
    exits = plan.risk_and_exits
    if (
        config is None
        or exits.margin_assumption_version != "2"
        or exits.leverage != config.leverage
        or plan.margin_mode.value.lower() != config.margin_mode
    ):
        return "demo_risk_settings_changed"
    grade_inputs = [
        _GRADE_BY_CODE.get(item.input_value)
        for item in plan.calculation_inputs
        if item.name == PROFILE_SIZING_GRADE
    ]
    if len(grade_inputs) > 1:
        return "demo_risk_grade_invalid"
    percent = demo_grade_risk(config, grade_inputs[0] if grade_inputs else None)
    if percent is None:
        return "demo_risk_grade_blocked"
    risk = exits.risk_budget.value
    if risk > equity * percent / 100:
        return "demo_depth_exceeds_risk_limit"
    if accounting.actual_daily_loss + accounting.demo_reserved_daily_loss + risk > (
        equity * config.daily_stop_pct / 100
    ):
        return "insufficient_daily_loss_allocation"
    return None


def enforce_demo_equity_stop(
    session: Session,
    *,
    accounting: AccountRiskAccountingState,
    user_id: UUID,
    equity: Decimal,
    threshold: Decimal,
    now: datetime,
    settings: Settings,
) -> bool:
    verdict = evaluate_equity_stop(
        threshold_percent=threshold,
        grace=timedelta(seconds=settings.account_equity_stop_baseline_grace_seconds),
        baseline=accounting.demo_equity_baseline,
        baseline_day=accounting.demo_equity_baseline_date,
        first_sight=accounting.demo_equity_baseline_date is None,
        trip_at_threshold=True,
        equity=equity,
        now=now,
    )
    if verdict.new_baseline is not None:
        accounting.demo_equity_baseline = verdict.new_baseline
        accounting.demo_equity_baseline_date = verdict.day
    if not verdict.trip or accounting.demo_equity_stop_tripped_on == verdict.day:
        return False
    accounting.demo_equity_stop_tripped_on = verdict.day
    session.flush()
    _arm(
        session,
        organization_id=accounting.organization_id,
        user_id=user_id,
        account_id=accounting.account_id,
        verdict=verdict,
        threshold_percent=threshold,
        settings=settings,
    )
    return True


def demo_stop_sized_contracts(
    snapshot: DemoVenueSnapshot, *, stop: Decimal, risk: Decimal, allowance: Decimal
) -> Decimal:
    """Largest lot-aligned quantity whose worst entry fits the stop budget.

    Each level is taken at its price moved ``allowance`` (a rate) away from the
    stop, the worst fill the plan's widened entry zone authorizes.
    """
    depth = Decimal("0")
    quantity = Decimal("0")
    for price, contracts in snapshot.book.levels:
        if abs(price - snapshot.price) / snapshot.price * 10000 > MAXIMUM_QUOTE_BPS:
            break
        depth += contracts
        distance = abs(price - stop) + price * allowance
        affordable = min(depth, snapshot.maximum, risk / (distance * snapshot.multiplier))
        rounded = (affordable / snapshot.lot).to_integral_value(rounding=ROUND_FLOOR) * snapshot.lot
        if rounded <= quantity:
            break
        quantity = rounded
    return quantity


def demo_v2_venue_refusal(
    plan: TradePlanRevision, snapshot: DemoVenueSnapshot, worst_price: Decimal
) -> str | None:
    """Verify the executable book and margin again before the venue POST."""
    from app.services.demo_leverage_config import demo_liquidation_refusal
    from app.services.risk_policy.plan_sizing import PAPER_DEMO_TAKER_FEE_RATE

    exits = plan.risk_and_exits
    if not valid_demo_equity(snapshot.venue_equity):
        return "demo_equity_unverified"
    is_long = plan.side.value == "BUY"
    upper = max(snapshot.price, worst_price)
    adverse_entry = upper if is_long else min(snapshot.price, worst_price)
    base = plan.quantity.value * snapshot.multiplier
    stop = exits.stop.value
    distance = abs(adverse_entry - stop)
    if base * distance > exits.risk_budget.value:
        return "demo_depth_exceeds_risk_limit"
    if (upper + stop) * PAPER_DEMO_TAKER_FEE_RATE > distance / 5:
        return "demo_fees_exceed_risk_limit"
    if base * upper * (1 / exits.leverage + PAPER_DEMO_TAKER_FEE_RATE) > snapshot.available:
        return "demo_margin_exceeds_available"
    return demo_liquidation_refusal(
        entry=adverse_entry,
        stop=stop,
        direction="long" if is_long else "short",
        leverage=exits.leverage,
        notional=base * adverse_entry,
        maintenance_tiers=snapshot.maintenance_tiers,
    )
