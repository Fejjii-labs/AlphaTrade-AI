"""Isolated-margin liquidation buffer.

The required distance is the larger of twice the stop distance and the stop
distance plus 1.5 ATR. Maintenance tiers pick the rate. A missing tier, a
non-isolated mode, or a buffer that the liquidation price does not clear
refuses the trade. The post-fill recheck returns close-or-reduce and does
not call an exchange.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from app.services.risk_policy.errors import RiskPolicyRejected

_ATR_BUFFER = Decimal("1.5")
_STOP_MULTIPLE = Decimal("2")


@dataclass(frozen=True)
class MaintenanceTier:
    """Inclusive notional floor and the maintenance rate that applies above it."""

    notional_floor: Decimal
    maintenance_rate: Decimal


@dataclass(frozen=True)
class LiquidationCheck:
    accepted: bool
    reason: str | None
    liquidation_distance: Decimal | None
    required_buffer: Decimal | None
    maintenance_rate: Decimal | None


@dataclass(frozen=True)
class LiquidationRecheck:
    """Post-fill result. ``exchange_write_attempted`` stays false in this package."""

    action: str
    accepted: bool
    reason: str | None
    liquidation_distance: Decimal | None
    required_buffer: Decimal | None
    exchange_write_attempted: bool = False


def required_buffer(stop_distance: Decimal, atr: Decimal) -> Decimal:
    """max(2 * stop distance, stop distance + 1.5 * ATR)."""

    if stop_distance <= 0 or atr <= 0:
        raise RiskPolicyRejected("liquidation_inputs_invalid")
    return max(stop_distance * _STOP_MULTIPLE, stop_distance + _ATR_BUFFER * atr)


def select_maintenance_tier(
    notional: Decimal, tiers: tuple[MaintenanceTier, ...] | list[MaintenanceTier] | None
) -> MaintenanceTier:
    if not tiers:
        raise RiskPolicyRejected("maintenance_tiers_unavailable")
    if notional <= 0:
        raise RiskPolicyRejected("notional_invalid")
    chosen: MaintenanceTier | None = None
    for tier in sorted(tiers, key=lambda item: item.notional_floor):
        if not isinstance(tier.maintenance_rate, Decimal) or not tier.maintenance_rate.is_finite():
            raise RiskPolicyRejected("maintenance_rate_invalid")
        if tier.maintenance_rate < 0 or tier.maintenance_rate >= 1:
            raise RiskPolicyRejected("maintenance_rate_invalid")
        if notional >= tier.notional_floor:
            chosen = tier
    if chosen is None:
        raise RiskPolicyRejected("maintenance_tier_unmatched")
    return chosen


def liquidation_distance(
    *,
    entry: Decimal,
    leverage: Decimal,
    maintenance_rate: Decimal,
) -> Decimal:
    """Adverse price distance to isolated liquidation for a linear contract.

    ``entry * (1/leverage - maintenance_rate)``. A non-positive distance means
    the position would already be inside maintenance, which is a refusal.
    """

    if entry <= 0 or leverage < 1:
        raise RiskPolicyRejected("liquidation_inputs_invalid")
    initial = Decimal("1") / leverage
    distance = entry * (initial - maintenance_rate)
    if distance <= 0:
        raise RiskPolicyRejected("liquidation_distance_non_positive")
    return distance


def liquidation_crosses_stop(
    *, direction: str, liquidation_price: Decimal, stop: Decimal
) -> bool:
    """True when the venue or formula liquidation price is through the stop.

    Long: liquidation at or above the stop. Short: at or below the stop.
    """

    if direction == "long":
        return liquidation_price >= stop
    if direction == "short":
        return liquidation_price <= stop
    raise RiskPolicyRejected("direction_missing")


def isolated_liquidation_price(
    *,
    entry: Decimal,
    leverage: Decimal,
    maintenance_rate: Decimal,
    direction: str,
) -> Decimal:
    """Isolated liquidation price from the maintenance formula. No venue call."""

    distance = liquidation_distance(
        entry=entry, leverage=leverage, maintenance_rate=maintenance_rate
    )
    if direction == "long":
        return entry - distance
    if direction == "short":
        return entry + distance
    raise RiskPolicyRejected("direction_missing")


def leverage_clearing_buffer(
    *,
    entry: Decimal,
    buffer: Decimal,
    maintenance_rate: Decimal,
    requested: Decimal,
) -> Decimal | None:
    """Highest whole-number leverage at or below ``requested`` that clears the buffer.

    Returns None when even 1x does not put liquidation beyond the buffer.
    Leverage is not raised above the requested value.
    """

    if entry <= 0 or buffer <= 0 or requested < 1:
        return None
    if maintenance_rate < 0 or maintenance_rate >= 1:
        return None
    denominator = buffer / entry + maintenance_rate
    if denominator <= 0:
        return requested
    maximum = (Decimal("1") / denominator).to_integral_value(rounding=ROUND_DOWN)
    if maximum < 1:
        return None
    if maximum >= requested:
        return requested
    return maximum


def check_liquidation_buffer(
    *,
    entry: Decimal,
    stop_distance: Decimal,
    atr: Decimal | None,
    leverage: Decimal,
    notional: Decimal,
    margin_mode: str | None,
    tiers: tuple[MaintenanceTier, ...] | list[MaintenanceTier] | None,
) -> LiquidationCheck:
    """Refuse unless isolated margin clears the buffer."""

    if margin_mode is None or margin_mode.lower() != "isolated":
        return LiquidationCheck(False, "margin_mode_not_isolated", None, None, None)
    if atr is None or atr <= 0:
        return LiquidationCheck(False, "atr_missing", None, None, None)
    try:
        buffer = required_buffer(stop_distance, atr)
        tier = select_maintenance_tier(notional, tiers)
        distance = liquidation_distance(
            entry=entry, leverage=leverage, maintenance_rate=tier.maintenance_rate
        )
    except RiskPolicyRejected as exc:
        return LiquidationCheck(False, exc.code, None, None, None)
    if distance < buffer:
        return LiquidationCheck(
            False, "liquidation_inside_buffer", distance, buffer, tier.maintenance_rate
        )
    return LiquidationCheck(True, None, distance, buffer, tier.maintenance_rate)


def recheck_after_fill(
    *,
    fill_price: Decimal,
    stop_distance: Decimal,
    atr: Decimal | None,
    leverage: Decimal,
    notional: Decimal,
    margin_mode: str | None,
    tiers: tuple[MaintenanceTier, ...] | list[MaintenanceTier] | None,
) -> LiquidationRecheck:
    """Re-run the buffer on the fill. Does not set leverage or margin mode."""

    outcome = check_liquidation_buffer(
        entry=fill_price,
        stop_distance=stop_distance,
        atr=atr,
        leverage=leverage,
        notional=notional,
        margin_mode=margin_mode,
        tiers=tiers,
    )
    action = "hold" if outcome.accepted else "close_or_reduce"
    return LiquidationRecheck(
        action=action,
        accepted=outcome.accepted,
        reason=outcome.reason,
        liquidation_distance=outcome.liquidation_distance,
        required_buffer=outcome.required_buffer,
        exchange_write_attempted=False,
    )
