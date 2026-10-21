"""Structural paper plan for a confirmed SFP. Detection thresholds stay untouched."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.market_contracts.enums import Finality
from app.market_contracts.ohlcv import OhlcvBar
from app.schemas.common import TradeDirection
from app.strategy_brain.sfp.contracts import SfpTradePlan, StructuralLevel
from app.strategy_brain.sfp.levels import level_evidence_fresh

# Same Wilder length the rest of the book uses. Not a detection threshold.
SFP_STOP_ATR_PERIOD = 14


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


def wilder_atr(bars: Sequence[OhlcvBar], *, period: int = SFP_STOP_ATR_PERIOD) -> Decimal | None:
    """Causal Wilder ATR from closed bars only. None until the period is full."""

    closed = tuple(bar for bar in bars if bar.finality is Finality.FINAL and bar.provider_complete)
    if period < 1 or len(closed) < period:
        return None
    true_ranges: list[Decimal] = []
    previous: Decimal | None = None
    for bar in closed:
        span = bar.high - bar.low
        if previous is None:
            true_ranges.append(span)
        else:
            true_ranges.append(max(span, abs(bar.high - previous), abs(bar.low - previous)))
        previous = bar.close
    value = sum(true_ranges[:period], start=Decimal("0")) / Decimal(period)
    for true_range in true_ranges[period:]:
        value = (value * Decimal(period - 1) + true_range) / Decimal(period)
    return value


def stop_offset(
    *,
    reference_price: Decimal,
    stop_buffer: Decimal,
    tick_size: Decimal | None,
    min_stop_ticks: int,
    atr: Decimal | None,
    min_stop_atr_multiple: Decimal,
) -> Decimal | None:
    """Largest distance beyond the sweep: percent buffer, N ticks, or k*ATR.

    The percent buffer stays a floor. Ticks apply only when a tick size is
    known. A configured ATR multiple with no ATR refuses the plan (None)
    instead of publishing a tighter stop than the operator asked for.
    """

    if reference_price <= 0 or stop_buffer < 0 or min_stop_ticks < 0 or min_stop_atr_multiple < 0:
        return None
    if min_stop_atr_multiple > 0 and (atr is None or atr <= 0):
        return None
    distance = reference_price * stop_buffer
    if tick_size is not None and tick_size > 0 and min_stop_ticks > 0:
        distance = max(distance, tick_size * min_stop_ticks)
    if atr is not None and atr > 0 and min_stop_atr_multiple > 0:
        distance = max(distance, atr * min_stop_atr_multiple)
    return distance


def derive_sfp_trade_plan(
    *,
    direction: TradeDirection,
    entry: Decimal,
    sweep_extreme: Decimal,
    reference_price: Decimal,
    stop_buffer: Decimal,
    opposing_levels: tuple[StructuralLevel, ...],
    tick_size: Decimal | None = None,
    min_stop_ticks: int = 0,
    atr: Decimal | None = None,
    min_stop_atr_multiple: Decimal = Decimal("0"),
) -> SfpTradePlan | None:
    """Build a paper plan, or return None when the geometry cannot be traded.

    Entry is the confirmation close. The stop sits beyond the sweep extreme by
    the larger of the percent buffer, N ticks, and k*ATR. Take-profit is the
    nearest known opposing structure. A stop or target on the wrong side of
    entry, or at zero distance, is refused instead of clamped.
    """

    if entry <= 0 or sweep_extreme <= 0 or reference_price <= 0 or stop_buffer < 0:
        return None
    support = direction is TradeDirection.LONG
    opposing = nearest_opposing_level(opposing_levels, support=support, price=entry)
    if opposing is None:
        return None
    buffer_distance = stop_offset(
        reference_price=reference_price,
        stop_buffer=stop_buffer,
        tick_size=tick_size,
        min_stop_ticks=min_stop_ticks,
        atr=atr,
        min_stop_atr_multiple=min_stop_atr_multiple,
    )
    if buffer_distance is None:
        return None
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
