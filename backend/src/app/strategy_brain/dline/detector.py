"""Pure draft detector. Prefix scans reproduce frozen would-be candidates, never fills."""

from datetime import datetime
from decimal import Decimal
from uuid import uuid5

from pydantic import Field

from app.market_contracts.identity import interval_timedelta
from app.market_contracts.models import CanonicalModel
from app.market_contracts.observation import PublicMarketObservation
from app.market_contracts.ohlcv import OhlcvBar
from app.services.canonical_serialization import canonical_sha256
from app.strategy_brain.dline.confluences import (
    closed_evidence,
    fib09,
    htf_context,
    nested_context,
    ranging,
)
from app.strategy_brain.dline.contracts import DlineCandidate, DlineSpec, SourcedLevel
from app.strategy_brain.dline.geometry import NAMESPACE, coordinates, pivots, select_line
from app.strategy_brain.dline.trade_plan import build_plan
from app.strategy_brain.sfp.contracts import available_at


class DlineScan(CanonicalModel):
    spec_hash: str
    candidates: tuple[DlineCandidate, ...] = ()
    reason_codes: tuple[str, ...] = ()
    evidence_hashes: tuple[str, ...] = Field(default=())


def detect_dline(
    bars: tuple[OhlcvBar, ...],
    observations: tuple[PublicMarketObservation, ...],
    spec: DlineSpec,
    *,
    evaluated_at: datetime,
    tick_size: Decimal,
    htf_bars: tuple[OhlcvBar, ...] = (),
    htf_observations: tuple[PublicMarketObservation, ...] = (),
    levels: tuple[SourcedLevel, ...] = (),
) -> DlineScan:
    # Revalidate even callers that bypassed Pydantic using model_copy.
    spec = DlineSpec.model_validate(spec.model_dump())
    signature = canonical_sha256(spec)
    empty = DlineScan(spec_hash=signature, reason_codes=("required_evidence_unavailable",))
    if tick_size <= 0:
        return empty
    bars, observations = closed_evidence(
        bars, observations, at=evaluated_at, symbol=spec.symbol, timeframe=spec.trigger_timeframe
    )
    if not bars:
        return empty
    levels = tuple(x for x in levels if x.instrument == bars[0].instrument)
    p = spec.parameters
    sign, _, lows, closes = coordinates(bars, spec)
    results = []
    attempts = {}
    active_lines = {}
    pending = None
    continuations = {}
    for i in range(2 * p.pivot_width + 2, len(bars)):
        # Every earlier receipt must already have been available at this trigger.
        if any(available_at(o) > available_at(observations[i]) for o in observations[:i]):
            continue
        # Freeze at the first crossing; later touches never refit this event.
        line = select_line(bars[:i], spec)
        if line is not None:
            previous = (line.break_price - line.slope) * sign
            crossed = (
                closes[i - 1] <= previous
                and closes[i] > line.break_price * sign + p.break_buffer_atr * line.atr
            )
            if crossed:
                known = max(observations[i].observed_at, observations[i].receive_time)
                line = line.model_copy(update={"frozen_at": known})
                # Same geometric line keeps identity for repeat crossings after reset.
                active_lines.setdefault(line.line_id, line)
                pending = (line, i)
        # Re-use frozen lines for later crossings; no stop-out/fill assertions.
        if pending is None:
            for prior in active_lines.values():
                a = prior.anchor_indices[0]
                projected = prior.anchor_price + prior.slope * (i - a)
                if (
                    i - a < p.lookback
                    and closes[i - 1] <= (projected - prior.slope) * sign
                    and closes[i] > projected * sign + p.break_buffer_atr * prior.atr
                ):
                    pending = (
                        prior.model_copy(
                            update={"break_price": projected, "frozen_at": bars[i].interval_end}
                        ),
                        i,
                    )
                    break
        if pending is None:
            continue
        frozen, trigger = pending
        if attempts.get(frozen.line_id, 0) >= p.attempt_cap:
            pending = None
            continue
        if p.entry_mode == "retest":
            if i == trigger:
                continue
            if (
                i - trigger > p.retest_window
                or closes[i] < frozen.break_price * sign - p.retest_tolerance_atr * frozen.atr
            ):
                pending = None
                continue
            if not bars[i].low <= frozen.break_price <= bars[i].high:
                continue
        pending = None
        history = bars[:trigger]  # all stop/confluence anchors must predate the break
        at = max(observations[trigger].observed_at, observations[trigger].receive_time)
        entry = bars[trigger].close if p.entry_mode == "breakout" else frozen.break_price
        aligned, parent, htf_proof = htf_context(
            htf_bars, htf_observations, spec, at=at, instrument=bars[0].instrument, entry=entry
        )
        if (p.htf_required and aligned is not True) or (parent and not parent["held"]):
            continue
        stops = pivots(lows[:trigger], p.pivot_width, high=False)
        if not stops:
            continue
        stop_index = stops[-1]
        nested = nested_context(history, spec, at=at)
        if nested is not None:
            stop_index = nested.pullback_index
        anchor = frozen.anchor_indices[0]
        pattern = min(lows[anchor:trigger]) * sign
        stop_anchor = lows[stop_index] * sign
        is_range = ranging(bars[: trigger + 1], p)
        plan = build_plan(
            entry=entry,
            stop_anchor=stop_anchor,
            pattern_extreme=pattern,
            trend_start=frozen.anchor_price,
            atr=frozen.atr,
            tick=tick_size,
            spec=spec,
            at=at,
            levels=tuple(x for x in levels if x.instrument == bars[0].instrument),
            ranging=is_range,
        )
        if plan is None:
            continue
        deep, deep_proof = fib09(history, spec, entry=entry, atr=frozen.atr)
        sourced = tuple(
            x
            for x in levels
            if x.symbol == spec.symbol
            and x.instrument == bars[0].instrument
            and not x.consumed
            and x.known_at <= at < x.expires_at
            and (x.price - entry) * sign > 0
        )
        unavailable = ["automated_ob_provider", "automated_poc_provider"]
        components = {
            "touch_count": str(len(frozen.touches)),
            "htf_bias": "present" if aligned else "absent" if aligned is False else "unavailable",
            "htf_trendline": "present" if parent else "unavailable",
            "nested_1_2": "present" if nested else "unavailable",
            "fib_09_retest": ("present" if deep else "absent")
            if deep is not None and p.entry_mode == "retest"
            else "unavailable",
            "sourced_target": "present" if sourced else "unavailable",
            "ranging": "unavailable" if trigger + 1 < p.range_lookback else str(is_range).lower(),
        }
        unavailable.extend(k for k, v in components.items() if v == "unavailable")
        attempt = attempts.get(frozen.line_id, 0) + 1
        attempts[frozen.line_id] = attempt
        parent_id = uuid5(NAMESPACE, parent["id"]) if parent else None
        if parent_id and attempt == 1:
            continuations[parent_id] = continuations.get(parent_id, 0) + 1
        proofs = {
            "closed_ohlcv": tuple(b.content_hash for b in bars[anchor : trigger + 1]),
            "receipts": tuple(o.content_hash for o in observations[anchor : trigger + 1]),
            "htf_bias": htf_proof,
            "fib_09": deep_proof,
            "nested_1_2": (str(nested.setup_id),) if nested else (),
            "sourced_target": tuple(x.evidence_ref for x in sourced),
        }
        if parent:
            proofs["htf_trendline"] = parent["proof"]
        results.append(
            DlineCandidate(
                setup_id=uuid5(frozen.line_id, f"{signature}:{bars[trigger].source_event_id}"),
                spec_hash=signature,
                line=frozen,
                attempt=attempt,
                direction=spec.direction,
                timeframe=spec.trigger_timeframe,
                observed_at=max(at, observations[i].receive_time, observations[i].observed_at),
                expires_at=bars[i].interval_end
                + interval_timedelta(spec.trigger_timeframe) * p.retest_window,
                trigger_event_id=bars[trigger].source_event_id,
                entry=entry,
                entry_intent="next_open_reference"
                if p.entry_mode == "breakout"
                else "retest_reference",
                aggressive_tp=is_range,
                confluences=components,
                proofs=proofs,
                unavailable=tuple(unavailable),
                quality_points=min(len(frozen.touches), p.preferred_touches)
                + sum(v == "present" for v in components.values()),
                parent_line_id=parent_id,
                continuation_number=continuations.get(parent_id, 1),
                **plan,
            )
        )
    return DlineScan(
        spec_hash=signature,
        candidates=tuple(results),
        evidence_hashes=tuple(b.content_hash for b in bars),
    )
