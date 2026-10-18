"""Compose grade, adjusters, sizer, liquidation, and breakers for one new trade."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.services.risk_policy.adjusters import AdjusterInputs, apply_adjusters
from app.services.risk_policy.breakers import BreakerVerdict
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.grader import GradeDecision, apply_ai_grade
from app.services.risk_policy.liquidation import (
    LiquidationRecheck,
    MaintenanceTier,
    check_liquidation_buffer,
    recheck_after_fill,
)
from app.services.risk_policy.profile import Grade, RiskPolicyProfile
from app.services.risk_policy.records import TradeRiskRecord, build_trade_record
from app.services.risk_policy.sizer import SizingInput, SizingResult, size_trade


@dataclass(frozen=True)
class TradeDecision:
    accepted: bool
    reason: str | None
    record: TradeRiskRecord | None
    sizing: SizingResult | None


def decide_trade(
    *,
    profile: RiskPolicyProfile,
    profile_version_id: UUID,
    grade: GradeDecision,
    adjusters: AdjusterInputs,
    sizing: SizingInput,
    breaker: BreakerVerdict,
    tiers: tuple[MaintenanceTier, ...] | list[MaintenanceTier] | None,
    ai_suggested_grade: Grade | None = None,
    ai_explanation: str | None = None,
) -> TradeDecision:
    """Accept a new trade or return a refusal. Does not place an order."""

    if breaker.refuse_new_trades:
        return TradeDecision(False, "breaker_active", None, None)
    if not grade.tradable or grade.grade is None:
        return TradeDecision(False, grade.refusal or "grade_not_tradable", None, None)
    explanation = ai_explanation
    if ai_suggested_grade is not None:
        bound, explanation = apply_ai_grade(grade.grade, ai_suggested_grade, ai_explanation or "")
        if bound is not grade.grade:
            return TradeDecision(False, "ai_changed_grade", None, None)
    if sizing.entry is None:
        return TradeDecision(False, "entry_missing", None, None)
    try:
        adjusted = apply_adjusters(profile.risk_percent(grade.grade), adjusters)
        sized = size_trade(
            profile,
            SizingInput(
                equity=sizing.equity,
                entry=sizing.entry,
                stop=sizing.stop,
                direction=sizing.direction,
                grade=grade.grade,
                grade_risk_percent=adjusted.adjusted_risk_percent,
                atr=sizing.atr,
                gap_beyond_stop=sizing.gap_beyond_stop,
                fee_rate=sizing.fee_rate,
                funding_rate=sizing.funding_rate,
                slippage_rate=sizing.slippage_rate,
                remaining_daily_budget_percent=sizing.remaining_daily_budget_percent,
                open_risk_percent=sizing.open_risk_percent,
                open_notional=sizing.open_notional,
                open_positions=sizing.open_positions,
                leverage=sizing.leverage,
                margin_mode=sizing.margin_mode,
            ),
        )
    except RiskPolicyRejected as exc:
        return TradeDecision(False, exc.code, None, None)
    liquidation = check_liquidation_buffer(
        entry=sizing.entry,
        stop_distance=sized.stop_distance,
        atr=sizing.atr,
        leverage=sized.leverage,
        notional=sized.notional,
        margin_mode=sizing.margin_mode,
        tiers=tiers,
    )
    if not liquidation.accepted:
        return TradeDecision(False, liquidation.reason, None, None)
    record = build_trade_record(
        profile_version_id=profile_version_id,
        decision=grade,
        adjusters=adjusted,
        sizing=sized,
        ai_explanation=explanation,
    )
    return TradeDecision(True, None, record, sized)


def post_fill_recheck(
    *,
    fill_price: Decimal,
    stop_distance: Decimal,
    atr: Decimal | None,
    leverage: Decimal,
    notional: Decimal,
    margin_mode: str | None,
    tiers: tuple[MaintenanceTier, ...] | list[MaintenanceTier] | None,
) -> LiquidationRecheck:
    """Post-fill buffer check. The exchange writer is not called."""

    return recheck_after_fill(
        fill_price=fill_price,
        stop_distance=stop_distance,
        atr=atr,
        leverage=leverage,
        notional=notional,
        margin_mode=margin_mode,
        tiers=tiers,
    )
