"""Causal strict pivots and pair-fitted lines. Equal plateaus do not form pivots."""

from decimal import Decimal
from itertools import combinations
from uuid import UUID, uuid5

from app.market_contracts.ohlcv import OhlcvBar
from app.schemas.common import TradeDirection
from app.strategy_brain.dline.contracts import DlineSpec, FrozenLine
from app.strategy_brain.sfp.trade_plan import wilder_atr

NAMESPACE = UUID("1ba10003-0000-4000-8000-000000000001")


def coordinates(
    bars: tuple[OhlcvBar, ...], spec: DlineSpec
) -> tuple[Decimal, list[Decimal], list[Decimal], list[Decimal]]:
    sign = Decimal(1 if spec.direction is TradeDirection.LONG else -1)
    return (
        sign,
        [b.high if sign == 1 else -b.low for b in bars],
        [b.low if sign == 1 else -b.high for b in bars],
        [b.close * sign for b in bars],
    )


def pivots(values: list[Decimal], width: int, *, high: bool = True) -> tuple[int, ...]:
    return tuple(
        i
        for i in range(width, len(values) - width)
        if all(
            (values[i] > values[j] if high else values[i] < values[j])
            for j in range(i - width, i + width + 1)
            if j != i
        )
    )


def select_line(bars: tuple[OhlcvBar, ...], spec: DlineSpec) -> FrozenLine | None:
    """Called with history ending one bar BEFORE the possible break."""
    p = spec.parameters
    sign, highs, _, closes = coordinates(bars, spec)
    points = pivots(highs, p.pivot_width)
    found = []
    for a, b in combinations(points, 2):
        if a < len(bars) - p.lookback or b - a < p.minimum_separation:
            continue
        slope = (highs[b] - highs[a]) / Decimal(b - a)
        if slope >= 0:
            continue
        atr = wilder_atr(bars[: b + p.pivot_width + 1], period=p.atr_period)
        if not atr or not p.slope_min_atr <= abs(slope) / atr <= p.slope_max_atr:
            continue

        def project(k: int, origin: int = a, gradient: Decimal = slope) -> Decimal:
            return highs[origin] + gradient * (k - origin)

        if any(closes[k] > project(k) for k in range(a + 1, len(bars))):
            continue
        touches = []
        residual = Decimal(0)
        for k in points:
            local_atr = wilder_atr(bars[: k + 1], period=p.atr_period)
            if k < a or (touches and k - touches[-1] < p.minimum_separation):
                continue
            error = abs(highs[k] - project(k))
            # The first anchor is exact even before ATR warmup.
            if error == 0 or (local_atr and error <= p.touch_atr * local_atr):
                touches.append(k)
                residual += error
        if len(touches) < p.minimum_touches:
            continue
        identity = uuid5(
            NAMESPACE,
            f"{spec.symbol}:{spec.trigger_timeframe}:{spec.direction}:"
            f"{bars[a].source_event_id}:{bars[b].source_event_id}",
        )
        line = FrozenLine(
            line_id=identity,
            anchor_indices=(a, b),
            anchor_event_ids=(bars[a].source_event_id, bars[b].source_event_id),
            touches=tuple(touches),
            slope=slope * sign,
            anchor_price=highs[a] * sign,
            atr=atr,
            normalized_slope=abs(slope) / atr,
            frozen_at=bars[-1].interval_end,
            break_price=project(len(bars)) * sign,
        )
        found.append(((-len(touches), -(touches[-1] - a), residual, a, b), line))
    return min(found, key=lambda item: item[0])[1] if found else None
