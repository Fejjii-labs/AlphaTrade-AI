"""Exchange account, watchlist, market snapshot, and indicator schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field

from app.schemas.common import (
    ExchangeAccountStatus,
    NonNegativeDecimal,
    ORMModel,
    StrategyId,
    StrictModel,
    Symbol,
    Timeframe,
)


class ExchangeAccount(ORMModel):
    """A linked exchange account. API keys are stored by reference only.

    The frontend never receives raw keys, and keys must not carry withdrawal
    permission (enforced at integration time, Architecture §12).
    """

    id: UUID
    organization_id: UUID
    user_id: UUID
    exchange: str
    api_key_ref: str = Field(description="Opaque reference to a secret store entry.")
    has_withdrawal_permission: bool = False
    status: ExchangeAccountStatus = ExchangeAccountStatus.ACTIVE
    created_at: datetime


class WatchlistItemCreate(StrictModel):
    """Request to add a market to a watchlist."""

    organization_id: UUID
    user_id: UUID
    symbol: Symbol
    exchange: str = Field(min_length=2, max_length=40)
    timeframes: list[Timeframe] = Field(min_length=1)
    strategy_ids: list[StrategyId] = Field(min_length=1)
    enabled: bool = True


class WatchlistItemUpdate(StrictModel):
    """Partial update for a watchlist item."""

    timeframes: list[Timeframe] | None = None
    strategy_ids: list[StrategyId] | None = None
    enabled: bool | None = None


class WatchlistItem(ORMModel):
    id: UUID
    organization_id: UUID
    user_id: UUID
    symbol: Symbol
    exchange: str
    timeframes: list[Timeframe] = Field(default_factory=list)
    strategy_ids: list[StrategyId] = Field(default_factory=list)
    enabled: bool = True
    created_at: datetime


class MarketSnapshot(ORMModel):
    """Point-in-time market data for a symbol/timeframe."""

    symbol: Symbol
    timeframe: Timeframe
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: NonNegativeDecimal
    funding_rate: Decimal | None = None
    timestamp: datetime


class MarketDataMeta(ORMModel):
    """Transparency metadata for market data provenance."""

    symbol: Symbol
    exchange: str
    timeframe: Timeframe | None = None
    timestamp: datetime
    source: str
    is_live: bool
    is_stale: bool
    stale_reason: str | None = None
    provider_name: str
    fallback_used: bool
    retrieved_at: datetime
    cache_hit: bool = False


class TickerResponse(ORMModel):
    meta: MarketDataMeta
    last_price: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    volume_24h: Decimal | None = None
    change_24h_pct: float | None = None


class OHLCVBarSchema(ORMModel):
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timestamp: datetime


class OHLCVResponse(ORMModel):
    meta: MarketDataMeta
    bars: list[OHLCVBarSchema]


class MarketSnapshotResponse(ORMModel):
    """Combined ticker + latest OHLCV bar + indicators."""

    meta: MarketDataMeta
    ticker: TickerResponse | None = None
    latest_bar: OHLCVBarSchema | None = None
    indicators: IndicatorContext | None = None
    funding_rate: Decimal | None = None
    funding_rate_available: bool = True
    # Funding rate is a mock fallback value (non-ban futures error, e.g. 451 region).
    # Kept separate from meta.fallback_used so spot-live analyses aren't labelled mock.
    funding_fallback: bool = False
    open_interest_available: bool = True
    unavailable_reason: str | None = None


class MarketAnalyzeRequest(StrictModel):
    symbol: Symbol
    exchange: str = Field(default="binance", min_length=2, max_length=40)
    timeframe: Timeframe = Timeframe.H1
    strategy_ids: list[StrategyId] = Field(default_factory=lambda: [StrategyId.HTF_TREND_PULLBACK])


class StrategySignalSummary(ORMModel):
    strategy_id: StrategyId
    direction: str | None = None
    confidence: float | None = None
    evidence: list[str] = Field(default_factory=list)
    data_quality_note: str | None = None


class MarketAnalyzeResponse(ORMModel):
    snapshot: MarketSnapshotResponse
    indicators: IndicatorContext
    strategy_signals: list[StrategySignalSummary] = Field(default_factory=list)
    data_quality: str = Field(description="live | mock | stale | missing")
    confidence_penalty_applied: bool = False


class IndicatorContext(ORMModel):
    """Derived indicator values used by strategy modules.

    All fields optional: a provider may supply a subset, and strategy modules
    must handle missing indicators gracefully.
    """

    symbol: Symbol
    timeframe: Timeframe
    rsi: float | None = Field(default=None, ge=0, le=100)
    vwap: Decimal | None = None
    ema_fast: Decimal | None = None
    ema_slow: Decimal | None = None
    macd: float | None = None
    macd_signal: float | None = None
    atr: Decimal | None = None
    volatility: float | None = Field(default=None, ge=0)
    volume_trend: float | None = None
    funding_rate: Decimal | None = None
    timestamp: datetime
