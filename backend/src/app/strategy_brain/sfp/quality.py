"""Separate measured components; no aggregate confidence or win probability."""

from decimal import Decimal
from itertools import pairwise
from uuid import UUID

from app.market_contracts.observation import PublicMarketObservation
from app.market_contracts.ohlcv import OhlcvBar
from app.schemas.common import TradeDirection
from app.schemas.nested_continuation import EvidenceAvailability
from app.strategy_brain.sfp.contracts import (
    LevelKind,
    QualityComponent,
    SfpParameters,
    SfpQuality,
    StructuralLevel,
    SweepEvent,
)
from app.strategy_brain.sfp.levels import level_evidence_fresh
from app.strategy_brain.sfp.trade_plan import nearest_opposing_level


def unavailable(
    reason: str,
    unit: str,
    *,
    availability: EvidenceAvailability = EvidenceAvailability.MISSING,
    observation_ids: tuple[UUID, ...] = (),
) -> QualityComponent:
    return QualityComponent(
        availability=availability,
        unit=unit,
        reason=reason,
        observation_ids=observation_ids,
    )


def measured(value: Decimal, unit: str, reason: str, ids: tuple[UUID, ...]) -> QualityComponent:
    return QualityComponent(
        availability=EvidenceAvailability.AVAILABLE,
        value=value,
        unit=unit,
        reason=reason,
        observation_ids=tuple(dict.fromkeys(ids)),
    )


def quality_components(
    *,
    sweep: SweepEvent,
    bar: OhlcvBar,
    observation: PublicMarketObservation,
    history: tuple[OhlcvBar, ...],
    history_observations: tuple[PublicMarketObservation, ...],
    levels: tuple[StructuralLevel, ...],
    direction: TradeDirection,
    parameters: SfpParameters,
    reclaim_delay: int | None,
    reclaim_observation_id: UUID | None,
    rejection_bar: OhlcvBar,
    rejection_observation: PublicMarketObservation,
) -> SfpQuality:
    reference, p = sweep.reference_level, parameters
    support = direction is TradeDirection.LONG
    ids = (observation.observation_id,)
    level_ids = tuple(o.observation_id for o in reference.basis_observations)
    matching_htf = [
        level
        for level in levels
        if level.kind in {LevelKind.HTF_SUPPORT, LevelKind.HTF_RESISTANCE}
        and level.kind.support == support
        and abs(level.price - reference.price) / reference.price <= p.htf_alignment_tolerance
    ]
    htf = [level for level in matching_htf if level_evidence_fresh(level)]
    target = nearest_opposing_level(levels, support=support, price=bar.close)
    prior = history[-p.quality_lookback :]
    prior_obs = history_observations[-p.quality_lookback :]
    prior_ids = tuple(o.observation_id for o in prior_obs)
    average_volume = (
        sum((b.base_volume for b in prior), Decimal(0)) / len(prior) if prior else Decimal(0)
    )
    closes = [b.close for b in prior]
    movement = sum((abs(b - a) for a, b in pairwise(closes)), Decimal(0))
    rejected = rejection_bar
    wick = (
        min(rejected.open, rejected.close) - rejected.low
        if support
        else rejected.high - max(rejected.open, rejected.close)
    )
    span = rejected.high - rejected.low
    return SfpQuality(
        level_importance=measured(
            reference.significance.points,
            "structural_points",
            "inspect_level_significance_components",
            level_ids,
        ),
        higher_timeframe_alignment=measured(
            Decimal(1),
            "alignment_flag",
            "known_same_side_htf_reference_within_tolerance",
            tuple(o.observation_id for level in htf for o in level.basis_observations),
        )
        if htf
        else unavailable(
            "matching_htf_evidence_stale" if matching_htf else "no_matching_known_htf_reference",
            "alignment_flag",
            availability=EvidenceAvailability.STALE
            if matching_htf
            else EvidenceAvailability.MISSING,
            observation_ids=tuple(
                o.observation_id for level in matching_htf for o in level.basis_observations
            ),
        ),
        sweep_quality=measured(
            sweep.depth_ratio,
            "reference_price_ratio",
            "observed_excursion_depth_only",
            (sweep.evidence.observation_id, *level_ids),
        ),
        reclaim_speed=measured(
            Decimal(reclaim_delay),
            "bars",
            "closed_reclaim_delay_from_sweep",
            (sweep.evidence.observation_id, reclaim_observation_id),
        )
        if reclaim_delay is not None and reclaim_observation_id is not None
        else unavailable("closed_reclaim_not_observed", "bars"),
        rejection_strength=measured(
            wick / span,
            "candle_range_ratio",
            "reclaim_or_sweep_candle_wick_proxy",
            (rejection_observation.observation_id,),
        )
        if span
        else unavailable("zero_candle_range", "candle_range_ratio"),
        volume=measured(
            bar.base_volume / average_volume,
            "relative_base_volume",
            "candle_volume_not_order_flow",
            (*prior_ids, *ids),
        )
        if len(prior) >= p.quality_lookback and average_volume > 0
        else unavailable("insufficient_positive_volume_baseline", "relative_base_volume"),
        available_target_space=measured(
            abs(target.price - bar.close),
            bar.instrument.price_unit,
            "distance_to_nearest_known_opposing_structure_not_a_trade_target",
            (*ids, *(o.observation_id for o in target.basis_observations)),
        )
        if target
        else unavailable("no_known_opposing_structure", bar.instrument.price_unit),
        market_regime=measured(
            abs(closes[-1] - closes[0]) / movement if movement else Decimal(0),
            "directional_efficiency_ratio",
            "closed_candle_efficiency_research_proxy_not_regime_classifier",
            prior_ids,
        )
        if len(prior) >= p.quality_lookback
        else unavailable("insufficient_closed_history", "directional_efficiency_ratio"),
        cvd=unavailable(
            "verified_cvd_not_connected",
            "quote_delta",
            availability=EvidenceAvailability.UNSUPPORTED,
        ),
        order_flow=unavailable(
            "verified_order_flow_not_connected",
            "flow",
            availability=EvidenceAvailability.UNSUPPORTED,
        ),
        open_interest=unavailable(
            "verified_open_interest_not_connected",
            "contracts",
            availability=EvidenceAvailability.UNSUPPORTED,
        ),
    )
