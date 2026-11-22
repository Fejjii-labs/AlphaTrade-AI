"""Live D-line (d_line/v2): Sofien's checklist only, closed candles only.

Owner spec 2026-11-22, for BloFin DEMO data collection. No Fib, EMA, ribbon or
moving-average input decides anything here.

Long (shorts mirror every price): a descending trendline through confirmed pivot
highs (2 touches minimum, 3 preferred) whose slope sits in the configured angle
band. A candle BODY close above the line triggers; wicks never do. The line is
frozen at the trigger. The next timeframe up must show higher highs and higher
lows (structure pivots only). Stop below the structure low (lowest low from the
line start to the trigger). TP1 at ``tp1_distance_fraction`` of the way to the
structure high (line start), TP2 at the structure high, TP3 at the latest
higher-timeframe swing high beyond TP2, the rest is the runner. Reward/risk to
TP2 must be at least ``minimum_reward_risk``.

Slope angle normalization (chart degrees depend on scaling, so this is fixed):
``angle = degrees(atan((|slope| / ATR) / atr_per_bar_at_45_degrees))``, where
slope is price change per trigger bar and ATR is the causal Wilder ATR of the
bars before the trigger. A line moving ``atr_per_bar_at_45_degrees`` ATR per bar
is 45 degrees. The default 0.2 matches the BTC 15m example chart (a line drawn
at about 45 degrees fell about 0.17 ATR per bar).

Attempts are simulated on the following closed candles (stop first on a shared
candle). A first attempt stopped at the structure stop ends the line. A second
attempt needs the first one closed at break-even after TP1, the structure low
intact, a close back at or through the frozen breakout level, then a new body
close beyond it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from itertools import combinations
from typing import Literal, Self
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, model_validator

from app.market_contracts.enums import Finality
from app.market_contracts.identity import interval_timedelta
from app.market_contracts.models import CanonicalModel
from app.market_contracts.ohlcv import OhlcvBar
from app.schemas.common import Timeframe, TradeDirection
from app.strategy_brain.sfp.trade_plan import wilder_atr

DLINE_LIVE_KIND = "d_line/v2"
DLINE_LIVE_NAMESPACE = UUID("1ba10003-0000-4000-8000-000000000002")
HTF_MAP = {Timeframe.M15: Timeframe.H1, Timeframe.M30: Timeframe.H2, Timeframe.H1: Timeframe.H4}


def htf_timeframe_for(timeframe: Timeframe) -> Timeframe:
    return HTF_MAP[timeframe]


class DlineLiveParameters(CanonicalModel):
    version: Literal["dline-live/v2"] = "dline-live/v2"
    # Trendline
    lookback: int = Field(default=120, ge=10, le=500)
    pivot_width: int = Field(default=2, ge=1, le=10)
    minimum_touches: int = Field(default=2, ge=2, le=10)
    preferred_touches: int = Field(default=3, ge=2, le=10)
    minimum_touch_separation: int = Field(default=2, ge=1, le=50)
    touch_tolerance_atr: Decimal = Field(default=Decimal(".15"), ge=0, le=2)
    atr_period: int = Field(default=14, ge=2, le=100)
    # Slope band in normalized degrees (see module docstring).
    atr_per_bar_at_45_degrees: Decimal = Field(default=Decimal(".2"), gt=0, le=10)
    ideal_angle_degrees: Decimal = Field(default=Decimal(45), gt=0, lt=90)
    angle_tolerance_degrees: Decimal = Field(default=Decimal(15), ge=0, lt=90)
    confidence_at_band_edge: Decimal = Field(default=Decimal(".5"), ge=0, le=1)
    two_touch_quality_factor: Decimal = Field(default=Decimal(".75"), ge=0, le=1)
    # Bigger trend on the next timeframe up: structure pivots only.
    htf_pivot_width: int = Field(default=2, ge=1, le=10)
    htf_lookback: int = Field(default=120, ge=5, le=500)
    # Entry: half at the breakout (next open), half as a limit at the line.
    entry_mode: Literal["split", "breakout", "retest"] = "split"
    breakout_fraction: Decimal = Field(default=Decimal(".5"), gt=0, lt=1)
    retest_expiry_bars: int = Field(default=6, ge=1, le=100)
    signal_expiry_bars: int = Field(default=1, ge=1, le=10)
    # Stop and targets
    stop_buffer_ticks: int = Field(default=1, ge=0, le=1000)
    tp1_distance_fraction: Decimal = Field(default=Decimal(".5"), gt=0, lt=1)
    tp1_fraction: Decimal = Field(default=Decimal(".15"), gt=0, lt=1)
    tp2_fraction: Decimal = Field(default=Decimal(".5"), gt=0, lt=1)
    tp3_fraction: Decimal = Field(default=Decimal(".2"), ge=0, lt=1)
    runner_fraction: Decimal = Field(default=Decimal(".15"), ge=0, lt=1)
    runner_trail_lookback_bars: int = Field(default=5, ge=1, le=999)
    minimum_reward_risk: Decimal = Field(default=Decimal(2), gt=0, le=100)
    max_attempts_per_line: int = Field(default=2, ge=1, le=10)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        total = self.tp1_fraction + self.tp2_fraction + self.tp3_fraction + self.runner_fraction
        if total != 1:
            raise ValueError("TP1 + TP2 + TP3 + runner fractions must equal 1")
        if self.preferred_touches < self.minimum_touches:
            raise ValueError("preferred_touches must be >= minimum_touches")
        if self.lookback < 2 * self.pivot_width + 3:
            raise ValueError("lookback must cover confirmed pivots")
        return self


class DlineLiveSpec(CanonicalModel):
    spec_version: Literal["strategy-pattern-spec/v1"] = "strategy-pattern-spec/v1"
    kind: Literal["d_line/v2"] = DLINE_LIVE_KIND
    name: str = "D-line"
    symbol: str = Field(min_length=2, max_length=32)
    trigger_timeframe: Literal[Timeframe.M15, Timeframe.M30, Timeframe.H1] = Timeframe.M15
    direction: TradeDirection = TradeDirection.LONG
    parameters: DlineLiveParameters = Field(default_factory=DlineLiveParameters)
    # Never real money: demo/paper venues only.
    paper_only: Literal[True] = True

    @property
    def htf_timeframe(self) -> Timeframe:
        return htf_timeframe_for(self.trigger_timeframe)


class DlineTarget(CanonicalModel):
    label: Literal["tp1", "tp2", "tp3"]
    price: Decimal
    fraction: Decimal


class DlineLiveSignal(CanonicalModel):
    setup_id: UUID
    line_id: UUID
    attempt: int
    direction: TradeDirection
    timeframe: Timeframe
    htf_timeframe: Timeframe
    anchor_indices: tuple[int, int]
    touches: tuple[int, ...]
    touch_count: int
    trigger_index: int
    trigger_event_id: str
    detected_at: AwareDatetime
    expires_at: AwareDatetime
    slope: Decimal
    atr: Decimal
    angle_degrees: Decimal
    confidence: Decimal
    breakout_level: Decimal
    structure_extreme: Decimal
    structure_stop_anchor: Decimal
    entry: Decimal
    entry_mode: Literal["split", "breakout", "retest"]
    breakout_fraction: Decimal
    retest_limit: Decimal
    retest_expires_at: AwareDatetime
    stop: Decimal
    targets: tuple[DlineTarget, ...]
    runner_fraction: Decimal
    reward_risk: Decimal
    htf_trend: Literal["higher_highs_higher_lows", "lower_highs_lower_lows"]
    reason_codes: tuple[str, ...] = ()


class DlineLiveScan(CanonicalModel):
    signals: tuple[DlineLiveSignal, ...] = ()
    rejections: tuple[tuple[int, str], ...] = ()
    bar_count: int = 0


class EntryLeg(CanonicalModel):
    kind: Literal["breakout", "retest"]
    fraction: Decimal
    status: Literal["filled", "pending", "expired"]
    price: Decimal | None = None
    filled_index: int | None = None


def slope_angle_degrees(slope: Decimal, atr: Decimal, atr_per_bar_at_45: Decimal) -> float:
    if atr <= 0:
        raise ValueError("ATR must be positive")
    return math.degrees(math.atan(float(abs(slope) / atr / atr_per_bar_at_45)))


def angle_in_band(angle: float, p: DlineLiveParameters) -> bool:
    return abs(angle - float(p.ideal_angle_degrees)) <= float(p.angle_tolerance_degrees)


def angle_confidence(angle: float, p: DlineLiveParameters) -> float:
    """1 at or below the ideal angle, falling linearly to the band-edge value above it."""

    ideal = float(p.ideal_angle_degrees)
    if angle <= ideal:
        return 1.0
    span = float(p.angle_tolerance_degrees)
    if span == 0:
        return float(p.confidence_at_band_edge)
    excess = min(angle - ideal, span) / span
    return 1.0 - excess * (1.0 - float(p.confidence_at_band_edge))


def _pivots(values: list[Decimal], width: int, *, high: bool) -> list[int]:
    return [
        i
        for i in range(width, len(values) - width)
        if all(
            (values[i] > values[j] if high else values[i] < values[j])
            for j in range(i - width, i + width + 1)
            if j != i
        )
    ]


def _round(price: Decimal, tick: Decimal, *, up: bool) -> Decimal:
    steps = (price / tick).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return (steps * tick).normalize() + Decimal(0)


def _q(value: float) -> Decimal:
    return Decimal(str(round(value, 6)))


@dataclass
class _Line:
    line_id: UUID
    a: int
    b: int
    touches: tuple[int, ...]
    slope: Decimal  # mirrored price per bar
    atr: Decimal
    angle: float
    confidence: float
    trigger: int
    breakout_level: Decimal  # mirrored, frozen at the first trigger
    structure_extreme: Decimal  # mirrored
    structure_low: Decimal  # mirrored
    stop: Decimal  # mirrored
    attempts: int = 0
    status: Literal["open", "be_closed", "invalidated", "done", "exhausted"] = "open"
    entry: Decimal = Decimal(0)
    tp1: Decimal = Decimal(0)
    tp2: Decimal = Decimal(0)
    tp1_hit: bool = False
    retested: bool = False
    last_trigger: int = 0
    reasons: list[str] = field(default_factory=list)


def _closed_prefix(
    bars: tuple[OhlcvBar, ...], spec: DlineLiveSpec, evaluated_at: datetime
) -> tuple[OhlcvBar, ...] | None:
    out: list[OhlcvBar] = []
    for bar in bars:
        if bar.timeframe != spec.trigger_timeframe or bar.instrument.provider_symbol != spec.symbol:
            return None
        if bar.finality is not Finality.FINAL or not bar.provider_complete:
            break
        if bar.interval_end > evaluated_at:
            break
        if out and (
            bar.interval_start != out[-1].interval_end or bar.instrument != out[0].instrument
        ):
            return None
        out.append(bar)
    return tuple(out)


def _htf_structure(
    htf: tuple[OhlcvBar, ...], sign: Decimal, p: DlineLiveParameters
) -> tuple[str | None, list[Decimal]]:
    """Return (None | refusal, mirrored confirmed pivot highs, oldest first)."""

    window = htf[-p.htf_lookback :]
    highs = [b.high if sign > 0 else -b.low for b in window]
    lows = [b.low if sign > 0 else -b.high for b in window]
    w = p.htf_pivot_width
    ph = [highs[k] for k in _pivots(highs, w, high=True)]
    pl = [lows[k] for k in _pivots(lows, w, high=False)]
    if len(ph) < 2 or len(pl) < 2:
        return "htf_structure_unavailable", ph
    if not (ph[-1] > ph[-2] and pl[-1] > pl[-2]):
        return "htf_structure_not_aligned", ph
    return None, ph


def detect_dline_live(
    bars: tuple[OhlcvBar, ...],
    htf_bars: tuple[OhlcvBar, ...],
    spec: DlineLiveSpec,
    *,
    evaluated_at: datetime,
    tick_size: Decimal,
) -> DlineLiveScan:
    """Pure, causal scan. Every decision at candle i reads candles <= i only."""

    spec = DlineLiveSpec.model_validate(spec.model_dump())
    p = spec.parameters
    closed = _closed_prefix(bars, spec, evaluated_at)
    if closed is None or tick_size <= 0:
        return DlineLiveScan(rejections=((-1, "required_evidence_invalid"),))
    bars = closed
    htf_tf = spec.htf_timeframe
    htf_all = tuple(
        b
        for b in htf_bars
        if b.timeframe == htf_tf
        and b.instrument.provider_symbol == spec.symbol
        and b.finality is Finality.FINAL
        and b.provider_complete
        and b.interval_end <= evaluated_at
    )
    sign = Decimal(1 if spec.direction is TradeDirection.LONG else -1)
    long = sign > 0
    highs = [b.high if long else -b.low for b in bars]
    lows = [b.low if long else -b.high for b in bars]
    closes = [b.close * sign for b in bars]
    pivots = _pivots(highs, p.pivot_width, high=True)
    delta = interval_timedelta(spec.trigger_timeframe)
    lines: list[_Line] = []
    signals: list[DlineLiveSignal] = []
    rejections: list[tuple[int, str]] = []

    def raw(value: Decimal) -> Decimal:
        return value * sign

    def toward_entry(value: Decimal) -> Decimal:
        # Targets and the retest limit round toward the entry side (conservative).
        return _round(raw(value), tick_size, up=not long)

    def plan(line: _Line, i: int) -> DlineLiveSignal | str:
        htf = tuple(b for b in htf_all if b.interval_end <= bars[i].interval_end)
        refusal, htf_highs = _htf_structure(htf, sign, p)
        if refusal is not None:
            return refusal
        entry = closes[i]
        stop = _round(raw(line.stop), tick_size, up=not long)
        tp2 = toward_entry(line.structure_extreme)
        entry_raw = raw(entry)
        risk = (entry_raw - stop) * sign
        reward = (tp2 - entry_raw) * sign
        if risk <= 0:
            return "risk_not_positive"
        if reward <= 0:
            return "structure_target_behind_entry"
        rr = reward / risk
        if rr < p.minimum_reward_risk:
            return "reward_risk_below_minimum"
        tp1 = toward_entry(entry + p.tp1_distance_fraction * (line.structure_extreme - entry))
        if not (tp1 - entry_raw) * sign > 0 or not (tp2 - tp1) * sign > 0:
            return "targets_not_ordered"
        reasons: list[str] = []
        tp3 = next(
            (toward_entry(h) for h in reversed(htf_highs) if (toward_entry(h) - tp2) * sign > 0),
            None,
        )
        targets = [DlineTarget(label="tp1", price=tp1, fraction=p.tp1_fraction)]
        if tp3 is not None and p.tp3_fraction > 0:
            targets.append(DlineTarget(label="tp2", price=tp2, fraction=p.tp2_fraction))
            targets.append(DlineTarget(label="tp3", price=tp3, fraction=p.tp3_fraction))
        else:
            targets.append(
                DlineTarget(label="tp2", price=tp2, fraction=p.tp2_fraction + p.tp3_fraction)
            )
            reasons.append("tp3_unavailable_merged_into_tp2")
        attempt = line.attempts + 1
        quality = line.confidence * (
            1.0 if len(line.touches) >= p.preferred_touches else float(p.two_touch_quality_factor)
        )
        level = toward_entry(line.breakout_level)
        detected = bars[i].interval_end
        return DlineLiveSignal(
            setup_id=uuid5(line.line_id, f"attempt:{attempt}:{bars[i].source_event_id}"),
            line_id=line.line_id,
            attempt=attempt,
            direction=spec.direction,
            timeframe=spec.trigger_timeframe,
            htf_timeframe=htf_tf,
            anchor_indices=(line.a, line.b),
            touches=line.touches,
            touch_count=len(line.touches),
            trigger_index=i,
            trigger_event_id=bars[i].source_event_id,
            detected_at=detected,
            expires_at=detected + delta * p.signal_expiry_bars,
            slope=raw(line.slope),
            atr=line.atr,
            angle_degrees=_q(line.angle),
            confidence=_q(quality),
            breakout_level=level,
            structure_extreme=raw(line.structure_extreme),
            structure_stop_anchor=raw(line.structure_low),
            entry=entry_raw,
            entry_mode=p.entry_mode,
            breakout_fraction=p.breakout_fraction,
            retest_limit=level,
            retest_expires_at=detected + delta * p.retest_expiry_bars,
            stop=stop,
            targets=tuple(targets),
            runner_fraction=p.runner_fraction,
            reward_risk=rr,
            htf_trend="higher_highs_higher_lows" if long else "lower_highs_lower_lows",
            reason_codes=(
                f"touches_{len(line.touches)}",
                "body_close_beyond_line",
                "htf_structure_aligned",
                *reasons,
            ),
        )

    def start_trade(line: _Line, signal: DlineLiveSignal, i: int) -> None:
        line.attempts = signal.attempt
        line.status = "open"
        line.entry = closes[i]
        line.tp1 = signal.targets[0].price * sign
        line.tp2 = signal.targets[1].price * sign
        line.tp1_hit = False
        line.retested = False
        line.last_trigger = i

    for i in range(1, len(bars)):
        # 1. Simulated trades and structure on this closed candle (entries were earlier).
        for line in lines:
            if line.status in {"invalidated", "done", "exhausted"} or line.last_trigger >= i:
                continue
            if line.status == "open":
                if not line.tp1_hit:
                    if lows[i] <= line.stop:
                        line.status = "invalidated"
                        rejections.append((i, "line_invalidated_after_stop"))
                    elif highs[i] >= line.tp1:
                        line.tp1_hit = True  # break-even applies from the next candle
                elif lows[i] <= line.entry:
                    line.status = "be_closed"
                elif highs[i] >= line.tp2:
                    line.status = "done"
                continue
            # be_closed: waiting for a second attempt
            if lows[i] <= line.stop:
                line.status = "invalidated"
                rejections.append((i, "structure_broken"))
                continue
            if closes[i] <= line.breakout_level:
                line.retested = True
                continue
            if line.retested and closes[i] > line.breakout_level:
                if line.attempts >= p.max_attempts_per_line:
                    line.status = "exhausted"
                    rejections.append((i, "attempt_cap_reached"))
                    continue
                result = plan(line, i)
                if isinstance(result, str):
                    rejections.append((i, result))
                    line.retested = False
                    continue
                signals.append(result)
                start_trade(line, result, i)
        # 2. A fresh line broken by this candle's body close.
        atr = wilder_atr(bars[:i], period=p.atr_period)
        if not atr:
            continue
        # One structure at a time: a new line starts after every frozen trigger.
        floor = max((line.trigger for line in lines), default=-1)
        confirmed = [k for k in pivots if k + p.pivot_width <= i - 1 and k >= i - p.lookback]
        found: list[tuple[tuple, _Line]] = []
        out_of_band = False
        for a, b in combinations(confirmed, 2):
            if b - a < p.minimum_touch_separation or a <= floor:
                continue
            slope = (highs[b] - highs[a]) / Decimal(b - a)
            if slope >= 0:
                continue

            def at(k: int, a: int = a, slope: Decimal = slope) -> Decimal:
                return highs[a] + slope * (k - a)

            if not closes[i] > at(i) or any(closes[k] > at(k) for k in range(a + 1, i)):
                continue
            touches: list[int] = []
            residual = Decimal(0)
            for k in confirmed:
                if k < a or (touches and k - touches[-1] < p.minimum_touch_separation):
                    continue
                error = abs(highs[k] - at(k))
                if error == 0 or error <= p.touch_tolerance_atr * atr:
                    touches.append(k)
                    residual += error
            if len(touches) < p.minimum_touches:
                continue
            angle = slope_angle_degrees(slope, atr, p.atr_per_bar_at_45_degrees)
            if not angle_in_band(angle, p):
                out_of_band = True
                continue
            structure_low = min(lows[a : i + 1])
            line = _Line(
                line_id=uuid5(
                    DLINE_LIVE_NAMESPACE,
                    f"{spec.symbol}:{spec.trigger_timeframe}:{spec.direction}:"
                    f"{bars[a].source_event_id}:{bars[b].source_event_id}",
                ),
                a=a,
                b=b,
                touches=tuple(touches),
                slope=slope,
                atr=atr,
                angle=angle,
                confidence=angle_confidence(angle, p),
                trigger=i,
                breakout_level=at(i),
                structure_extreme=highs[a],
                structure_low=structure_low,
                stop=structure_low - p.stop_buffer_ticks * tick_size,
            )
            found.append(((-len(touches), -(touches[-1] - a), residual, a, b), line))
        if not found:
            if out_of_band:
                rejections.append((i, "slope_out_of_band"))
            continue
        line = min(found, key=lambda item: item[0])[1]
        result = plan(line, i)
        if isinstance(result, str):
            rejections.append((i, result))
            continue
        lines.append(line)
        signals.append(result)
        start_trade(line, result, i)
    return DlineLiveScan(signals=tuple(signals), rejections=tuple(rejections), bar_count=len(bars))


def simulate_split_entry(
    signal: DlineLiveSignal, bars: tuple[OhlcvBar, ...], p: DlineLiveParameters
) -> tuple[EntryLeg, ...]:
    """Entry legs from closed candles after the trigger. Limit fills never beat the open."""

    i = signal.trigger_index
    long = signal.direction is TradeDirection.LONG
    legs: list[EntryLeg] = []
    if p.entry_mode in {"split", "breakout"}:
        fraction = p.breakout_fraction if p.entry_mode == "split" else Decimal(1)
        if i + 1 < len(bars):
            legs.append(
                EntryLeg(
                    kind="breakout",
                    fraction=fraction,
                    status="filled",
                    price=bars[i + 1].open,
                    filled_index=i + 1,
                )
            )
        else:
            legs.append(EntryLeg(kind="breakout", fraction=fraction, status="pending"))
    if p.entry_mode in {"split", "retest"}:
        fraction = Decimal(1) - p.breakout_fraction if p.entry_mode == "split" else Decimal(1)
        limit = signal.retest_limit
        last = i + p.retest_expiry_bars
        leg = None
        for j in range(i + 1, min(last, len(bars) - 1) + 1):
            bar = bars[j]
            if (long and bar.low <= limit) or (not long and bar.high >= limit):
                price = min(bar.open, limit) if long else max(bar.open, limit)
                leg = EntryLeg(
                    kind="retest", fraction=fraction, status="filled", price=price, filled_index=j
                )
                break
        if leg is None:
            leg = EntryLeg(
                kind="retest",
                fraction=fraction,
                status="expired" if len(bars) - 1 >= last else "pending",
            )
        legs.append(leg)
    return tuple(legs)
