"""Versioned research inputs and immutable would-be candidates, never trade authority."""

from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.market_contracts.identity import InstrumentIdentity
from app.market_contracts.models import CanonicalModel, PositiveCanonicalDecimal
from app.schemas.common import Timeframe, TradeDirection

DLINE_KIND = "d_line/v1"


class DlineParameters(CanonicalModel):
    # All defaults are research hypotheses, not calibrated on the seven screenshots.
    version: Literal["dline-research/v1"] = "dline-research/v1"
    lookback: int = Field(default=96, ge=10, le=500)
    pivot_width: int = Field(default=2, ge=1, le=10)
    minimum_touches: int = Field(default=2, ge=2, le=3)
    preferred_touches: int = Field(default=3, ge=3, le=10)
    minimum_separation: int = Field(default=2, ge=1, le=20)
    touch_atr: Decimal = Field(default=Decimal(".15"), ge=0, le=1)
    # pending Sofien's refinement: ATR/bar proxy, never chart degrees.
    slope_min_atr: Decimal = Field(default=Decimal(".01"), ge=0, le=10)
    slope_max_atr: Decimal = Field(default=Decimal(".8"), gt=0, le=10)
    atr_period: int = Field(default=14, ge=2, le=100)
    break_buffer_atr: Decimal = Field(default=Decimal("0"), ge=0, le=2)
    stop_buffer_atr: Decimal = Field(default=Decimal(".1"), ge=0, le=5)
    minimum_stop_ticks: int = Field(default=1, ge=1, le=100)
    minimum_rr: Decimal = Field(default=Decimal("2"), ge=2, le=100)
    risk_fraction: Decimal = Field(default=Decimal(".01"), gt=0, le=Decimal(".03"))
    entry_mode: Literal["breakout", "retest"] = "breakout"
    # pending Sofien's refinement: fixed break projection, subsequent bars only.
    retest_window: int = Field(default=6, ge=1, le=100)
    retest_tolerance_atr: Decimal = Field(default=Decimal(".15"), ge=0, le=2)
    retest_policy: Literal["frozen_break_projection"] = "frozen_break_projection"
    # pending Sofien's refinement: no stop-out/fill claims in this detector.
    attempt_cap: int = Field(default=2, ge=1, le=10)
    # pending Sofien's refinement: spec's 50%, not the plan's proposed 40%.
    tp1_fraction: Decimal = Field(default=Decimal(".5"), gt=0, lt=1)
    runner_fraction: Decimal = Field(default=Decimal(".2"), ge=0, le=Decimal(".2"))
    # pending Sofien's refinement: cap disables runner on 5m by default.
    five_minute_ob_policy: Literal["cap", "allow_runner"] = "cap"
    # pending Sofien's refinement: full ranging exit must itself justify 2R.
    ranging_first_target_min_rr: Decimal = Field(default=Decimal("2"), gt=0, le=100)
    range_lookback: int = Field(default=14, ge=2, le=100)
    range_efficiency_threshold: Decimal = Field(default=Decimal(".2"), ge=0, le=1)
    htf_required: bool = True
    # pending Sofien's refinement: the 5m -> 1h EMA map is provisional.
    five_minute_htf: Timeframe = Timeframe.H1
    fifteen_minute_htf: Timeframe = Timeframe.H4
    hourly_htf: Timeframe = Timeframe.D1
    ema_period: int = Field(default=50, ge=2, le=200)
    ema_slope_bars: int = Field(default=3, ge=1, le=20)
    htf_max_age_bars: int = Field(default=2, ge=1, le=10)
    # pending Sofien's refinement: B-.9*(B-A), rather than A+.9*(B-A).
    fib_09_orientation: Literal["deep_retracement", "from_origin"] = "deep_retracement"
    fib_09_tolerance_atr: Decimal = Field(default=Decimal(".15"), ge=0, le=2)
    round_trip_cost_fraction: Decimal = Field(default=Decimal(".001"), ge=0, le=Decimal(".1"))

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.tp1_fraction + self.runner_fraction >= 1:
            raise ValueError("TP1 plus runner must leave a final scheduled target")
        if self.lookback < 2 * self.pivot_width + 3:
            raise ValueError("lookback must cover confirmed pivots")
        return self


class DlineSpec(CanonicalModel):
    spec_version: Literal["strategy-pattern-spec/v1"] = "strategy-pattern-spec/v1"
    kind: Literal["d_line/v1"] = DLINE_KIND
    name: str = "D-line draft research"
    symbol: Literal["BTCUSDT"] = "BTCUSDT"
    trigger_timeframe: Literal[Timeframe.M5, Timeframe.M15, Timeframe.H1] = Timeframe.M15
    direction: TradeDirection = TradeDirection.LONG
    parameters: DlineParameters = Field(default_factory=DlineParameters)
    paper_only: Literal[True] = True


class SourcedLevel(CanonicalModel):
    """Explicit manual/replay snapshot. v1 has no automated OB/POC provider."""

    price: PositiveCanonicalDecimal  # zone snapshots supply their near opposing edge
    kind: Literal["poc", "ob", "daily", "weekly"]
    symbol: str
    instrument: InstrumentIdentity
    timeframe: Timeframe
    source: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1, max_length=200)
    known_at: AwareDatetime
    expires_at: AwareDatetime
    consumed: bool = False

    @model_validator(mode="after")
    def provenance(self) -> Self:
        if self.expires_at <= self.known_at or self.instrument.provider_symbol != self.symbol:
            raise ValueError(
                "Sourced level requires matching instrument and a positive validity window"
            )
        return self


class FrozenLine(CanonicalModel):
    line_id: UUID
    anchor_indices: tuple[int, int]
    anchor_event_ids: tuple[str, str]
    touches: tuple[int, ...]
    slope: Decimal
    anchor_price: Decimal
    atr: PositiveCanonicalDecimal
    normalized_slope: Decimal
    frozen_at: AwareDatetime
    break_price: Decimal


class ResearchTarget(CanonicalModel):
    price: PositiveCanonicalDecimal
    fraction: Decimal = Field(ge=0, le=1)
    sources: tuple[str, ...]


class DlineCandidate(CanonicalModel):
    setup_id: UUID
    spec_hash: str
    line: FrozenLine
    attempt: int
    direction: TradeDirection
    timeframe: Timeframe
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    trigger_event_id: str
    entry: PositiveCanonicalDecimal
    entry_intent: Literal["next_open_reference", "retest_reference"]
    stop: PositiveCanonicalDecimal
    targets: tuple[ResearchTarget, ...]
    runner_fraction: Decimal
    gross_rr: Decimal
    net_rr: Decimal
    weighted_scheduled_r: Decimal
    aggressive_tp: bool
    confluences: dict[str, str]
    proofs: dict[str, tuple[str, ...]]
    unavailable: tuple[str, ...]
    quality_points: int
    parent_line_id: UUID | None = None
    continuation_number: int = 1
    state: Literal["CONFIRMED"] = "CONFIRMED"
    execution_permission: Literal["none"] = "none"
    lifecycle: Literal["draft"] = "draft"
    reason_codes: tuple[str, ...] = ("draft_research_only", "no_execution_capability")
