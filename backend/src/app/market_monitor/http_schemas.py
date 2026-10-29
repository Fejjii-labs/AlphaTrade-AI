"""Read-only HTTP contracts for continuous perpetual market status."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.evidence_pipeline.http_schemas import (
    CanonicalCurrentPriceRead,
    CanonicalSourceIdentityRead,
)
from app.schemas.common import StrictModel


class MarketMonitorStreamRead(StrictModel):
    reconnect_state: str
    gap_state: str
    warm_up_status: str
    last_sequence: int | None = None
    last_event_id: str | None = None
    last_event_at: datetime | None = None
    reconnect_count: int = Field(ge=0)


class MarketMonitorCoverageRead(StrictModel):
    completeness: str
    gap_state: str
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    first_trade_id: str | None = None
    last_trade_id: str | None = None


class MarketMonitorCvdRead(StrictModel):
    available: bool
    signed_quote_delta: str | None = None
    total_quote_volume: str | None = None
    signed_flow_ratio: str | None = None
    event_count: int = Field(default=0, ge=0)
    event_set_hash: str | None = Field(default=None, min_length=64, max_length=64)
    reason: str | None = None


class MarketMonitorOhlcvRead(StrictModel):
    available: bool
    completeness_15m: str
    completeness_4h: str
    latest_15m_close: str | None = None
    latest_15m_end: datetime | None = None
    reason: str | None = None


class MarketMonitorProviderRead(StrictModel):
    name: str
    health: str
    is_mock: bool
    using_fallback: Literal[False] | bool = False
    detail: str | None = None


class MarketMonitorBackoffRead(StrictModel):
    active: bool
    attempt: int = Field(ge=0)
    next_retry_at: datetime | None = None
    last_error_class: str | None = None


class MarketMonitorActivationRead(StrictModel):
    """Configured evidence source. Does not start Watcher or trading."""

    state: Literal["inactive", "active", "refused"]
    configured_source: Literal["replay", "binance_usdm", "bybit_usdt_perpetual"]
    intended_staging_source: Literal["binance_usdm"] = "binance_usdm"
    rollback_source: Literal["replay"] = "replay"
    read_only: Literal[True] = True
    exchange_credentials_used: Literal[False] = False
    spot_fallback_permitted: Literal[False] = False
    fabricated_fallback_permitted: Literal[False] = False
    first_symbol: Literal["BTCUSDT"] = "BTCUSDT"
    trade_freshness_seconds: Literal[10] = 10


class MarketMonitorStatusRead(StrictModel):
    authority: Literal["canonical_market_monitor"] = "canonical_market_monitor"
    live_executable: Literal[False] = False
    watcher_activated: Literal[False] = False
    compatibility_price_used: Literal[False] = False
    symbol: str
    mode: str
    availability: str
    reason: str
    perpetual: Literal[True] = True
    source: CanonicalSourceIdentityRead
    current_price: CanonicalCurrentPriceRead
    last_update: datetime | None
    evaluated_at: datetime
    stream: MarketMonitorStreamRead
    coverage: MarketMonitorCoverageRead
    cvd: MarketMonitorCvdRead
    ohlcv: MarketMonitorOhlcvRead
    provider: MarketMonitorProviderRead
    backoff: MarketMonitorBackoffRead
    content_hash: str = Field(min_length=64, max_length=64)
    unavailable_reason: str | None = None
    activation: MarketMonitorActivationRead
    # Worker-persisted row write time and age on the API clock. Null when served in-process.
    written_at: datetime | None = None
    age_seconds: float | None = None
