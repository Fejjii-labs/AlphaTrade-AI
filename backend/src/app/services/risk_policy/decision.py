"""Compose grade, adjusters, sizer, liquidation, and breakers for one new trade."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID

from app.services.risk_policy.adjusters import AdjusterInputs, apply_adjusters
from app.services.risk_policy.breakers import BreakerVerdict
from app.services.risk_policy.calibration import PromotionReport
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.grader import GradeDecision, apply_ai_grade
from app.services.risk_policy.liquidation import (
    LiquidationRecheck,
    MaintenanceTier,
    check_liquidation_buffer,
    leverage_clearing_buffer,
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
    promotion: PromotionReport | None = None,
    preserve_grade_risk: bool = False,
) -> TradeDecision:
    """Accept a new trade or return a refusal. Does not place an order.

    When the requested leverage would put liquidation inside the buffer, leverage
    is lowered until the buffer clears or the trade is refused. Quantity is not
    increased. This function does not call an exchange.
    """

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
    except RiskPolicyRejected as exc:
        return TradeDecision(False, exc.code, None, None)
    requested = sizing.leverage if sizing.leverage is not None else profile.default_leverage
    attempt = replace(
        sizing,
        grade=grade.grade,
        grade_risk_percent=adjusted.adjusted_risk_percent,
        leverage=requested,
    )
    sized = None
    for _ in range(32):
        try:
            sized = size_trade(
                profile,
                attempt,
                promotion=promotion,
                preserve_grade_risk=preserve_grade_risk,
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
        if liquidation.accepted:
            break
        if (
            liquidation.reason != "liquidation_inside_buffer"
            or liquidation.required_buffer is None
            or liquidation.maintenance_rate is None
        ):
            return TradeDecision(False, liquidation.reason, None, None)
        lowered = leverage_clearing_buffer(
            entry=sizing.entry,
            buffer=liquidation.required_buffer,
            maintenance_rate=liquidation.maintenance_rate,
            requested=sized.leverage,
        )
        if lowered is None or lowered >= sized.leverage:
            return TradeDecision(False, "liquidation_inside_buffer", None, None)
        attempt = replace(attempt, leverage=lowered)
    else:
        return TradeDecision(False, "liquidation_inside_buffer", None, None)
    if sized is None or sizing.equity is None or sized.margin > sizing.equity:
        return TradeDecision(False, "margin_exceeds_equity", None, None)
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
