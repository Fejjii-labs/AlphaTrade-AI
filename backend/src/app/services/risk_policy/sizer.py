"""Position sizer.

Effective risk is the minimum of the grade risk (after adjusters), the profile
allowance, the code ceiling, and the daily budget left after open risk-at-stop.
Quantity comes from the adverse stop distance, with fees, funding, and
slippage inside R. Leverage sets isolated margin and does not change quantity.
Notional caps are independent of leverage. Out-of-bound inputs refuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from app.services.risk_policy.calibration import PromotionReport, assert_promotion_allows
from app.services.risk_policy.ceilings import (
    HARD_MAX_DAILY_LOSS_PERCENT,
    HARD_MAX_LEVERAGE,
    HARD_MAX_RISK_PER_TRADE_PERCENT,
)
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.profile import Grade, RiskPolicyProfile, validate_profile

_QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True)
class SizingInput:
    """None on a required field means the caller did not have the input."""

    equity: Decimal | None
    entry: Decimal | None
    stop: Decimal | None
    direction: str | None
    grade: Grade | None
    grade_risk_percent: Decimal | None
    atr: Decimal | None
    gap_beyond_stop: Decimal | None
    fee_rate: Decimal | None
    funding_rate: Decimal | None
    funding_periods: Decimal | None
    slippage_rate: Decimal | None
    lot_step: Decimal | None
    min_quantity: Decimal | None
    remaining_daily_budget_percent: Decimal | None
    open_risk_percent: Decimal | None
    open_notional: Decimal | None
    open_positions: int | None
    leverage: Decimal | None
    margin_mode: str | None


@dataclass(frozen=True)
class SizingResult:
    quantity: Decimal
    notional: Decimal
    margin: Decimal
    effective_risk_percent: Decimal
    risk_amount: Decimal
    cost_amount: Decimal
    cost_share_of_r: Decimal
    stop_distance: Decimal
    adverse_distance: Decimal
    notional_capped: bool
    grade_risk_percent: Decimal
    profile_risk_percent: Decimal
    ceiling_percent: Decimal
    budget_room_percent: Decimal
    leverage: Decimal

    def calculation(self) -> dict[str, str]:
        """The effective-risk calculation stored on the trade."""

        return {
            "grade_risk_percent": _fmt(self.grade_risk_percent),
            "profile_risk_percent": _fmt(self.profile_risk_percent),
            "ceiling_percent": _fmt(self.ceiling_percent),
            "budget_room_percent": _fmt(self.budget_room_percent),
            "effective_risk_percent": _fmt(self.effective_risk_percent),
            "risk_amount": _fmt(self.risk_amount),
            "quantity": _fmt(self.quantity),
            "notional": _fmt(self.notional),
            "margin": _fmt(self.margin),
            "leverage": _fmt(self.leverage),
            "stop_distance": _fmt(self.stop_distance),
            "adverse_distance": _fmt(self.adverse_distance),
            "cost_amount": _fmt(self.cost_amount),
            "cost_share_of_r": _fmt(self.cost_share_of_r),
            "notional_capped": "true" if self.notional_capped else "false",
            "formula": "min(grade, profile, ceiling, daily_room, aggregate_room)",
        }


def size_trade(
    profile: RiskPolicyProfile,
    data: SizingInput,
    *,
    promotion: PromotionReport | None = None,
) -> SizingResult:
    """Size one new trade. Leverage does not increase quantity."""

    validate_profile(profile)
    assert_promotion_allows(profile, promotion)
    equity = _required(data.equity, "equity_missing")
    entry = _required(data.entry, "entry_missing")
    stop = _required(data.stop, "stop_missing")
    atr = _required(data.atr, "atr_missing")
    gap = _required(data.gap_beyond_stop, "gap_missing")
    fee_rate = _required(data.fee_rate, "fee_rate_missing")
    funding_rate = _required(data.funding_rate, "funding_rate_missing")
    funding_periods = _required(data.funding_periods, "funding_periods_missing")
    slippage_rate = _required(data.slippage_rate, "slippage_rate_missing")
    lot_step = _required(data.lot_step, "lot_step_missing")
    min_quantity = _required(data.min_quantity, "min_quantity_missing")
    remaining_daily = _required(data.remaining_daily_budget_percent, "daily_budget_missing")
    open_risk = _required(data.open_risk_percent, "open_risk_missing")
    open_notional = _required(data.open_notional, "open_notional_missing")
    grade_risk = _required(data.grade_risk_percent, "grade_risk_missing")
    if data.open_positions is None:
        raise RiskPolicyRejected("open_positions_missing")
    if data.direction not in {"long", "short"}:
        raise RiskPolicyRejected("direction_missing")
    if data.grade is None or data.grade is Grade.C:
        raise RiskPolicyRejected("grade_not_tradable")
    if data.margin_mode is None:
        raise RiskPolicyRejected("margin_mode_missing")
    if data.margin_mode.lower() != "isolated":
        raise RiskPolicyRejected("margin_mode_not_isolated")
    _positive(equity, "equity_invalid")
    _positive(entry, "entry_invalid")
    _positive(atr, "atr_invalid")
    _non_negative(gap, "gap_invalid")
    _non_negative(fee_rate, "fee_rate_invalid")
    _non_negative(funding_periods, "funding_periods_invalid")
    _non_negative(slippage_rate, "slippage_rate_invalid")
    _positive(lot_step, "lot_step_invalid")
    _positive(min_quantity, "min_quantity_invalid")
    if stop <= 0:
        raise RiskPolicyRejected("stop_not_positive")
    _non_negative(open_risk, "open_risk_invalid")
    _non_negative(open_notional, "open_notional_invalid")
    _non_negative(remaining_daily, "daily_budget_invalid")
    _non_negative(grade_risk, "grade_risk_invalid")
    if not isinstance(funding_rate, Decimal) or not funding_rate.is_finite():
        raise RiskPolicyRejected("funding_rate_invalid")
    if (
        not isinstance(data.open_positions, int)
        or isinstance(data.open_positions, bool)
        or data.open_positions < 0
    ):
        raise RiskPolicyRejected("open_positions_invalid")
    if data.open_positions >= profile.max_open_positions:
        raise RiskPolicyRejected("max_open_positions")
    stop_distance = _stop_distance(data.direction, entry, stop)
    floor = entry * profile.min_stop_absolute_floor
    minimum = max(profile.min_stop_atr_multiple * atr, floor)
    if stop_distance < minimum:
        raise RiskPolicyRejected("stop_too_tight")
    leverage = data.leverage if data.leverage is not None else Decimal("1")
    if not isinstance(leverage, Decimal) or not leverage.is_finite():
        raise RiskPolicyRejected("leverage_invalid")
    if leverage < 1 or leverage > profile.max_leverage or leverage > HARD_MAX_LEVERAGE:
        raise RiskPolicyRejected("leverage_exceeds_ceiling")
    profile_risk = profile.risk_percent(data.grade)
    daily_cap = min(profile.daily_loss_limit_percent, HARD_MAX_DAILY_LOSS_PERCENT, remaining_daily)
    daily_room = daily_cap - open_risk
    aggregate_room = profile.max_aggregate_open_risk_percent - open_risk
    budget_room = min(daily_room, aggregate_room)
    if budget_room <= 0:
        raise RiskPolicyRejected("budget_exhausted")
    effective = min(grade_risk, profile_risk, HARD_MAX_RISK_PER_TRADE_PERCENT, budget_room)
    if effective <= 0:
        raise RiskPolicyRejected("budget_exhausted")
    adverse = stop_distance + gap
    unit_cost = _unit_cost(entry, stop, fee_rate, funding_rate, funding_periods, slippage_rate)
    share = _cost_share(adverse, unit_cost)
    if share > profile.max_cost_share_of_r:
        raise RiskPolicyRejected("costs_exceed_r")
    risk_amount = equity * effective / Decimal("100")
    denominator = adverse + unit_cost
    if denominator <= 0:
        raise RiskPolicyRejected("sizing_denominator_invalid")
    quantity = _round_lot(risk_amount / denominator, lot_step)
    notional_cap = equity * profile.max_notional_per_trade_percent / Decimal("100")
    aggregate_notional = equity * profile.max_aggregate_notional_percent / Decimal("100")
    notional_room = aggregate_notional - open_notional
    if notional_room <= 0 or notional_cap <= 0:
        raise RiskPolicyRejected("notional_cap")
    capped = False
    room = min(notional_cap, notional_room)
    notional = quantity * entry
    if notional > room:
        quantity = _round_lot(room / entry, lot_step)
        capped = True
    if quantity < min_quantity:
        raise RiskPolicyRejected("below_min_quantity")
    notional = (quantity * entry).quantize(_QUANTUM, rounding=ROUND_DOWN)
    cost_amount = (quantity * unit_cost).quantize(_QUANTUM, rounding=ROUND_DOWN)
    price_risk = (quantity * adverse).quantize(_QUANTUM, rounding=ROUND_DOWN)
    realized_r = price_risk + cost_amount
    if realized_r <= 0 or realized_r > risk_amount + _QUANTUM:
        raise RiskPolicyRejected("risk_amount_invalid")
    realized_share = cost_amount / realized_r if realized_r else Decimal("1")
    if realized_share > profile.max_cost_share_of_r:
        raise RiskPolicyRejected("costs_exceed_r")
    effective_recorded = (realized_r / equity * Decimal("100")).quantize(
        _QUANTUM, rounding=ROUND_DOWN
    )
    margin = (notional / leverage).quantize(_QUANTUM, rounding=ROUND_DOWN)
    return SizingResult(
        quantity=quantity,
        notional=notional,
        margin=margin,
        effective_risk_percent=effective_recorded,
        risk_amount=realized_r,
        cost_amount=cost_amount,
        cost_share_of_r=realized_share,
        stop_distance=stop_distance,
        adverse_distance=adverse,
        notional_capped=capped,
        grade_risk_percent=grade_risk,
        profile_risk_percent=profile_risk,
        ceiling_percent=HARD_MAX_RISK_PER_TRADE_PERCENT,
        budget_room_percent=budget_room,
        leverage=leverage,
    )


def size_concurrent(
    profile: RiskPolicyProfile,
    orders: tuple[SizingInput, ...] | list[SizingInput],
) -> tuple[SizingResult, ...]:
    """Size orders that would be open together.

    Each order is sized as if the others were not there. If each one fits but
    the sum of their risk or notional does not, the whole batch is refused.
    """

    if not orders:
        raise RiskPolicyRejected("concurrent_batch_empty")
    alone = tuple(size_trade(profile, order) for order in orders)
    open_positions = orders[0].open_positions
    if open_positions is None:
        raise RiskPolicyRejected("open_positions_missing")
    if open_positions + len(orders) > profile.max_open_positions:
        raise RiskPolicyRejected("max_open_positions")
    open_risk = orders[0].open_risk_percent or Decimal("0")
    open_notional = orders[0].open_notional or Decimal("0")
    equity = orders[0].equity
    if equity is None or equity <= 0:
        raise RiskPolicyRejected("equity_invalid")
    total_risk = open_risk + sum((item.effective_risk_percent for item in alone), Decimal("0"))
    if total_risk > profile.max_aggregate_open_risk_percent:
        raise RiskPolicyRejected("concurrent_budget_exhausted")
    remaining = orders[0].remaining_daily_budget_percent or Decimal("0")
    daily_cap = min(profile.daily_loss_limit_percent, HARD_MAX_DAILY_LOSS_PERCENT, remaining)
    if total_risk > daily_cap:
        raise RiskPolicyRejected("concurrent_budget_exhausted")
    total_notional = open_notional + sum((item.notional for item in alone), Decimal("0"))
    aggregate_cap = equity * profile.max_aggregate_notional_percent / Decimal("100")
    if total_notional > aggregate_cap:
        raise RiskPolicyRejected("concurrent_notional_cap")
    return alone


def _stop_distance(direction: str, entry: Decimal, stop: Decimal) -> Decimal:
    if direction == "long":
        if stop >= entry:
            raise RiskPolicyRejected("stop_on_wrong_side")
        return entry - stop
    if stop <= entry:
        raise RiskPolicyRejected("stop_on_wrong_side")
    return stop - entry


def _unit_cost(
    entry: Decimal,
    stop: Decimal,
    fee_rate: Decimal,
    funding_rate: Decimal,
    funding_periods: Decimal,
    slippage_rate: Decimal,
) -> Decimal:
    """Entry fee, exit fee at the stop, funding periods times the rate, and slippage."""

    return (
        entry * fee_rate
        + stop * fee_rate
        + entry * abs(funding_rate) * funding_periods
        + entry * slippage_rate
    )


def _cost_share(adverse: Decimal, unit_cost: Decimal) -> Decimal:
    if adverse <= 0:
        raise RiskPolicyRejected("adverse_distance_invalid")
    return unit_cost / (adverse + unit_cost) if unit_cost else Decimal("0")


def _round_lot(quantity: Decimal, lot_step: Decimal) -> Decimal:
    steps = (quantity / lot_step).to_integral_value(rounding=ROUND_DOWN)
    return steps * lot_step


def _required(value: Decimal | None, code: str) -> Decimal:
    if value is None:
        raise RiskPolicyRejected(code)
    if not isinstance(value, Decimal) or not value.is_finite():
        raise RiskPolicyRejected(code)
    return value


def _positive(value: Decimal, code: str) -> None:
    if value <= 0:
        raise RiskPolicyRejected(code)


def _non_negative(value: Decimal, code: str) -> None:
    if value < 0:
        raise RiskPolicyRejected(code)


def _fmt(value: Decimal) -> str:
    return format(value, "f")
