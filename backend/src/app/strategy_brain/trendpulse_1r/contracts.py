"""Immutable conservative TrendPulse1R v1 definitions and research output."""

from datetime import datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.market_contracts.identity import EvidenceMarketIdentity
from app.market_contracts.models import CanonicalModel, PositiveCanonicalDecimal
from app.market_contracts.observation import PublicMarketObservation
from app.market_contracts.ohlcv import OhlcvBar
from app.schemas.common import Timeframe, TradeDirection
from app.schemas.trade_plan import InstrumentRules
from app.services.canonical_serialization import canonical_sha256

TRENDPULSE_KIND = "trendpulse_1r/v1"
TRENDPULSE_ADAPTER_VERSION: Literal["trendpulse-1r-research/v2"] = "trendpulse-1r-research/v2"
TRENDPULSE_NAMESPACE = UUID("edec0001-0000-4000-8000-000000000001")
DECIMAL_PRECISION = 80


class TrendPulseParameters(CanonicalModel):
    """Fixed baseline. Changing any rule requires a separately reviewed version."""

    version: Literal["trendpulse-1r-research/v1"] = "trendpulse-1r-research/v1"
    provisional: Literal[True] = True
    fast_ema_period: Literal[20] = 20
    slow_ema_period: Literal[50] = 50
    trend_history_bars: Literal[250] = 250
    entry_history_bars: Literal[60] = 60
    slope_bars: Literal[3] = 3
    minimum_slope_ratio: PositiveCanonicalDecimal = Decimal("0.001")
    structure_lookback: Literal[32] = 32
    pivot_width: Literal[2] = 2
    pullback_bars: Literal[3] = 3
    pullback_tolerance: PositiveCanonicalDecimal = Decimal("0.001")
    maximum_pullback_penetration: PositiveCanonicalDecimal = Decimal("0.003")
    stop_buffer_ticks: Literal[1] = 1
    minimum_risk_ratio: PositiveCanonicalDecimal = Decimal("0.001")
    maximum_risk_ratio: PositiveCanonicalDecimal = Decimal("0.02")
    maximum_entry_rounding_ratio: PositiveCanonicalDecimal = Decimal("0.001")
    maximum_trigger_open_gap_ratio: PositiveCanonicalDecimal = Decimal("0.001")
    expiry_seconds: Literal[60] = 60
    trigger: Literal["closed_break_of_pullback_extreme"] = "closed_break_of_pullback_extreme"

    @model_validator(mode="after")
    def fixed_rules(self) -> Self:
        fixed = {
            "minimum_slope_ratio": "0.001",
            "pullback_tolerance": "0.001",
            "maximum_pullback_penetration": "0.003",
            "minimum_risk_ratio": "0.001",
            "maximum_risk_ratio": "0.02",
            "maximum_entry_rounding_ratio": "0.001",
            "maximum_trigger_open_gap_ratio": "0.001",
        }
        if any(getattr(self, key) != Decimal(value) for key, value in fixed.items()):
            raise ValueError("TrendPulse1R v1 rules are fixed; changes need a new rule version")
        return self


class TrendPulseSpec(CanonicalModel):
    spec_version: Literal["strategy-pattern-spec/v1"] = "strategy-pattern-spec/v1"
    kind: Literal["trendpulse_1r/v1"] = "trendpulse_1r/v1"
    name: str = "TrendPulse1R conservative research v1"
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,32}$")
    trend_timeframe: Literal[Timeframe.M15] = Timeframe.M15
    trigger_timeframe: Literal[Timeframe.M5] = Timeframe.M5
    direction: TradeDirection
    parameters: TrendPulseParameters = Field(default_factory=TrendPulseParameters)
    paper_only: Literal[True] = True


class TrendPulseStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    REFUSED = "refused"
    NO_SETUP = "no_setup"
    QUALIFIED = "qualified_research_signal"
    DUPLICATE = "duplicate"


class TrendPulseEvidenceReference(CanonicalModel):
    timeframe: Timeframe
    observation_id: UUID
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    available_at: AwareDatetime


class TrendPulseSignal(CanonicalModel):
    """Closed-candle research geometry, never a Candidate or executable TradePlan."""

    adapter_version: Literal["trendpulse-1r-research/v2"] = TRENDPULSE_ADAPTER_VERSION
    signal_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    instrument_rules_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity: EvidenceMarketIdentity
    direction: TradeDirection
    trigger_event_id: str
    trigger_end: AwareDatetime
    known_at: AwareDatetime
    decision_at: AwareDatetime
    expires_at: AwareDatetime
    trend_end: AwareDatetime
    trend_ema20: PositiveCanonicalDecimal
    trend_ema50: PositiveCanonicalDecimal
    ema50_slope_ratio: Decimal
    structure_anchor_ids: tuple[str, str, str, str]
    entry_ema20: PositiveCanonicalDecimal
    pullback_event_ids: tuple[str, str, str]
    structural_extreme: PositiveCanonicalDecimal
    entry: PositiveCanonicalDecimal
    structural_stop: PositiveCanonicalDecimal
    target: PositiveCanonicalDecimal
    gross_reward_risk: Literal[1] = 1
    evidence: tuple[TrendPulseEvidenceReference, ...]
    execution_authorized: Literal[False] = False
    management_authority: Literal[False] = False
    runtime_activated: Literal[False] = False
    performance: None = None


class TrendPulseResult(CanonicalModel):
    status: TrendPulseStatus
    reason: str
    signal: TrendPulseSignal | None = None
    duplicate_signal_id: UUID | None = None
    runtime_activated: Literal[False] = False


def exact_hash(value: BaseModel | dict[str, Any]) -> str:
    """Independent of the caller's Decimal precision and rounding mode."""
    from decimal import ROUND_HALF_EVEN, Context

    with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
        return canonical_sha256(value)


def require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("TrendPulse1R requires timezone-aware decision timestamps")


class TrendPulseRequest(CanonicalModel):
    """Offline interface for later callers; no HTTP endpoint or provider fetch."""

    spec: TrendPulseSpec
    trend_bars: tuple[OhlcvBar, ...] = Field(max_length=1024)
    trend_observations: tuple[PublicMarketObservation, ...] = Field(max_length=1024)
    entry_bars: tuple[OhlcvBar, ...] = Field(max_length=1024)
    entry_observations: tuple[PublicMarketObservation, ...] = Field(max_length=1024)
    instrument_rules: InstrumentRules
    trigger_end: AwareDatetime
    evaluated_at: AwareDatetime
    seen_signal_ids: frozenset[UUID] = frozenset()
