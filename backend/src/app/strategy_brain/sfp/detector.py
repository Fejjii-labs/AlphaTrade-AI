"""Pure chronological SFP events, isolated from assessment, Candidate and execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid5

from app.market_contracts.enums import Finality, FreshnessState, SourceFamily
from app.market_contracts.identity import interval_timedelta, require_perpetual
from app.market_contracts.observation import PublicMarketObservation
from app.market_contracts.ohlcv import OhlcvBar
from app.schemas.common import TradeDirection
from app.schemas.nested_continuation import BrainSetupState, EvidenceAvailability
from app.services.canonical_serialization import canonical_sha256
from app.strategy_brain.sfp.contracts import (
    SFP_NAMESPACE,
    LevelKind,
    SfpCondition,
    SfpDetection,
    SfpScan,
    SfpSpec,
    SfpTradePlan,
    StructuralLevel,
    SweepDirection,
    SweepEvent,
    available_at,
    validate_binding,
)
from app.strategy_brain.sfp.levels import derive_levels, level_evidence_fresh, validate_level
from app.strategy_brain.sfp.quality import quality_components
from app.strategy_brain.sfp.trade_plan import (
    SFP_STOP_ATR_PERIOD,
    derive_sfp_trade_plan,
    wilder_atr,
)


@dataclass
class _Episode:
    """Ephemeral replay accumulator, never a persistence or Candidate authority."""

    setup_id: UUID
    sweep: SweepEvent
    start: int
    expires_at: datetime
    state: BrainSetupState = BrainSetupState.FORMING
    condition: SfpCondition = SfpCondition.WICK_THROUGH
    reclaim: int | None = None
    reclaim_id: UUID | None = None
    confirmation_id: UUID | None = None
    reclaim_extreme: Decimal | None = None
    outside_closes: int = 0
    last_observed_at: datetime | None = None
    trade_plan: SfpTradePlan | None = None
    plan_resolved: bool = False


def _known(level: StructuralLevel, bar: OhlcvBar, spec: SfpSpec) -> bool:
    return (
        level.known_at <= bar.interval_start
        and level.basis_bars[-1].interval_end <= bar.interval_start
        and bar.interval_start - level.established_at
        <= interval_timedelta(level.timeframe) * spec.parameters.level_lookback
    )


def _emit(
    episode: _Episode,
    *,
    bar: OhlcvBar,
    observation: PublicMarketObservation,
    index: int,
    bars: tuple[OhlcvBar, ...],
    observations: tuple[PublicMarketObservation, ...],
    levels: tuple[StructuralLevel, ...],
    spec: SfpSpec,
    reasons: tuple[str, ...],
    tick_size: Decimal | None = None,
    atr_multiple_floor: Decimal = Decimal("0"),
    observed_at: datetime | None = None,
    event_time: datetime | None = None,
) -> SfpDetection:
    spec_hash = canonical_sha256(spec)
    rejection_index = episode.reclaim if episode.reclaim is not None else episode.start
    knowledge_time = observed_at or available_at(observation)
    history = tuple(
        (b, o)
        for b, o in zip(bars[:index], observations[:index], strict=True)
        if available_at(o) <= knowledge_time
    )
    quality = quality_components(
        sweep=episode.sweep,
        bar=bar,
        observation=observation,
        history=tuple(b for b, _ in history),
        history_observations=tuple(o for _, o in history),
        levels=levels,
        direction=spec.direction,
        parameters=spec.parameters,
        reclaim_delay=episode.reclaim - episode.start if episode.reclaim is not None else None,
        reclaim_observation_id=episode.reclaim_id,
        rejection_bar=bars[rejection_index],
        rejection_observation=observations[rejection_index],
    )
    if (
        episode.state is BrainSetupState.CONFIRMED
        and episode.confirmation_id == observation.observation_id
        and not episode.plan_resolved
    ):
        # Entry is this confirmation close. Later intact bars keep this plan.
        # The stop is the larger of the percent buffer, N ticks, and k*ATR.
        # The floor widens k for the paper plan and the stored alert plan.
        # It is not part of setup_id.
        authored = spec.parameters.min_stop_atr_multiple
        multiple = max(authored, atr_multiple_floor) if atr_multiple_floor > 0 else authored
        atr = wilder_atr(bars[: index + 1], period=SFP_STOP_ATR_PERIOD) if multiple > 0 else None
        episode.trade_plan = derive_sfp_trade_plan(
            direction=spec.direction,
            entry=bar.close,
            sweep_extreme=episode.sweep.extreme,
            reference_price=episode.sweep.reference_level.price,
            stop_buffer=spec.parameters.stop_buffer,
            opposing_levels=levels,
            tick_size=tick_size,
            min_stop_ticks=spec.parameters.min_stop_ticks,
            atr=atr,
            min_stop_atr_multiple=multiple,
        )
        episode.plan_resolved = True
    trade_plan = episode.trade_plan if episode.state is BrainSetupState.CONFIRMED else None
    digest = canonical_sha256(
        {
            "setup": episode.setup_id,
            "spec": spec_hash,
            "observation": observation.content_hash,
            "state": episode.state,
            "condition": episode.condition,
            "reclaim": episode.reclaim_id,
            "confirmation": episode.confirmation_id,
            "reasons": reasons,
            "expiry": episode.expires_at if "setup_expired" in reasons else None,
            "reference_proof": [
                o.content_hash for o in episode.sweep.reference_level.basis_observations
            ],
            "sweep_observation": episode.sweep.evidence.content_hash,
            "quality": quality.model_dump(mode="python"),
        }
    )
    return SfpDetection(
        setup_id=episode.setup_id,
        event_id=uuid5(SFP_NAMESPACE, digest),
        spec_hash=spec_hash,
        state=episode.state,
        condition=episode.condition,
        direction=spec.direction,
        sweep=episode.sweep,
        event_time=event_time
        or (
            episode.expires_at
            if "setup_expired" in reasons
            else bar.interval_end
            if bar.finality is Finality.FINAL
            else observation.event_time
        ),
        observed_at=observed_at or available_at(observation),
        expires_at=episode.expires_at,
        evidence=observation,
        provisional=(bar.finality is not Finality.FINAL or not bar.provider_complete)
        and event_time is None,
        reclaim_observation_id=episode.reclaim_id,
        confirmation_observation_id=episode.confirmation_id,
        quality=quality,
        trade_plan=trade_plan,
        reason_codes=reasons,
    )


def _advance(
    episode: _Episode,
    bar: OhlcvBar,
    observation: PublicMarketObservation,
    index: int,
    spec: SfpSpec,
) -> tuple[str, ...]:
    p = spec.parameters
    sign = Decimal(1) if spec.direction is TradeDirection.LONG else Decimal(-1)
    price, close = episode.sweep.reference_level.price * sign, bar.close * sign
    closed = bar.finality is Finality.FINAL and bar.provider_complete
    if not closed:
        # A forming candle may show a wick or excursion, never a closed reclaim/failure.
        return ("provisional_candle_cannot_confirm_or_invalidate",)
    if max(bar.interval_end, available_at(observation)) >= episode.expires_at:
        episode.state = BrainSetupState.EXPIRED
        return ("setup_expired",)
    if (
        episode.reclaim is None
        and close < price
        and episode.outside_closes + 1 >= p.breakout_confirmation_closes
    ):
        episode.state, episode.condition = (
            BrainSetupState.INVALIDATED,
            SfpCondition.SUCCESSFUL_BREAKOUT,
        )
        return ("sustained_closed_breakout_research_definition",)
    extreme = bar.low if sign == 1 else -bar.high
    original_extreme = episode.sweep.extreme * sign
    if p.maximum_sweep_depth is not None and (price - extreme) / abs(price) > p.maximum_sweep_depth:
        episode.state, episode.condition = BrainSetupState.INVALIDATED, SfpCondition.INVALIDATED_SFP
        return ("maximum_sweep_depth_exceeded",)
    if extreme < original_extreme - abs(price) * p.structural_invalidation_buffer:
        episode.state, episode.condition = BrainSetupState.INVALIDATED, SfpCondition.INVALIDATED_SFP
        return ("closed_structural_extreme_breached",)
    if episode.reclaim is not None:
        if close <= price:
            episode.state, episode.condition = (
                BrainSetupState.INVALIDATED,
                SfpCondition.FAILED_RECLAIM,
            )
            return ("closed_reclaim_lost",)
        if episode.state is BrainSetupState.CONFIRMED:
            return ("confirmed_structure_intact",)
        if index - episode.reclaim > p.confirmation_window:
            episode.state = BrainSetupState.EXPIRED
            return ("confirmation_window_elapsed",)
        assert episode.reclaim_extreme is not None
        if index > episode.reclaim and close > episode.reclaim_extreme and close > bar.open * sign:
            episode.state, episode.condition = BrainSetupState.CONFIRMED, SfpCondition.CONFIRMED_SFP
            episode.confirmation_id = observation.observation_id
            return ("closed_break_of_reclaim_extreme",)
        return ("awaiting_closed_break_of_reclaim_extreme",)
    if index - episode.start > p.reclaim_window:
        episode.state, episode.condition = BrainSetupState.INVALIDATED, SfpCondition.FAILED_RECLAIM
        return ("reclaim_window_elapsed",)
    if close > price:
        episode.reclaim, episode.reclaim_id = index, observation.observation_id
        episode.reclaim_extreme = bar.high if sign == 1 else -bar.low
        episode.condition = SfpCondition.CONFIRMED_RECLAIM
        return ("closed_reclaim", "awaiting_later_closed_confirmation")
    episode.outside_closes = episode.outside_closes + 1 if close < price else 0
    episode.condition = (
        SfpCondition.TEMPORARY_EXCURSION if close < price else SfpCondition.WICK_THROUGH
    )
    return ("no_closed_reclaim",)


def detect_sfp(
    bars: tuple[OhlcvBar, ...],
    observations: tuple[PublicMarketObservation, ...],
    spec: SfpSpec,
    *,
    evaluated_at: datetime,
    context_levels: tuple[StructuralLevel, ...] = (),
    tick_size: Decimal | None = None,
    atr_multiple_floor: Decimal = Decimal("0"),
) -> SfpScan:
    """Replay causal detections from canonical evidence; no I/O or trading side effects.

    One current revision per candle is required. Exact duplicates converge;
    conflicting revisions fail closed. Only one terminal forming candle is
    allowed. Context levels must carry their closed canonical proofs.
    ``atr_multiple_floor`` widens the paper and alert stop. It is not hashed
    into ``setup_id``.
    """
    if evaluated_at.tzinfo is None:
        raise ValueError("evaluated_at must be timezone-aware")
    if not bars or not observations:
        return SfpScan(
            required_evidence=EvidenceAvailability.MISSING, reason_codes=("required_ohlcv_missing",)
        )
    if len(bars) != len(observations):
        raise ValueError("Every SFP candle needs its canonical observation")
    selected: dict[str, tuple[OhlcvBar, PublicMarketObservation]] = {}
    for bar, observation in zip(bars, observations, strict=True):
        if (
            available_at(observation) > evaluated_at
            or bar.interval_start > evaluated_at
            or (bar.finality is Finality.FINAL and bar.interval_end > evaluated_at)
        ):
            continue
        validate_binding(bar, observation)
        old = selected.get(bar.source_event_id)
        if old and (
            old[0].content_hash != bar.content_hash
            or old[1].content_hash != observation.content_hash
        ):
            raise ValueError("Conflicting candle revisions require a new explicit replay")
        if old is None or available_at(observation) < available_at(old[1]):
            selected[bar.source_event_id] = (bar, observation)
    pairs = sorted(selected.values(), key=lambda pair: pair[0].interval_start)
    if not pairs:
        return SfpScan(
            required_evidence=EvidenceAvailability.MISSING, reason_codes=("no_knowable_ohlcv",)
        )
    bars, observations = tuple(b for b, _ in pairs), tuple(o for _, o in pairs)
    identity = observations[0].identity
    require_perpetual(identity)
    if identity.source.family not in {
        SourceFamily.REPLAY_FIXTURE,
        SourceFamily.BINANCE_USDM_FUTURES_PUBLIC,
        SourceFamily.BYBIT_USDT_PERPETUAL_PUBLIC,
    }:
        raise ValueError("Uncontracted OHLCV source")
    if (
        identity.instrument.provider_symbol != spec.symbol
        or identity.timeframe != spec.trigger_timeframe
    ):
        raise ValueError("SFP spec does not match evidence identity")
    delta, p = interval_timedelta(spec.trigger_timeframe), spec.parameters
    for i, (bar, observation) in enumerate(pairs):
        if (
            observation.identity != identity
            or bar.adapter_version != identity.source.adapter_version
        ):
            raise ValueError("Mixed SFP evidence identities or adapter versions")
        if (
            (i and bar.interval_start != bars[i - 1].interval_end)
            or (
                i < len(bars) - 1
                and (bar.finality is not Finality.FINAL or not bar.provider_complete)
            )
            or bar.finality not in {Finality.FINAL, Finality.FORMING}
        ):
            return SfpScan(
                required_evidence=EvidenceAvailability.INCOMPLETE,
                reason_codes=("required_causal_series_incomplete",),
            )
        if observation.freshness_state is not FreshnessState.FRESH:
            return SfpScan(
                required_evidence=EvidenceAvailability.STALE,
                reason_codes=("required_observation_stale",),
            )
    last_clock = (
        bars[-1].interval_end
        if bars[-1].finality is Finality.FINAL
        else available_at(observations[-1])
    )
    if evaluated_at - last_clock >= delta * p.required_evidence_max_age_bars:
        return SfpScan(
            required_evidence=EvidenceAvailability.STALE,
            reason_codes=("required_ohlcv_tail_stale",),
        )
    for level in context_levels:
        validate_level(level)
        if (
            level.identity.instrument != identity.instrument
            or level.identity.source != identity.source
            or level.identity.provenance != identity.provenance
        ):
            raise ValueError("Context level must use the same canonical market and source")
        if (
            level.kind in {LevelKind.HTF_SUPPORT, LevelKind.HTF_RESISTANCE}
            and interval_timedelta(level.timeframe) <= delta
        ):
            raise ValueError("HTF references require an actual higher timeframe")
    all_levels = (*derive_levels(bars, observations, p, evaluated_at=evaluated_at), *context_levels)
    unique: dict[UUID, StructuralLevel] = {}
    for level in sorted(all_levels, key=lambda level: (level.known_at, str(level.level_id))):
        unique.setdefault(level.level_id, level)
    episodes: dict[UUID, _Episode] = {}
    episodes_by_price: dict[Decimal, _Episode] = {}
    events: list[SfpDetection] = []
    support = spec.direction is TradeDirection.LONG
    for i, (bar, observation) in enumerate(pairs):
        known = tuple(level for level in unique.values() if _known(level, bar, spec))
        # Same-price aliases select the strongest currently knowable reference.
        eligible: dict[Decimal, StructuralLevel] = {}
        for level in sorted(
            known, key=lambda level: (-level.significance.points, str(level.level_id))
        ):
            prior_episode = episodes_by_price.get(level.price)
            if prior_episode is not None and (
                prior_episode.state not in {BrainSetupState.INVALIDATED, BrainSetupState.EXPIRED}
                or level.established_at < prior_episode.sweep.candle_end
            ):
                continue
            if (
                level.kind.support == support
                and level.significance.points >= p.minimum_level_significance
                and level_evidence_fresh(level)
                and level.level_id not in episodes
            ):
                eligible.setdefault(level.price, level)
        for level in eligible.values():
            if not i:
                continue
            price, extreme = level.price, bar.low if support else bar.high
            depth = price - extreme if support else extreme - price
            if (
                (bars[i - 1].close < price if support else bars[i - 1].close > price)
                or available_at(observations[i - 1]) > available_at(observation)
                or depth <= 0
                or depth / price < p.minimum_sweep_depth
            ):
                continue
            sweep = SweepEvent(
                reference_level=level,
                direction=SweepDirection.BELOW if support else SweepDirection.ABOVE,
                depth=depth,
                depth_ratio=depth / price,
                extreme=extreme,
                event_time=observation.event_time,
                candle_end=bar.interval_end,
                observed_at=available_at(observation),
                finality=bar.finality,
                candle_closed=bar.finality is Finality.FINAL and bar.provider_complete,
                evidence=observation,
            )
            setup_id = uuid5(
                SFP_NAMESPACE,
                canonical_sha256(
                    {
                        "spec": spec.model_dump(mode="python"),
                        "level": level.level_id,
                        "sweep_bar": bar.source_event_id,
                    }
                ),
            )
            episodes[level.level_id] = _Episode(
                setup_id, sweep, i, bar.interval_end + delta * p.expiry_bars
            )
            episodes_by_price[level.price] = episodes[level.level_id]
        for episode in episodes.values():
            if episode.state in {BrainSetupState.INVALIDATED, BrainSetupState.EXPIRED}:
                continue
            if available_at(observation) < (episode.last_observed_at or episode.sweep.observed_at):
                continue  # Delayed episode evidence cannot backdate an earlier observation.
            episode.last_observed_at = available_at(observation)
            reasons: tuple[str, ...]
            if (
                i == episode.start
                and p.maximum_sweep_depth is not None
                and episode.sweep.depth_ratio > p.maximum_sweep_depth
                and episode.sweep.candle_closed
            ):
                episode.state, episode.condition = (
                    BrainSetupState.INVALIDATED,
                    SfpCondition.INVALIDATED_SFP,
                )
                reasons = ("maximum_sweep_depth_exceeded",)
            else:
                reasons = _advance(episode, bar, observation, i, spec)
            events.append(
                _emit(
                    episode,
                    bar=bar,
                    observation=observation,
                    index=i,
                    bars=bars,
                    observations=observations,
                    levels=known,
                    spec=spec,
                    reasons=reasons,
                    tick_size=tick_size,
                    atr_multiple_floor=atr_multiple_floor,
                )
            )
    # A clock can expire a setup between closes without inventing another market candle.
    for episode in episodes.values():
        if (
            episode.state not in {BrainSetupState.INVALIDATED, BrainSetupState.EXPIRED}
            and evaluated_at >= episode.expires_at
        ):
            episode.state = BrainSetupState.EXPIRED
            events.append(
                _emit(
                    episode,
                    bar=bars[-1],
                    observation=observations[-1],
                    index=len(bars) - 1,
                    bars=bars,
                    observations=observations,
                    levels=tuple(
                        level for level in unique.values() if _known(level, bars[-1], spec)
                    ),
                    spec=spec,
                    reasons=("setup_expired",),
                    tick_size=tick_size,
                    atr_multiple_floor=atr_multiple_floor,
                    event_time=episode.expires_at,
                    observed_at=evaluated_at,
                )
            )
    return SfpScan(
        events=tuple(
            sorted(
                events, key=lambda event: (event.observed_at, event.event_time, str(event.event_id))
            )
        ),
        required_evidence=EvidenceAvailability.AVAILABLE,
    )
