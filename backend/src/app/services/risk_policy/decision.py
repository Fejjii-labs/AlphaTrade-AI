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
    reduce_leverage: bool = True,
    replace_grade_cap: bool = False,
) -> TradeDecision:
    """Accept a new trade or return a refusal. Does not place an order.

    ``reduce_leverage`` is the library helper: it lowers leverage in whole
    numbers until the buffer clears, and it never raises leverage or quantity.
    The wired paper and demo path passes False and refuses instead. This
    function does not call an exchange.
    """

    if breaker.refuse_new_trades:
        return TradeDecision(False, "breaker_active", None, None)
    if grade.grade is None or (not grade.tradable and not replace_grade_cap):
        return TradeDecision(False, grade.refusal or "grade_not_tradable", None, None)
    explanation = ai_explanation
    if ai_suggested_grade is not None:
        bound, explanation = apply_ai_grade(grade.grade, ai_suggested_grade, ai_explanation or "")
        if bound is not grade.grade:
            return TradeDecision(False, "ai_changed_grade", None, None)
    if sizing.entry is None:
        return TradeDecision(False, "entry_missing", None, None)
    base_risk = (
        sizing.grade_risk_percent
        if sizing.grade_risk_percent is not None
        else profile.risk_percent(grade.grade)
    )
    try:
        adjusted = apply_adjusters(base_risk, adjusters)
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
                replace_grade_cap=replace_grade_cap,
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
        if not reduce_leverage:
            return TradeDecision(
                False, liquidation.reason or "liquidation_inside_buffer", None, None
            )
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
    direction: str | None = None,
    stop_price: Decimal | None = None,
    observed_liquidation_price: Decimal | None = None,
) -> LiquidationRecheck:
    """Post-fill check. The exchange writer is not called.

    When a liquidation price and a stop are present, the position is
    ``close_or_reduce`` if that price is through the stop (long: liquidation
    at or above the stop; short: at or below). A missing ATR does not turn an
    otherwise clear price into a close.
    """

    from app.services.risk_policy.liquidation import liquidation_crosses_stop

    breach = None
    if (
        direction in {"long", "short"}
        and stop_price is not None
        and observed_liquidation_price is not None
        and liquidation_crosses_stop(
            direction=direction,
            liquidation_price=observed_liquidation_price,
            stop=stop_price,
        )
    ):
        breach = LiquidationRecheck(
            action="close_or_reduce",
            accepted=False,
            reason="liquidation_through_stop",
            liquidation_distance=None,
            required_buffer=None,
            exchange_write_attempted=False,
        )
    if atr is None and observed_liquidation_price is not None:
        if breach is not None:
            return breach
        return LiquidationRecheck(
            action="hold",
            accepted=True,
            reason=None,
            liquidation_distance=None,
            required_buffer=None,
            exchange_write_attempted=False,
        )
    outcome = recheck_after_fill(
        fill_price=fill_price,
        stop_distance=stop_distance,
        atr=atr,
        leverage=leverage,
        notional=notional,
        margin_mode=margin_mode,
        tiers=tiers,
    )
    if breach is not None:
        return breach
    return outcome
