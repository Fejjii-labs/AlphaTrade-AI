"""On-demand research screening and authenticated durable read contracts."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.market_contracts.models import CanonicalModel
from app.market_contracts.observation import PublicMarketObservation
from app.market_contracts.ohlcv import OhlcvBar
from app.schemas.trade_plan import InstrumentRules
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseStatus
from app.strategy_brain.trendpulse_1r.experiment import ExperimentBoundTrendPulse


class TrendPulseScreeningCreate(CanonicalModel):
    request_id: UUID
    variant_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    trigger_end: AwareDatetime


class TrendPulseScreeningEvidence(CanonicalModel):
    """Original canonical receipts, including partial acquisition on failure."""

    trend_bars: tuple[OhlcvBar, ...] = Field(default=(), max_length=252)
    trend_observations: tuple[PublicMarketObservation, ...] = Field(default=(), max_length=252)
    entry_bars: tuple[OhlcvBar, ...] = Field(default=(), max_length=62)
    entry_observations: tuple[PublicMarketObservation, ...] = Field(default=(), max_length=62)
    instrument_rules: InstrumentRules | None = None


class TrendPulseScreeningRecord(CanonicalModel):
    contract_version: Literal["trendpulse-screening/v1"] = "trendpulse-screening/v1"
    id: UUID
    request_id: UUID
    organization_id: UUID
    experiment_id: UUID
    experiment_version_id: UUID
    configuration_hash: str
    variant_key: str
    strategy_version_id: UUID
    strategy_content_hash: str
    trigger_end: AwareDatetime
    acquisition_started_at: AwareDatetime
    decision_at: AwareDatetime
    evidence_mode: Literal["public_rest", "replay"]
    receipt_provenance: Literal["live_public_rest", "recorded_public_receipts", "synthetic_fixture"]
    status: TrendPulseStatus
    reason: str
    trend_receipts: int
    entry_receipts: int
    evidence_hash: str
    signal_id: UUID | None = None
    duplicate_of: UUID | None = None
    execution_authorized: Literal[False] = False
    management_authority: Literal[False] = False
    sample_eligible: Literal[False] = False
    performance: None = None


class TrendPulseScreeningDetail(TrendPulseScreeningRecord):
    evidence: TrendPulseScreeningEvidence
    signal: ExperimentBoundTrendPulse | None = None


class TrendPulseScreeningPage(CanonicalModel):
    items: list[TrendPulseScreeningRecord]
    total: int
    limit: int
    offset: int


class ExperimentResearchTick(CanonicalModel):
    screenings: list[TrendPulseScreeningRecord] = Field(max_length=2)
    execution_requested: Literal[False] = False
    samples_admitted: Literal[0] = 0
