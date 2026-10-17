"""Primary/secondary perpetual evidence selection.

A failure of the primary source switches the whole active identity. It does
not copy a secondary price into a primary observation.
"""

from __future__ import annotations

from datetime import datetime
from typing import NoReturn
from uuid import UUID

from app.market_contracts.adapters.protocol import PerpetualMarketSource
from app.market_contracts.cursor import TradeStreamSnapshot
from app.market_contracts.derivatives import DerivativeMetric, DerivativeObservation
from app.market_contracts.errors import (
    EvidenceSourceSwitchRequiredError,
    RateLimitedError,
    RegionalProviderFailureError,
    UpstreamBanError,
    WrongSourceError,
)
from app.market_contracts.identity import EvidenceMarketIdentity, InstrumentIdentity
from app.market_contracts.ohlcv import ClosedOhlcvSeries
from app.market_contracts.order_book import OrderBookObservation, order_book_observation
from app.market_contracts.trade_reduction import build_released_trade_snapshot
from app.market_contracts.trades import OrderedTradeBatch
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.schemas.common import Timeframe
from app.schemas.nested_continuation import EvidenceAvailability

_SWITCH_ERRORS = (UpstreamBanError, RegionalProviderFailureError, RateLimitedError)


def _require_market_data_kind(primary_kind: ProviderKind, secondary_kind: ProviderKind) -> None:
    """Refuse a failover pair that is not one shared market-data capability."""

    if primary_kind is ProviderKind.MARKET_DATA and secondary_kind is ProviderKind.MARKET_DATA:
        return
    raise WrongSourceError(
        "Primary and secondary perpetual sources must both report the market_data provider kind."
    )


