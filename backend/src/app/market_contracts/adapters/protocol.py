"""Read-only perpetual market source protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.market_contracts.cursor import TradeStreamSnapshot
from app.market_contracts.derivatives import DerivativeMetric, DerivativeObservation
from app.market_contracts.identity import EvidenceMarketIdentity, InstrumentIdentity
from app.market_contracts.ohlcv import ClosedOhlcvSeries
from app.market_contracts.trades import OrderedTradeBatch
from app.providers.base import ProviderKind, ProviderStatus
from app.schemas.common import Timeframe


class PerpetualMarketSource(Protocol):
    """Read-only source of perpetual OHLCV, aggregate trades, OI and funding."""

    name: str
    kind: ProviderKind

    def fetch_order_flow_snapshot(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        start: datetime,
        end: datetime,
        source_connection_id: UUID,
        receive_at: datetime,
    ) -> TradeStreamSnapshot: ...

    def fetch_derivative_observation(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        metric: DerivativeMetric,
        observed_at: datetime,
    ) -> DerivativeObservation: ...

    def fetch_closed_ohlcv(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        timeframe: Timeframe,
        min_final_bars: int,
        evaluated_at: datetime,
        use_public_kline_cache: bool = True,
    ) -> ClosedOhlcvSeries: ...

    def fetch_ordered_trades(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        start: datetime,
        end: datetime,
        source_connection_id: UUID,
        receive_at: datetime,
    ) -> OrderedTradeBatch: ...

    def status(self) -> ProviderStatus: ...
