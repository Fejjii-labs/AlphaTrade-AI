"""Versioned SFP interpretation of existing canonical candles and observations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, model_validator

from app.market_contracts.enums import Finality, ObservationType
from app.market_contracts.hashing import semantic_content_hash
from app.market_contracts.identity import EvidenceMarketIdentity
from app.market_contracts.models import (
    CanonicalModel,
    NonNegativeCanonicalDecimal,
    PositiveCanonicalDecimal,
)
from app.market_contracts.observation import PublicMarketObservation
from app.market_contracts.ohlcv import OhlcvBar, observation_id_for
from app.schemas.common import Timeframe, TradeDirection
from app.schemas.nested_continuation import BrainSetupState, EvidenceAvailability
from app.services.canonical_serialization import canonical_sha256

SFP_KIND: Literal["swing_failure_pattern/v1"] = "swing_failure_pattern/v1"
SFP_NAMESPACE = UUID("1ba10002-0000-4000-8000-000000000001")


class SfpParameters(CanonicalModel):
    """Explicit research inputs. Ratios are relative to reference price, never win odds."""

    version: Literal["sfp-research/v1", "sfp-research/v2"] = "sfp-research/v1"
    provisional: Literal[True] = True
    level_lookback: int = Field(ge=3, le=1000)
    pivot_width: int = Field(ge=1, le=20)
    minimum_level_significance: NonNegativeCanonicalDecimal
    minimum_sweep_depth: NonNegativeCanonicalDecimal = Field(lt=1)
    maximum_sweep_depth: NonNegativeCanonicalDecimal | None = Field(lt=1)
    equal_level_tolerance: NonNegativeCanonicalDecimal = Field(lt=1)
    reclaim_window: int = Field(ge=0, le=500)
    confirmation_window: int = Field(ge=1, le=500)
    breakout_confirmation_closes: int = Field(ge=1, le=500)
    structural_invalidation_buffer: NonNegativeCanonicalDecimal = Field(lt=1)
    expiry_bars: int = Field(ge=1, le=2000)
    required_evidence_max_age_bars: int = Field(ge=1, le=10)
    quality_lookback: int = Field(ge=2, le=500)
    htf_alignment_tolerance: NonNegativeCanonicalDecimal = Field(lt=1)
    confirmation: Literal["closed_break_of_reclaim_extreme"] = "closed_break_of_reclaim_extreme"
    # None keeps stored v1 specs from alerting. A new version sets the distance.
    approach_proximity: NonNegativeCanonicalDecimal | None = None
    approach_cooldown_seconds: int = Field(default=900, ge=60, le=86400)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.level_lookback < 2 * self.pivot_width + 1:
            raise ValueError("level_lookback must cover both pivot wings")
        if (
            self.maximum_sweep_depth is not None
            and self.maximum_sweep_depth < self.minimum_sweep_depth
        ):
            raise ValueError("maximum_sweep_depth must be >= minimum_sweep_depth")
        if self.approach_proximity is not None and self.approach_proximity >= 1:
            raise ValueError("approach_proximity must be below 1")
        return self


def sfp_research_v2_parameters() -> SfpParameters:
    """New-version defaults. Does not rewrite a stored strategy version.

    Lookback is 72 closed bars (inside the 48-96 band). Sweep depth is
    0.2-0.3%. Approach proximity is 0.1% and stays off until this version
    is created and approved.
    """

    return SfpParameters(
        version="sfp-research/v2",
        level_lookback=72,
        pivot_width=2,
        minimum_level_significance=Decimal("2"),
        minimum_sweep_depth=Decimal("0.002"),
        maximum_sweep_depth=Decimal("0.003"),
        equal_level_tolerance=Decimal("0.001"),
        reclaim_window=2,
        confirmation_window=2,
        breakout_confirmation_closes=2,
        structural_invalidation_buffer=Decimal("0.005"),
        expiry_bars=6,
        required_evidence_max_age_bars=1,
        quality_lookback=3,
        htf_alignment_tolerance=Decimal("0.01"),
        approach_proximity=Decimal("0.001"),
        approach_cooldown_seconds=900,
    )


class SfpSpec(CanonicalModel):
    """Authored family contract, deliberately not registered with the AST compiler yet."""

    spec_version: Literal["strategy-pattern-spec/v1"] = "strategy-pattern-spec/v1"
    kind: Literal["swing_failure_pattern/v1"] = SFP_KIND
    name: str = "Swing Failure Pattern research foundation"
    symbol: str = Field(min_length=2, max_length=32)
    trigger_timeframe: Timeframe
    direction: TradeDirection
    parameters: SfpParameters
    paper_only: Literal[True] = True


class LevelKind(StrEnum):
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"
    RANGE_HIGH = "range_high"
    RANGE_LOW = "range_low"
    EQUAL_HIGHS = "equal_highs"
    EQUAL_LOWS = "equal_lows"
    HTF_RESISTANCE = "htf_resistance"
    HTF_SUPPORT = "htf_support"

    @property
    def support(self) -> bool:
        return self in {
            LevelKind.SWING_LOW,
            LevelKind.RANGE_LOW,
            LevelKind.EQUAL_LOWS,
            LevelKind.HTF_SUPPORT,
        }


class LevelSignificance(CanonicalModel):
    model_version: Literal["structural-components/v1"] = "structural-components/v1"
    touches: int = Field(ge=1)
    pivot_confirmation_bars: int = Field(ge=0)
    prominence_ratio: NonNegativeCanonicalDecimal
    higher_timeframe_reference: bool

    @property
    def points(self) -> Decimal:
        """Ordinal research points; each contribution remains inspectable."""
        return (
            Decimal(self.touches)
            + Decimal(self.pivot_confirmation_bars > 0)
            + self.prominence_ratio
            + Decimal(self.higher_timeframe_reference)
        )


def available_at(observation: PublicMarketObservation) -> datetime:
    return max(observation.observed_at, observation.receive_time)


def validate_binding(bar: OhlcvBar, observation: PublicMarketObservation) -> None:
    """Verify the existing envelope and its canonical payload, without new evidence IDs."""
    if (
        observation.observation_type is not ObservationType.OHLCV
        or observation.identity.instrument != bar.instrument
        or observation.identity.timeframe != bar.timeframe
        or observation.source_event_id != bar.source_event_id
        or observation.payload_content_hash != bar.content_hash
        or observation.finality != bar.finality
        or observation.revision != bar.revision
        or observation.interval_start != bar.interval_start
        or observation.interval_end != bar.interval_end
        or observation.event_time != bar.interval_start
        or observation.source_time != bar.source_time
        or observation.observation_id
        != observation_id_for(bar.source_event_id, finality=bar.finality, revision=bar.revision)
        or bar.content_hash != semantic_content_hash(bar, extra_exclude=frozenset({"content_hash"}))
        or observation.content_hash
        != semantic_content_hash(observation, extra_exclude=frozenset({"content_hash"}))
    ):
        raise ValueError("SFP requires matching canonical OHLCV observations and hashes")
    if (
        observation.observed_at < bar.interval_start
        or observation.receive_time < bar.interval_start
        or bar.source_time > available_at(observation)
        or (bar.finality is Finality.FINAL and available_at(observation) < bar.interval_end)
    ):
        raise ValueError("OHLCV observation precedes its knowable event")


class StructuralLevel(CanonicalModel):
    """A derived reference with its original canonical proof, not a level store."""

    kind: LevelKind
    identity: EvidenceMarketIdentity
    price: PositiveCanonicalDecimal
    anchor_event_ids: tuple[str, ...] = Field(min_length=1)
    basis_bars: tuple[OhlcvBar, ...] = Field(min_length=1)
    basis_observations: tuple[PublicMarketObservation, ...] = Field(min_length=1)
    established_at: AwareDatetime
    known_at: AwareDatetime
    significance: LevelSignificance
    equality_tolerance: NonNegativeCanonicalDecimal = Field(lt=1)

    @property
    def timeframe(self) -> Timeframe:
        assert self.identity.timeframe is not None  # Verified by canonical basis binding.
        return self.identity.timeframe

    @property
    def level_id(self) -> UUID:
        # Natural anchors keep identity stable when receive times/history windows change.
        return uuid5(
            SFP_NAMESPACE,
            canonical_sha256(
                {
                    "kind": self.kind,
                    "identity": self.identity.model_dump(mode="python"),
                    "price": self.price,
                    "anchors": self.anchor_event_ids,
                }
            ),
        )

    @model_validator(mode="after")
    def proof(self) -> Self:
        from app.strategy_brain.sfp.levels import validate_level

        validate_level(self)
        return self


class SfpCondition(StrEnum):
    """Observed price behavior; BrainSetupState remains the only setup lifecycle."""

    WICK_THROUGH = "wick_through"
    TEMPORARY_EXCURSION = "temporary_excursion"
    CONFIRMED_RECLAIM = "confirmed_reclaim"
    FAILED_RECLAIM = "failed_reclaim"
    SUCCESSFUL_BREAKOUT = "successful_breakout"
    INVALIDATED_SFP = "invalidated_sfp"
    CONFIRMED_SFP = "confirmed_sfp"


class SweepDirection(StrEnum):
    BELOW = "below"
    ABOVE = "above"


class SweepEvent(CanonicalModel):
    reference_level: StructuralLevel
    direction: SweepDirection
    depth: PositiveCanonicalDecimal
    depth_ratio: PositiveCanonicalDecimal
    extreme: PositiveCanonicalDecimal
    # Canonical kline event time is its start; exact intrabar sweep time is unavailable.
    event_time: AwareDatetime
    candle_end: AwareDatetime
    observed_at: AwareDatetime
    finality: Finality
    candle_closed: bool
    evidence: PublicMarketObservation


class QualityComponent(CanonicalModel):
    availability: EvidenceAvailability
    value: NonNegativeCanonicalDecimal | None = None
    unit: str
    reason: str
    observation_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def honest(self) -> Self:
        if self.availability is EvidenceAvailability.AVAILABLE:
            if self.value is None or not self.observation_ids:
                raise ValueError("Available quality needs a value and canonical evidence")
        elif self.value is not None:
            raise ValueError("Unavailable evidence cannot carry a fabricated value")
        return self


class SfpQuality(CanonicalModel):
    level_importance: QualityComponent
    higher_timeframe_alignment: QualityComponent
    sweep_quality: QualityComponent
    reclaim_speed: QualityComponent
    rejection_strength: QualityComponent
    volume: QualityComponent
    available_target_space: QualityComponent
    market_regime: QualityComponent
    cvd: QualityComponent
    order_flow: QualityComponent
    open_interest: QualityComponent


class SfpDetection(CanonicalModel):
    setup_id: UUID
    event_id: UUID
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: BrainSetupState
    condition: SfpCondition
    direction: TradeDirection
    sweep: SweepEvent
    event_time: AwareDatetime
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    evidence: PublicMarketObservation
    provisional: bool
    reclaim_observation_id: UUID | None
    confirmation_observation_id: UUID | None
    quality: SfpQuality
    reason_codes: tuple[str, ...]


class SfpScan(CanonicalModel):
    events: tuple[SfpDetection, ...] = ()
    required_evidence: EvidenceAvailability
    reason_codes: tuple[str, ...] = ()
    paper_only: Literal[True] = True
