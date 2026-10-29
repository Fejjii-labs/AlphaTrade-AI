"""Typed envelopes for the continuous perpetual market monitor."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from app.evidence_pipeline.types import CurrentPriceQuote
from app.market_contracts.enums import (
    DataCompleteness,
    GapState,
    ReconnectState,
    SourceFamily,
    WarmUpStatus,
)
from app.market_contracts.models import CanonicalModel
from app.providers.base import ProviderHealth


class MarketAvailability(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    REPLAY = "replay"


class MarketMode(StrEnum):
    LIVE_PERPETUAL = "live_perpetual"
    REPLAY = "replay"


class MonitorReason(StrEnum):
    OK = "ok"
    REPLAY_FIXTURE = "replay_fixture"
    STALE_STREAM = "stale_stream"
    RATE_LIMITED = "rate_limited"
    RECONNECTING = "reconnecting"
    GAP = "gap"
    UNRECOVERABLE_GAP = "unrecoverable_gap"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    SYMBOL_MISMATCH = "symbol_mismatch"
    WRONG_SOURCE = "wrong_source"
    SPOT_REJECTED = "spot_rejected"
    WARM_UP = "warm_up"
    INCOMPLETE = "incomplete"
    OUT_OF_ORDER = "out_of_order"
    DUPLICATE_CONFLICT = "duplicate_conflict"
    BACKOFF = "backoff"
    MONITOR_STATE_MISSING = "monitor_state_missing"
    MONITOR_STATE_STALE = "monitor_state_stale"


class StreamHealth(CanonicalModel):
    reconnect_state: ReconnectState
    gap_state: GapState
    warm_up_status: WarmUpStatus
    last_sequence: int | None = Field(default=None, ge=0)
    last_event_id: str | None = None
    last_event_at: datetime | None = None
    reconnect_count: int = Field(ge=0)
    connection_identity: UUID


class CoverageReport(CanonicalModel):
    completeness: DataCompleteness
    gap_state: GapState
    content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    first_trade_id: str | None = None
    last_trade_id: str | None = None


class StreamCvdReport(CanonicalModel):
    available: bool
    signed_quote_delta: str | None = None
    total_quote_volume: str | None = None
    signed_flow_ratio: str | None = None
    event_count: int = Field(default=0, ge=0)
    event_set_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason: str | None = None


class OhlcvHealth(CanonicalModel):
    available: bool
    completeness_15m: DataCompleteness
    completeness_4h: DataCompleteness
    latest_15m_close: str | None = None
    latest_15m_end: datetime | None = None
    # True only when the latest 15m bar is provider-complete and FINAL.
    # Series presence (including PARTIAL) is not candle finality.
    latest_15m_final: bool | None = None
    series_15m_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    series_4h_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason: str | None = None


class ProviderHealthReport(CanonicalModel):
    name: str
    health: ProviderHealth
    is_mock: bool
    using_fallback: bool = False
    detail: str | None = None


class BackoffReport(CanonicalModel):
    active: bool
    attempt: int = Field(ge=0)
    next_retry_at: datetime | None = None
    last_error_class: str | None = None


class SymbolMonitorSnapshot(CanonicalModel):
    """Honest per-symbol monitor projection. Transport fields are not semantic."""

    symbol: str
    mode: MarketMode
    availability: MarketAvailability
    reason: MonitorReason
    current_price: CurrentPriceQuote | None
    last_update: datetime | None
    evaluated_at: datetime
    instrument_id: str
    provider_symbol: str
    source_family: SourceFamily
    provider_name: str
    is_live: bool
    is_mock: bool
    fallback_used: bool = False
    perpetual: bool = True
    watcher_activated: bool = False
    live_executable: bool = False
    stream: StreamHealth
    coverage: CoverageReport
    cvd: StreamCvdReport
    ohlcv: OhlcvHealth
    provider: ProviderHealthReport
    backoff: BackoffReport
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
