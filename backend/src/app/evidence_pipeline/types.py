"""Typed envelopes for assembled first-slice perpetual evidence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from app.market_contracts.cvd import CvdWindow
from app.market_contracts.enums import DataCompleteness, Finality, FreshnessState, SourceFamily
from app.market_contracts.flow import SignedQuoteFlow
from app.market_contracts.freshness import FreshnessEvaluation
from app.market_contracts.identity import EvidenceMarketIdentity
from app.market_contracts.models import CanonicalModel, PositiveCanonicalDecimal
from app.market_contracts.ohlcv import ClosedOhlcvSeries, OhlcvBar
from app.signal_fusion.adapters import AssessmentCommand
from app.signal_fusion.evidence_window import CanonicalEvidenceWindowV1
from app.signal_fusion.first_slice_types import FirstSliceEvidenceBundle


class CurrentPricePresentation(StrEnum):
    LIVE_MARK = "live_mark"
    REPLAY_FIXTURE = "replay_fixture"
    STALE = "stale"
    INCOMPLETE = "incomplete"
    DEGRADED = "degraded"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    SPOT_REJECTED = "spot_rejected"
    WRONG_SOURCE = "wrong_source"
    WRONG_INSTRUMENT = "wrong_instrument"
    UNAVAILABLE = "unavailable"


class CompletenessReport(CanonicalModel):
    ohlcv_15m: DataCompleteness
    ohlcv_4h: DataCompleteness
    cvd: DataCompleteness
    signed_flow: DataCompleteness
    coverage_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    cvd_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    signed_flow_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class EvidenceClockReport(CanonicalModel):
    """Independent freshness/finality clocks. Quote age is never widened to pass.

    Quote freshness, market-stream freshness, closed-evidence validity, and
    setup lifetime are separate. Elapsed wall time without later final bars
    does not expire a setup.
    """

    evidence_cutoff_at: datetime | None = None
    acquisition_completed_at: datetime | None = None
    quote_source_time: datetime | None = None
    quote_fresh: bool = False
    trade_stream_event_time_max: datetime | None = None
    market_stream_fresh: bool = False
    live_confirmation_window_open: bool
    trigger_finality: Finality
    trigger_interval_end: datetime
    historical_closed_evidence: bool
    closed_evidence_valid: bool = False
    subsequent_final_15m_count: int = Field(ge=0)
    setup_expired: bool = False
    setup_lifetime_remaining_bars: int = Field(default=0, ge=0)
    setup_trigger_bar_hash: str | None = None


class CurrentPriceQuote(CanonicalModel):
    """Last contracted perpetual trade. Never a mock hash or demo seed."""

    price: PositiveCanonicalDecimal
    source_time: datetime
    venue_trade_id: str = Field(min_length=1, max_length=120)
    freshness: FreshnessEvaluation
    usable_as_current_market_price: bool
    presentation: CurrentPricePresentation
    is_live: bool
    is_mock: bool
    fallback_used: bool = False
    provider_name: str = Field(min_length=3, max_length=80)
    source_family: SourceFamily
    instrument_id: str = Field(min_length=8, max_length=80)
    provider_symbol: str = Field(min_length=2, max_length=32)


class AssembledCanonicalEvidence(CanonicalModel):
    """One fail-closed first-slice assembly. Identity is CanonicalEvidenceWindowV1."""

    organization_id: UUID
    replay: bool
    evaluated_at: datetime
    identity: EvidenceMarketIdentity
    trigger_bar: OhlcvBar
    context_bar: OhlcvBar | None
    series_15m: ClosedOhlcvSeries
    series_4h: ClosedOhlcvSeries | None
    cvd: CvdWindow | None
    signed_flow: SignedQuoteFlow | None
    current_price: CurrentPriceQuote | None
    completeness: CompletenessReport
    freshness_state: FreshnessState
    clocks: EvidenceClockReport
    bundle: FirstSliceEvidenceBundle
    assessment_command: AssessmentCommand
    evidence_window: CanonicalEvidenceWindowV1
    evidence_window_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    connection_id: UUID
    evaluation_mark: Decimal
