"""Causal confluences using canonical HTF candles and the existing Nested detector."""

from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from typing import TypedDict

from app.market_contracts.enums import Finality
from app.market_contracts.identity import InstrumentIdentity, interval_timedelta
from app.market_contracts.observation import PublicMarketObservation
from app.market_contracts.ohlcv import OhlcvBar
from app.schemas.common import Timeframe
from app.schemas.nested_continuation import BrainSetupState, NestedContinuationSpec
from app.strategy_brain.detector import NestedDetection, detect_nested
from app.strategy_brain.dline.contracts import DlineParameters, DlineSpec
from app.strategy_brain.dline.geometry import coordinates, pivots
from app.strategy_brain.sfp.contracts import available_at, validate_binding


class ParentLine(TypedDict):
    id: str
    held: bool
    proof: tuple[str, str]


def closed_evidence(
    bars: tuple[OhlcvBar, ...],
    observations: tuple[PublicMarketObservation, ...],
    *,
    at: datetime,
    symbol: str,
    timeframe: Timeframe,
    instrument: InstrumentIdentity | None = None,
) -> tuple[tuple[OhlcvBar, ...], tuple[PublicMarketObservation, ...]]:
    """Reject bad binding, gaps/revisions; future/forming rows cannot become known."""
    if len(bars) != len(observations):
        return (), ()
    accepted = []
    receipts = []
    try:
        for bar, obs in zip(bars, observations, strict=True):
            validate_binding(bar, obs)
            if (
                bar.instrument.provider_symbol != symbol
                or bar.timeframe != timeframe
                or (instrument is not None and bar.instrument != instrument)
            ):
                return (), ()
            if (
                bar.finality is not Finality.FINAL
                or not bar.provider_complete
                or bar.interval_end > at
                or available_at(obs) > at
            ):
                continue
            if accepted and (
                bar.interval_start != accepted[-1].interval_end
                or bar.instrument != accepted[0].instrument
            ):
                return (), ()
            accepted.append(bar)
            receipts.append(obs)
    except ValueError:
        return (), ()
    return tuple(accepted), tuple(receipts)


def htf_context(
    bars: tuple[OhlcvBar, ...],
    observations: tuple[PublicMarketObservation, ...],
    spec: DlineSpec,
    *,
    at: datetime,
    instrument: InstrumentIdentity,
    entry: Decimal,
) -> tuple[bool | None, ParentLine | None, tuple[str, ...]]:
    p = spec.parameters
    timeframe = {"5m": p.five_minute_htf, "15m": p.fifteen_minute_htf, "1h": p.hourly_htf}[
        spec.trigger_timeframe.value
    ]
    bars, obs = closed_evidence(
        bars, observations, at=at, symbol=spec.symbol, timeframe=timeframe, instrument=instrument
    )
    if (
        len(bars) < p.ema_period + p.ema_slope_bars
        or at - bars[-1].interval_end > interval_timedelta(timeframe) * p.htf_max_age_bars
    ):
        return None, None, ()
    sign = Decimal(1 if spec.direction.value == "long" else -1)
    values = [sum((b.close for b in bars[: p.ema_period]), Decimal(0)) / p.ema_period]
    alpha = Decimal(2) / (p.ema_period + 1)
    for b in bars[p.ema_period :]:
        values.append(b.close * alpha + values[-1] * (1 - alpha))
    aligned = (bars[-1].close - values[-1]) * sign > 0 and (
        values[-1] - values[-1 - p.ema_slope_bars]
    ) * sign > 0
    # Causal rising HTF support (short mirror), latest two strict confirmed pivots.
    support = [b.low if sign == 1 else -b.high for b in bars]
    points = pivots(support, p.pivot_width, high=False)
    parent = None
    if len(points) >= 2:
        a, b = points[-2:]
        slope = (support[b] - support[a]) / Decimal(b - a)
        if slope > 0:
            hold = all(
                bars[k].close * sign >= support[a] + slope * (k - a) for k in range(b, len(bars))
            )
            projection = support[a] + slope * (
                Decimal(len(bars) - 1)
                + Decimal(str((at - bars[-1].interval_end) / interval_timedelta(timeframe)))
                - a
            )
            hold = hold and entry * sign >= projection
            parent = {
                "id": f"{bars[a].source_event_id}:{bars[b].source_event_id}",
                "held": hold,
                "proof": (bars[a].content_hash, bars[b].content_hash),
            }
    return aligned, parent, tuple(o.content_hash for o in obs)


def nested_context(
    bars: tuple[OhlcvBar, ...], spec: DlineSpec, *, at: datetime
) -> NestedDetection | None:
    nested = NestedContinuationSpec(
        symbol=spec.symbol, trigger_timeframe=spec.trigger_timeframe, direction=spec.direction
    )
    events = detect_nested(bars, nested, evaluated_at=at)
    if not events:
        return None
    latest = events[-1]
    if (
        latest.state not in {BrainSetupState.FORMING, BrainSetupState.CONFIRMED}
        or latest.pullback_index is None
        or latest.expires_at <= at
        or "pullback_pivot_not_yet_confirmed" in latest.reason_codes
    ):
        return None
    return latest


def ranging(bars: tuple[OhlcvBar, ...], parameters: DlineParameters) -> bool:
    prior = bars[-parameters.range_lookback :]
    if len(prior) < parameters.range_lookback:
        return False  # explicitly unavailable, not a fabricated ADX reading
    travel = sum((abs(b.close - a.close) for a, b in pairwise(prior)), Decimal(0))
    efficiency = abs(prior[-1].close - prior[0].close) / travel if travel else Decimal(0)
    return efficiency <= parameters.range_efficiency_threshold


def fib09(
    bars: tuple[OhlcvBar, ...], spec: DlineSpec, *, entry: Decimal, atr: Decimal
) -> tuple[bool | None, tuple[str, ...]]:
    sign, highs, lows, _ = coordinates(bars, spec)
    width = spec.parameters.pivot_width
    low_points = pivots(lows, width, high=False)
    high_points = pivots(highs, width)
    pairs = [(a, b) for a in low_points for b in high_points if a < b and highs[b] > lows[a]]
    if not pairs:
        return None, ()
    a, b = max(pairs, key=lambda x: (x[1], x[0]))
    span = highs[b] - lows[a]
    price = (
        highs[b] - Decimal(".9") * span
        if spec.parameters.fib_09_orientation == "deep_retracement"
        else lows[a] + Decimal(".9") * span
    )
    return abs(entry * sign - price) <= atr * spec.parameters.fib_09_tolerance_atr, (
        bars[a].content_hash,
        bars[b].content_hash,
    )
