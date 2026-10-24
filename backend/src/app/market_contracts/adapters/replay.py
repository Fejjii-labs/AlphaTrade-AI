"""Replay perpetual source: identical results without network calls."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.market_contracts.coverage import build_complete_trade_window_coverage
from app.market_contracts.cursor import TradeStreamSnapshot
from app.market_contracts.cvd import select_trades_in_window
from app.market_contracts.derivatives import (
    DerivativeMetric,
    DerivativeObservation,
    derivative_observation,
)
from app.market_contracts.enums import SourceFamily
from app.market_contracts.errors import (
    FormingCandleError,
    UnsupportedTradeContractError,
    WrongMarketError,
    WrongSourceError,
)
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import (
    EvidenceMarketIdentity,
    InstrumentIdentity,
    require_instrument,
    require_perpetual,
)
from app.market_contracts.ohlcv import ClosedOhlcvSeries, OhlcvBar, require_closed_series
from app.market_contracts.replay_fixtures import canonical_first_slice_fixture
from app.market_contracts.trades import OrderedTradeBatch, TradeEvent, order_trades
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.schemas.common import Timeframe
from app.schemas.nested_continuation import EvidenceAvailability


class ReplayPerpetualSource:
    """Deterministic in-process source backed by the canonical first-slice fixture."""

    name = "binance-usdm-perpetual-replay"
    kind = ProviderKind.MARKET_DATA

    def fetch_order_flow_snapshot(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        start: datetime,
        end: datetime,
        source_connection_id: UUID,
        receive_at: datetime,
    ) -> TradeStreamSnapshot:
        raise UnsupportedTradeContractError(
            "Replay fixture has no live five-minute trade contract."
        )

    def fetch_derivative_observation(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        metric: DerivativeMetric,
        observed_at: datetime,
    ) -> DerivativeObservation:
        require_perpetual(identity)
        require_instrument(identity, instrument)
        return derivative_observation(
            identity=identity,
            metric=metric,
            observed_at=observed_at,
            availability=EvidenceAvailability.UNSUPPORTED,
            reason="replay_fixture_has_no_verified_oi_or_funding",
        )

    def __init__(
        self,
        *,
        bars_15m: list[OhlcvBar] | None = None,
        bars_4h: list[OhlcvBar] | None = None,
        trades: list[TradeEvent] | None = None,
        evaluated_at: datetime | None = None,
    ) -> None:
        fixture = canonical_first_slice_fixture(evaluated_at=evaluated_at)
        self._evaluated_at = evaluated_at or fixture["evaluated_at"]
        self._bars_15m = bars_15m or list(fixture["bars_15m"])
        self._bars_4h = bars_4h or list(fixture["bars_4h"])
        self._trades = trades or list(fixture["trades"])

    def fetch_closed_ohlcv(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        timeframe: Timeframe,
        min_final_bars: int,
        evaluated_at: datetime,
        use_public_kline_cache: bool = True,
    ) -> ClosedOhlcvSeries:
        del use_public_kline_cache  # Replay reads the fixture, not the public cache.
        self._assert_identity(identity, instrument)
        bars = self._bars_for(timeframe)
        if not bars:
            raise FormingCandleError(f"Replay fixture has no {timeframe.value} bars.")
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
        selected = select_trades_in_window(self._trades, start=start, end=end)
        retagged: list[TradeEvent] = []
        for trade in selected:
            updated = trade.model_copy(
                update={
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
            is_mock=True,
            detail="Replay fixtures for Binance USD-M BTCUSDT perpetual evidence (not live).",
            last_success_at=None,
            error_message=None,
        )

    def _bars_for(self, timeframe: Timeframe) -> list[OhlcvBar]:
        if timeframe is Timeframe.M15:
            return self._bars_15m
        if timeframe is Timeframe.H4:
            return self._bars_4h
        raise WrongMarketError(f"Replay fixture does not include timeframe {timeframe.value}.")

    def _assert_identity(
        self,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
    ) -> None:
        require_perpetual(identity)
        require_instrument(identity, instrument)
        source = identity.source
        provenance = identity.provenance
        if (
            source.family is not SourceFamily.REPLAY_FIXTURE
            or source.provider_name != self.name
            or provenance.source_family is not SourceFamily.REPLAY_FIXTURE
            or provenance.provider_name != self.name
            or provenance.is_live
            or not provenance.is_mock
        ):
            raise WrongSourceError(
                "Replay evidence identity must exactly identify the replay provider."
            )
        if identity.provenance.fallback_used:
            raise WrongMarketError("Replay evidence cannot record a fallback.")


def replay_identity(timeframe: Timeframe) -> EvidenceMarketIdentity:
    return first_slice_identity(timeframe=timeframe, replay=True, is_live=False)
