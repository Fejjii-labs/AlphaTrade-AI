"""First-slice evaluation inputs. Not a second identity model.

``ExecutableSetupRef`` / ``CompiledSetupDefinition`` remain the setup identity.
These types only carry Phase 5 payloads the frozen ``AssessmentCommand``
envelopes do not embed. First-slice numeric constants are the compatibility
adapter defaults for the canonical authored spec; product evaluation binds
thresholds from the compiled spec via ``FirstSliceEvaluationParams``.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import AwareDatetime, Field

from app.market_contracts.cursor import TradeStreamSnapshot
from app.market_contracts.derivatives import DerivativeObservation
from app.market_contracts.enums import MarketType, VenueId
from app.market_contracts.models import CanonicalModel, PositiveCanonicalDecimal
from app.market_contracts.ohlcv import OhlcvBar
from app.market_contracts.order_flow import OrderFlowObservation
from app.schemas.common import Timeframe
from app.signal_fusion.types import ManualLevelRevisionRef
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseRequest

FIRST_SLICE_TICK_SIZE = Decimal("0.10")
FIRST_SLICE_SWING_ATR_DISTANCE = Decimal("0.50")
FIRST_SLICE_SWEEP_ATR = Decimal("0.25")
FIRST_SLICE_VOLUME_LOOKBACK = 20
FIRST_SLICE_VOLUME_RATIO = Decimal("1.50")
FIRST_SLICE_SELL_IMBALANCE = Decimal("-0.10")
FIRST_SLICE_INVALIDATION_ATR = Decimal("0.10")
FIRST_SLICE_INVALIDATION_TICKS = Decimal("2")
FIRST_SLICE_EXPIRY_BARS = 2

FIRST_SLICE_RULE_IDS: tuple[str, ...] = (
    "policy_compatible",
    "market_identity",
    "perpetual_identity",
    "final_evidence",
    "freshness",
    "no_unresolved_gap",
    "complete_warmup",
    "wilder_atr_15m",
    "wilder_atr_4h",
    "confirmed_swing_high",
    "manual_4h_resistance",
    "htf_resistance_context",
    "ltf_liquidity_sweep",
    "volume_spike",
    "bearish_cvd_divergence",
    "aggressive_sell_imbalance",
    "not_invalidated",
    "not_expired",
)

QUALITY_RULE_IDS: tuple[str, ...] = (
    "policy_compatible",
    "market_identity",
    "perpetual_identity",
    "final_evidence",
    "freshness",
    "no_unresolved_gap",
    "complete_warmup",
    "wilder_atr_15m",
    "wilder_atr_4h",
)

_EVAL_RULE_COUNT = len(FIRST_SLICE_RULE_IDS)
FIRST_SLICE_RULE_WEIGHT = Decimal("1") / Decimal(_EVAL_RULE_COUNT)


class ManualResistanceEvidence(CanonicalModel):
    """Versioned 4h resistance payload matching ``ManualLevelRevisionRef``."""

    ref: ManualLevelRevisionRef
    price: PositiveCanonicalDecimal
    timeframe: Timeframe
    effective_at: AwareDatetime
    valid: bool
    venue: VenueId
    market_type: MarketType
    instrument_id: str = Field(min_length=8, max_length=80)


class FirstSliceEvidenceBundle(CanonicalModel):
    """Canonical first-slice market payloads consumed with ``AssessmentCommand``."""

    bars_15m: tuple[OhlcvBar, ...] = ()
    bars_4h: tuple[OhlcvBar, ...] = ()
    snapshot: TradeStreamSnapshot | None = None
    resistances: tuple[ManualResistanceEvidence, ...] = ()
    subsequent_final_15m: tuple[OhlcvBar, ...] = ()
    tick_size: PositiveCanonicalDecimal = FIRST_SLICE_TICK_SIZE
    market_intelligence: tuple[DerivativeObservation, ...] = ()
    order_flow: OrderFlowObservation | None = None
    trendpulse: TrendPulseRequest | None = None