class FailoverPerpetualSource:
    """One active venue at a time. Secondary is used only after an explicit switch.

    ``kind`` is the provider capability of the active source, not the venue name.
    Perpetual evidence is market data. Construction refuses a pair unless both
    sources report :attr:`ProviderKind.MARKET_DATA`, so a switch cannot change
    the category the provider registry records at startup.
    """

    def __init__(
        self,
        primary: PerpetualMarketSource,
        secondary: PerpetualMarketSource,
        *,
        primary_instrument: InstrumentIdentity,
        secondary_instrument: InstrumentIdentity,
    ) -> None:
        if primary_instrument.instrument_id == secondary_instrument.instrument_id:
            raise WrongSourceError("Primary and secondary evidence must be different instruments.")
        if primary_instrument.venue is secondary_instrument.venue:
            raise WrongSourceError("Primary and secondary evidence must not share a venue.")
        _require_market_data_kind(primary.kind, secondary.kind)
        self._primary = primary
        self._secondary = secondary
        self._primary_instrument = primary_instrument
        self._secondary_instrument = secondary_instrument
        self._using_secondary = False
        self.name = primary.name
        self.kind = primary.kind

    @property
    def using_secondary(self) -> bool:
        return self._using_secondary

    @property
    def active_source(self) -> PerpetualMarketSource:
        if self._using_secondary:
            return self._secondary
        return self._primary

    def active_instrument(self) -> InstrumentIdentity:
        if self._using_secondary:
            return self._secondary_instrument
        return self._primary_instrument

    def fetch_derivative_observation(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        metric: DerivativeMetric,
        observed_at: datetime,
    ) -> DerivativeObservation:
        self._require_active_identity(identity, instrument)
        try:
            return self.active_source.fetch_derivative_observation(
                identity=identity,
                instrument=instrument,
                metric=metric,
                observed_at=observed_at,
            )
        except _SWITCH_ERRORS as exc:
            self._switch(exc)

    def fetch_closed_ohlcv(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        timeframe: Timeframe,
        min_final_bars: int,
        evaluated_at: datetime,
    ) -> ClosedOhlcvSeries:
        self._require_active_identity(identity, instrument)
        try:
            return self.active_source.fetch_closed_ohlcv(
                identity=identity,
                instrument=instrument,
                timeframe=timeframe,
                min_final_bars=min_final_bars,
                evaluated_at=evaluated_at,
            )
        except _SWITCH_ERRORS as exc:
            self._switch(exc)

    def fetch_order_book_observation(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        observed_at: datetime,
    ) -> OrderBookObservation:
        self._require_active_identity(identity, instrument)
        fetch = getattr(self.active_source, "fetch_order_book_observation", None)
        if not callable(fetch):
            return order_book_observation(
                identity=identity,
                observed_at=observed_at,
                availability=EvidenceAvailability.UNSUPPORTED,
                reason="provider_has_no_resting_book_contract",
            )
        try:
            item = fetch(identity=identity, instrument=instrument, observed_at=observed_at)
            if not isinstance(item, OrderBookObservation):
                raise WrongSourceError("Active source returned an invalid resting snapshot.")
            return item
        except _SWITCH_ERRORS as exc:
            self._switch(exc)

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
        self._require_active_identity(identity, instrument)
        try:
            return self.active_source.fetch_ordered_trades(
                identity=identity,
                instrument=instrument,
                start=start,
                end=end,
                source_connection_id=source_connection_id,
                receive_at=receive_at,
            )
        except _SWITCH_ERRORS as exc:
            self._switch(exc)

    def reduce_ordered_trades(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        start: datetime,
        end: datetime,
        source_connection_id: UUID,
        receive_at: datetime,
    ) -> TradeStreamSnapshot:
        """Reduce on the active source. A source without reduce is released after fetch."""

        self._require_active_identity(identity, instrument)
        try:
            reduce = getattr(self.active_source, "reduce_ordered_trades", None)
            if callable(reduce):
                reduced = reduce(
                    identity=identity,
                    instrument=instrument,
                    start=start,
                    end=end,
                    source_connection_id=source_connection_id,
                    receive_at=receive_at,
                )
                if not isinstance(reduced, TradeStreamSnapshot):
                    raise WrongSourceError("Trade reduction did not return a snapshot.")
                return reduced
            batch = self.active_source.fetch_ordered_trades(
                identity=identity,
                instrument=instrument,
                start=start,
                end=end,
                source_connection_id=source_connection_id,
                receive_at=receive_at,
            )
            return build_released_trade_snapshot(
                batch.trades,
                identity=identity,
                lineage_id=source_connection_id,
                window_start=start,
                window_end=end,
                observed_at=receive_at,
            )
        except _SWITCH_ERRORS as exc:
            self._switch(exc)

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
        self._require_active_identity(identity, instrument)
        try:
            fetch = getattr(self.active_source, "fetch_order_flow_snapshot", None)
            if not callable(fetch):
                raise WrongSourceError("Active provider has no verified order-flow contract.")
            snapshot = fetch(
                identity=identity,
                instrument=instrument,
                start=start,
                end=end,
                source_connection_id=source_connection_id,
                receive_at=receive_at,
            )
            if not isinstance(snapshot, TradeStreamSnapshot):
                raise WrongSourceError("Active source returned an invalid order-flow snapshot.")
            return snapshot
        except _SWITCH_ERRORS as exc:
            self._switch(exc)

    def status(self) -> ProviderStatus:
        return self.active_source.status()

    def try_recover_primary(self) -> InstrumentIdentity | None:
        """Return the primary instrument when it is healthy again. Do not publish yet."""

        if not self._using_secondary:
            return None
        report = self._primary.status()
        if report.health is not ProviderHealth.HEALTHY or report.using_fallback or report.is_mock:
            return None
        self._using_secondary = False
        self.name = self._primary.name
        self.kind = self._primary.kind
        return self._primary_instrument

    def _require_active_identity(
        self,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
    ) -> None:
        active = self.active_instrument()
        if identity.venue is active.venue and instrument.instrument_id == active.instrument_id:
            return
        if self._using_secondary:
            raise EvidenceSourceSwitchRequiredError(
                "Previous venue identity cannot label the secondary source.",
                instrument=active,
            )
        raise WrongSourceError("Evidence identity does not match the active perpetual source.")

    def _switch(self, exc: Exception) -> NoReturn:
        if self._using_secondary:
            raise exc
        self._using_secondary = True
        self.name = self._secondary.name
        self.kind = self._secondary.kind
        raise EvidenceSourceSwitchRequiredError(
            "Primary perpetual source failed. The primary payload was not published.",
            instrument=self._secondary_instrument,
        ) from exc
