"""Would-be plan arithmetic only. No orders, fills, stop moves or partial exits."""

from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import TypedDict

from app.market_contracts.identity import interval_timedelta
from app.schemas.common import Timeframe
from app.strategy_brain.dline.contracts import DlineSpec, ResearchTarget, SourcedLevel


def round_price(price: Decimal, tick: Decimal, *, up: bool) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * tick


class ResearchPlan(TypedDict):
    stop: Decimal
    targets: tuple[ResearchTarget, ...]
    runner_fraction: Decimal
    gross_rr: Decimal
    net_rr: Decimal
    weighted_scheduled_r: Decimal


def build_plan(
    *,
    entry: Decimal,
    stop_anchor: Decimal,
    pattern_extreme: Decimal,
    trend_start: Decimal,
    atr: Decimal,
    tick: Decimal,
    spec: DlineSpec,
    at: datetime,
    levels: tuple[SourcedLevel, ...],
    ranging: bool,
) -> ResearchPlan | None:
    p = spec.parameters
    sign = Decimal(1 if spec.direction.value == "long" else -1)
    stop = round_price(
        stop_anchor - sign * max(p.stop_buffer_atr * atr, p.minimum_stop_ticks * tick),
        tick,
        up=sign < 0,
    )
    risk = (entry - stop) * sign
    if risk <= 0:
        return None
    fib1 = pattern_extreme + Decimal(".618") * (trend_start - pattern_extreme)
    fib2 = trend_start
    rr2 = (fib2 - entry) * sign / risk
    if rr2 < p.minimum_rr:
        return None
    usable = [
        x
        for x in levels
        if x.symbol == spec.symbol
        and not x.consumed
        and x.known_at <= at < x.expires_at
        and (x.price - entry) * sign > 0
    ]
    cap_levels = [
        x
        for x in usable
        if x.kind == "ob" and interval_timedelta(x.timeframe) >= interval_timedelta(Timeframe.M15)
    ]
    cap = (
        min(cap_levels, key=lambda x: (x.price - entry) * sign).price - sign * tick
        if cap_levels
        and spec.trigger_timeframe is Timeframe.M5
        and p.five_minute_ob_policy == "cap"
        else None
    )
    values = [(fib1, "fib:0.618"), (fib2, "fib:1.0")]
    values.extend(
        (
            x.price - sign * tick if x.kind == "ob" else x.price,
            f"{x.kind}:{x.source}:{x.evidence_ref}",
        )
        for x in usable
    )
    clusters = {}
    for price, source in values:
        price = round_price(price, tick, up=sign < 0)
        if (price - entry) * sign <= 0 or (cap is not None and (price - cap) * sign > 0):
            continue
        clusters.setdefault(price, []).append(source)
    prices = sorted(clusters, key=lambda v: (v - entry) * sign)
    if len(prices) > 4:
        prices = [*prices[:3], prices[-1]]
    if not prices:
        return None
    if ranging:
        prices = prices[:1]
        if (prices[0] - entry) * sign / risk < p.ranging_first_target_min_rr:
            return None
        runner = Decimal(0)
        fractions = [Decimal(1)]
    else:
        if len(prices) < 2 or (prices[-1] - entry) * sign / risk < p.minimum_rr:
            return None
        runner = p.runner_fraction if usable and cap is None else Decimal(0)
        residual = 1 - p.tp1_fraction - runner
        fractions = [p.tp1_fraction] + [residual / Decimal(len(prices) - 1)] * (len(prices) - 1)
        fractions[-1] = 1 - runner - sum(fractions[:-1])
    targets = tuple(
        ResearchTarget(price=v, fraction=f, sources=tuple(clusters[v]))
        for v, f in zip(prices, fractions, strict=True)
    )
    gross = (prices[-1] - entry) * sign / risk
    return {
        "stop": stop,
        "targets": targets,
        "runner_fraction": runner,
        "gross_rr": gross,
        "net_rr": gross - entry * p.round_trip_cost_fraction / risk,
        "weighted_scheduled_r": sum((t.price - entry) * sign / risk * t.fraction for t in targets),
    }
