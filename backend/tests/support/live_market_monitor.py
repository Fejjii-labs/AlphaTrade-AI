"""Scripted perpetual source for AT-069 monitor tests. No network."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.market_contracts.coverage import build_complete_trade_window_coverage
from app.market_contracts.cvd import select_trades_in_window
from app.market_contracts.enums import SourceFamily
from app.market_contracts.errors import (
    FormingCandleError,
    MarketContractError,
    WrongInstrumentError,
    WrongMarketError,
    WrongSourceError,
)
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import (
    ADAPTER_VERSION,
    BINANCE_REST_ADAPTER_VERSION,
    EvidenceMarketIdentity,
    InstrumentIdentity,
    require_instrument,
    require_perpetual,
)
from app.market_contracts.ohlcv import ClosedOhlcvSeries, OhlcvBar, require_closed_series
from app.market_contracts.trades import OrderedTradeBatch, TradeEvent, order_trades
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.schemas.common import Timeframe
from tests.support.phase5_market import binance_usdm_btcusdt


class ScriptedPerpetualSource:
    """Queue of trade batches or fail-closed errors."""

    def __init__(
        self,
        *,
        replay: bool = False,
        instrument: InstrumentIdentity | None = None,
        bars_15m: list[OhlcvBar] | None = None,
        bars_4h: list[OhlcvBar] | None = None,
    ) -> None:
        self._replay = replay
        self.name = "binance-usdm-perpetual-replay" if replay else "binance-usdm-perpetual"
        self.kind = ProviderKind.MARKET_DATA
        self._instrument = instrument if instrument is not None else binance_usdm_btcusdt()
        self._queue: list[list[TradeEvent] | MarketContractError] = []
        self._bars_15m = list(bars_15m or [])
        self._bars_4h = list(bars_4h or [])
        self.fetch_count = 0
        self.last_error: str | None = None
        # The monitor reads a long catch-up window in contiguous spans. Like a
        # real venue, every span of one tick answers from the same queued tape.
        self._span_item: list[TradeEvent] | None = None
        self._span_end: datetime | None = None

    def enqueue(self, item: list[TradeEvent] | MarketContractError) -> None:
        self._queue.append(item)

    def fetch_closed_ohlcv(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        timeframe: Timeframe,
        min_final_bars: int,
        evaluated_at: datetime,
    ) -> ClosedOhlcvSeries:
        self._assert_identity(identity, instrument)
        bars = self._bars_15m if timeframe is Timeframe.M15 else self._bars_4h
        # Shared synthetic generators retain replay v1. A scripted live source
        # emits the current REST bundle version; unknown versions stay untouched
        # so real version-mismatch refusals remain testable.
        if not self._replay and identity.source.adapter_version == BINANCE_REST_ADAPTER_VERSION:
            bars = [
                with_content_hash(
                    bar.model_copy(update={"adapter_version": BINANCE_REST_ADAPTER_VERSION})
                )
                if bar.adapter_version == ADAPTER_VERSION
                else bar
                for bar in bars
            ]
        if timeframe not in {Timeframe.M15, Timeframe.H4}:
            raise WrongMarketError(f"Unsupported timeframe {timeframe.value}.")
        if len(bars) < min_final_bars:
            raise FormingCandleError(
                f"Need {min_final_bars} final {timeframe.value} candles; received {len(bars)}."
            )
        return require_closed_series(
            bars,
            identity=identity,
            timeframe=timeframe,
            evaluated_at=evaluated_at,
            min_bars=min_final_bars,
        )

    def fetch_ordered_trades(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        start: datetime,
        end: datetime,
        source_connection_id: UUID,
        receive_at: datetime,
    ) -> OrderedTradeBatch:
        self._assert_identity(identity, instrument)
        if end <= start:
            raise WrongMarketError("Trade window end must be after start.")
        if self._span_end is not None and start == self._span_end:
            item = self._span_item
        else:
            self.fetch_count += 1
            item = self._queue.pop(0) if self._queue else None
        self._span_item, self._span_end = None, None
        if isinstance(item, MarketContractError):
            self.last_error = type(item).__name__
            raise item
        raw = [] if item is None else select_trades_in_window(item, start=start, end=end)
        self._span_item, self._span_end = item, end
        retagged: list[TradeEvent] = []
        for trade in raw:
            adapter_version = trade.adapter_version
            if not self._replay and adapter_version == ADAPTER_VERSION:
                adapter_version = BINANCE_REST_ADAPTER_VERSION
            updated = trade.model_copy(
                update={
                    "adapter_version": adapter_version,
                    "source_connection_id": source_connection_id,
                    "receive_timestamp": receive_at,
                }
            )
            retagged.append(
                with_content_hash(updated, extra_exclude=frozenset({"source_connection_id"}))
            )
        ordered = order_trades(retagged)
        coverage = build_complete_trade_window_coverage(
            identity=identity,
            lineage_id=source_connection_id,
            requested_start=start,
            requested_end=end,
            trades=ordered,
        )
        batch = OrderedTradeBatch(
            identity=identity,
            trades=ordered,
            source_connection_id=source_connection_id,
            coverage=coverage,
            content_hash="0" * 64,
        )
        return with_content_hash(batch)

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            kind=self.kind,
            health=ProviderHealth.HEALTHY,
            using_fallback=False,
            is_mock=self._replay,
            detail="Scripted USD-M perpetual source for monitor tests.",
            last_success_at=None,
            error_message=self.last_error,
        )

    def _assert_identity(
        self, identity: EvidenceMarketIdentity, instrument: InstrumentIdentity
    ) -> None:
        require_perpetual(identity)
        require_instrument(identity, instrument)
        if instrument.instrument_id != self._instrument.instrument_id:
            raise WrongInstrumentError("Scripted source instrument does not match the request.")
        source = identity.source
        provenance = identity.provenance
        if self._replay:
            if (
                source.family is not SourceFamily.REPLAY_FIXTURE
                or provenance.is_live
                or not provenance.is_mock
            ):
                raise WrongSourceError("Replay scripted source refuses a live identity.")
            return
        if (
            source.family is not SourceFamily.BINANCE_USDM_FUTURES_PUBLIC
            or not provenance.is_live
            or provenance.is_mock
            or provenance.fallback_used
        ):
            raise WrongSourceError("Live scripted source refuses a replay or fallback identity.")
