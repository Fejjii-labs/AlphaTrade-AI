"""Structural paper plan for a confirmed SFP. Detection thresholds stay untouched."""

from __future__ import annotations

from decimal import Decimal

from app.schemas.common import TradeDirection
from app.strategy_brain.sfp.contracts import SfpTradePlan, StructuralLevel
from app.strategy_brain.sfp.levels import level_evidence_fresh


def nearest_opposing_level(
    levels: tuple[StructuralLevel, ...],
    *,
    support: bool,
    price: Decimal,
) -> StructuralLevel | None:
    """Nearest fresh structure on the other side of ``price``.

    This is the same selection the quality distance already measures. The
    distance stays a quality component; this price is the trade target.
    """

    opposing = [
        level
        for level in levels
        if level.kind.support != support
        and level_evidence_fresh(level)
        and (level.price > price if support else level.price < price)
    ]
    if not opposing:
        return None
    return min(opposing, key=lambda level: abs(level.price - price))


def derive_sfp_trade_plan(
    *,
    direction: TradeDirection,
    entry: Decimal,
    sweep_extreme: Decimal,
    reference_price: Decimal,
    stop_buffer: Decimal,
    opposing_levels: tuple[StructuralLevel, ...],
) -> SfpTradePlan | None:
    """Build a paper plan, or return None when the geometry cannot be traded.

    Entry is the confirmation close. The stop sits beyond the sweep extreme by
    ``stop_buffer`` times the swept reference price. Take-profit is the nearest
    known opposing structure. A stop or target on the wrong side of entry, or
    at zero distance, is refused instead of clamped.
    """

    if entry <= 0 or sweep_extreme <= 0 or reference_price <= 0 or stop_buffer < 0:
        return None
    support = direction is TradeDirection.LONG
    opposing = nearest_opposing_level(opposing_levels, support=support, price=entry)
    if opposing is None:
        return None
    buffer_distance = reference_price * stop_buffer
    stop = sweep_extreme - buffer_distance if support else sweep_extreme + buffer_distance
    target = opposing.price
    if stop <= 0 or target <= 0:
        return None
    # Strict inequality refuses a wrong-side level and a zero-distance level.
    tradable = stop < entry < target if support else target < entry < stop
    if not tradable:
        return None
    return SfpTradePlan(
        entry=entry,
        stop=stop,
        take_profit=target,
        stop_buffer=stop_buffer,
    )
