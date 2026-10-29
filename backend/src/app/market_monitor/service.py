"""HTTP-facing perpetual market monitor. Does not start Watcher."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from app.core.config import Settings
from app.evidence_pipeline.http_schemas import (
    CanonicalCurrentPriceRead,
    CanonicalFreshnessRead,
    CanonicalSourceIdentityRead,
)
from app.evidence_pipeline.types import CurrentPricePresentation, CurrentPriceQuote
from app.market_activation.profile import market_activation_public
from app.market_contracts.freshness import first_slice_freshness_policy
from app.market_contracts.identity import ADAPTER_VERSION
from app.market_monitor.http_schemas import (
    MarketMonitorActivationRead,
    MarketMonitorBackoffRead,
    MarketMonitorCoverageRead,
    MarketMonitorCvdRead,
    MarketMonitorOhlcvRead,
    MarketMonitorProviderRead,
    MarketMonitorStatusRead,
    MarketMonitorStreamRead,
)
from app.market_monitor.types import MarketAvailability, SymbolMonitorSnapshot


class MarketMonitorReader(Protocol):
    """In-process monitor (replay/LOCAL) or the worker-persisted reader."""

    def snapshot(self, symbol: str = "BTCUSDT") -> SymbolMonitorSnapshot: ...


class PerpetualMarketMonitorService:
    def __init__(self, monitor: MarketMonitorReader, *, settings: Settings) -> None:
        self._monitor = monitor
        self._settings = settings

    def read(self, *, symbol: str = "BTCUSDT") -> MarketMonitorStatusRead:
        snapshot = self._monitor.snapshot(symbol)
        return status_from_snapshot(snapshot, settings=self._settings)


def status_from_snapshot(
    snapshot: SymbolMonitorSnapshot,
    *,
    settings: Settings,
) -> MarketMonitorStatusRead:
    price = _price_read(snapshot)
    unavailable = None
    non_fresh = snapshot.availability in {
        MarketAvailability.UNAVAILABLE,
        MarketAvailability.STALE,
        MarketAvailability.REPLAY,
        MarketAvailability.DEGRADED,
    }
    if non_fresh and not price.usable_as_current_market_price:
        unavailable = snapshot.reason.value
    return MarketMonitorStatusRead(
        symbol=snapshot.symbol,
        mode=snapshot.mode.value,
        availability=snapshot.availability.value,
        reason=snapshot.reason.value,
        source=CanonicalSourceIdentityRead(
            venue="binance",
            market_type="perpetual",
            instrument_id=snapshot.instrument_id,
            provider_symbol=snapshot.provider_symbol,
            provider_name=snapshot.provider_name,
            source_family=snapshot.source_family.value,
            adapter_version=ADAPTER_VERSION,
            is_live=snapshot.is_live,
            is_mock=snapshot.is_mock,
            fallback_used=False,
        ),
        current_price=price,
        last_update=snapshot.last_update,
        evaluated_at=snapshot.evaluated_at,
        stream=MarketMonitorStreamRead(
            reconnect_state=snapshot.stream.reconnect_state.value,
            gap_state=snapshot.stream.gap_state.value,
            warm_up_status=snapshot.stream.warm_up_status.value,
            last_sequence=snapshot.stream.last_sequence,
            last_event_id=snapshot.stream.last_event_id,
            last_event_at=snapshot.stream.last_event_at,
            reconnect_count=snapshot.stream.reconnect_count,
        ),
        coverage=MarketMonitorCoverageRead(
            completeness=snapshot.coverage.completeness.value,
            gap_state=snapshot.coverage.gap_state.value,
            content_hash=snapshot.coverage.content_hash,
            first_trade_id=snapshot.coverage.first_trade_id,
            last_trade_id=snapshot.coverage.last_trade_id,
        ),
        cvd=MarketMonitorCvdRead(
            available=snapshot.cvd.available,
            signed_quote_delta=snapshot.cvd.signed_quote_delta,
            total_quote_volume=snapshot.cvd.total_quote_volume,
            signed_flow_ratio=snapshot.cvd.signed_flow_ratio,
            event_count=snapshot.cvd.event_count,
            event_set_hash=snapshot.cvd.event_set_hash,
            reason=snapshot.cvd.reason,
        ),
        ohlcv=MarketMonitorOhlcvRead(
            available=snapshot.ohlcv.available,
            completeness_15m=snapshot.ohlcv.completeness_15m.value,
            completeness_4h=snapshot.ohlcv.completeness_4h.value,
            latest_15m_close=snapshot.ohlcv.latest_15m_close,
            latest_15m_end=snapshot.ohlcv.latest_15m_end,
            reason=snapshot.ohlcv.reason,
        ),
        provider=MarketMonitorProviderRead(
            name=snapshot.provider.name,
            health=snapshot.provider.health.value,
            is_mock=snapshot.provider.is_mock,
            using_fallback=False,
            detail=snapshot.provider.detail,
        ),
        backoff=MarketMonitorBackoffRead(
            active=snapshot.backoff.active,
            attempt=snapshot.backoff.attempt,
            next_retry_at=snapshot.backoff.next_retry_at,
            last_error_class=snapshot.backoff.last_error_class,
        ),
        content_hash=snapshot.content_hash,
        unavailable_reason=unavailable,
        activation=MarketMonitorActivationRead.model_validate(market_activation_public(settings)),
    )


def _price_read(snapshot: SymbolMonitorSnapshot) -> CanonicalCurrentPriceRead:
    quote = snapshot.current_price
    if quote is None:
        return _empty_price(snapshot)
    return _price_from_quote(quote)


def _price_from_quote(quote: CurrentPriceQuote) -> CanonicalCurrentPriceRead:
    return CanonicalCurrentPriceRead(
        usable_as_current_market_price=quote.usable_as_current_market_price,
        presentation=quote.presentation.value,
        price=str(quote.price),
        source_time=quote.source_time,
        venue_trade_id=quote.venue_trade_id,
        is_live=quote.is_live,
        is_mock=quote.is_mock,
        fallback_used=False,
        freshness=CanonicalFreshnessRead(
            policy_version=quote.freshness.policy_version,
            state=quote.freshness.state.value,
            evaluated_at=quote.freshness.evaluated_at,
            source_time=quote.freshness.source_time,
            age_seconds=str(quote.freshness.age_seconds),
            valid_until=quote.freshness.valid_until,
        ),
    )


def _empty_price(snapshot: SymbolMonitorSnapshot) -> CanonicalCurrentPriceRead:
    policy = first_slice_freshness_policy()
    presentation = CurrentPricePresentation.UNAVAILABLE.value
    if snapshot.availability is MarketAvailability.STALE:
        presentation = CurrentPricePresentation.STALE.value
    elif snapshot.availability is MarketAvailability.REPLAY:
        presentation = CurrentPricePresentation.REPLAY_FIXTURE.value
    elif snapshot.availability is MarketAvailability.DEGRADED:
        presentation = CurrentPricePresentation.DEGRADED.value
    elif snapshot.reason.value == "provider_unavailable":
        presentation = CurrentPricePresentation.PROVIDER_UNAVAILABLE.value
    elif snapshot.reason.value == "spot_rejected":
        presentation = CurrentPricePresentation.SPOT_REJECTED.value
    elif snapshot.reason.value == "symbol_mismatch":
        presentation = CurrentPricePresentation.WRONG_INSTRUMENT.value
    elif snapshot.reason.value == "wrong_source":
        presentation = CurrentPricePresentation.WRONG_SOURCE.value
    elif snapshot.reason.value == "provider_error":
        presentation = CurrentPricePresentation.PROVIDER_UNAVAILABLE.value
    return CanonicalCurrentPriceRead(
        usable_as_current_market_price=False,
        presentation=presentation,
        price=None,
        source_time=None,
        venue_trade_id=None,
        is_live=snapshot.is_live,
        is_mock=snapshot.is_mock,
        fallback_used=False,
        freshness=CanonicalFreshnessRead(
            policy_version=policy.policy_version,
            state="unknown",
            evaluated_at=(
                snapshot.evaluated_at if snapshot.evaluated_at.tzinfo else datetime.now(UTC)
            ),
        ),
    )
