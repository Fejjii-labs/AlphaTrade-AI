"""Read-only HTTP contracts for canonical perpetual evidence.

These wrap assembled USD-M evidence. They do not mint Candidates or prices.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.market_contracts.derivatives import DerivativeObservation
from app.market_contracts.order_book import OrderBookObservation
from app.market_contracts.order_flow import OrderFlowObservation
from app.schemas.common import StrictModel


class CanonicalSourceIdentityRead(StrictModel):
    venue: str
    market_type: str
    instrument_id: str
    provider_symbol: str
    provider_name: str
    source_family: str
    adapter_version: str
    is_live: bool
    is_mock: bool
    fallback_used: Literal[False] = False


class CanonicalFreshnessRead(StrictModel):
    policy_version: str
    state: str
    evaluated_at: datetime
    source_time: datetime | None = None
    age_seconds: str | None = None
    valid_until: datetime | None = None


class CanonicalCompletenessRead(StrictModel):
    ohlcv_15m: str
    ohlcv_4h: str
    cvd: str
    signed_flow: str
    coverage_content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    cvd_content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    signed_flow_content_hash: str | None = Field(default=None, min_length=64, max_length=64)


class CanonicalCurrentPriceRead(StrictModel):
    usable_as_current_market_price: bool
    presentation: str
    price: str | None = None
    source_time: datetime | None = None
    venue_trade_id: str | None = None
    is_live: bool
    is_mock: bool
    fallback_used: Literal[False] = False
    freshness: CanonicalFreshnessRead


class CanonicalSetupEvidenceRead(StrictModel):
    available: bool
    evidence_window_hash: str | None = Field(default=None, min_length=64, max_length=64)
    trigger_interval_start: datetime | None = None
    trigger_interval_end: datetime | None = None
    evaluated_at: datetime | None = None
    cvd_signed_quote_delta: str | None = None
    signed_flow_ratio: str | None = None
    completeness: CanonicalCompletenessRead
    reason: str | None = None


class CanonicalEvidenceRead(StrictModel):
    authority: Literal["canonical"] = "canonical"
    live_executable: Literal[False] = False
    watcher_activated: Literal[False] = False
    organization_id: str
    symbol: str
    source: CanonicalSourceIdentityRead
    current_price: CanonicalCurrentPriceRead
    setup_evidence: CanonicalSetupEvidenceRead
    timestamps: dict[str, datetime | None]
    unavailable_reason: str | None = None
    market_intelligence: tuple[DerivativeObservation, ...] = ()
    order_flow: OrderFlowObservation | None = None
    order_book: OrderBookObservation | None = None
