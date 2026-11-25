"""Shared builders for Phase 5 market-contract tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from app.market_contracts.adapters.binance_usdm import BINANCE_REST_FAST_READ_GRACE
from app.market_contracts.coverage import build_complete_trade_window_coverage
from app.market_contracts.cursor import TradeStreamAssembler, TradeStreamSnapshot
from app.market_contracts.enums import MarketType, ProductFamily, SourceFamily, VenueId
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.freshness import first_slice_freshness_policy
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import (
    ADAPTER_VERSION,
    EvidenceMarketIdentity,
    InstrumentIdentity,
    ProviderProvenance,
    SourceIdentity,
    binance_usdm_btcusdt,
    canonical_instrument_id,
    interval_timedelta,
)
from app.market_contracts.ohlcv import OhlcvBar, build_ohlcv_bar
from app.market_contracts.trades import OrderedTradeBatch, TradeEvent, build_trade_event
from app.schemas.common import Timeframe

EVALUATED_AT = datetime(2026, 1, 15, 16, 15, 5, tzinfo=UTC)
TRIGGER_OPEN = datetime(2026, 1, 15, 16, 0, tzinfo=UTC)
# Live USD-M fixtures evaluate at close+5 s (the v2 floor). Sources reading them
# keep that grace; v3's 60 s admission is tested in test_binance_candle_finalization.
RECORDED_FIXTURE_GRACE = BINANCE_REST_FAST_READ_GRACE
CONNECTION = UUID("11111111-2222-3333-4444-555555555555")


def identity(
    timeframe: Timeframe = Timeframe.M15, *, replay: bool = True
) -> EvidenceMarketIdentity:
    return first_slice_identity(timeframe=timeframe, replay=replay, is_live=False)


def closed_bar(
    *,
    open_time: datetime = TRIGGER_OPEN,
    timeframe: Timeframe = Timeframe.M15,
    index: int = 0,
    evaluated_at: datetime = EVALUATED_AT,
    instrument: InstrumentIdentity | None = None,
    revision: int = 1,
) -> OhlcvBar:
    open_ = Decimal("100000") + Decimal(index)
    close = open_ + Decimal("2")
    return build_ohlcv_bar(
        instrument=instrument or binance_usdm_btcusdt(),
        timeframe=timeframe,
        interval_start=open_time,
        open_=open_,
        high=close + Decimal("1"),
        low=open_ - Decimal("1"),
        close=close,
        base_volume=Decimal("10"),
        quote_volume=Decimal("1000000"),
        evaluated_at=evaluated_at,
        grace=timedelta(seconds=first_slice_freshness_policy().ohlcv_post_close_grace_seconds),
        trade_count=4,
        adapter_version=ADAPTER_VERSION,
        revision=revision,
    )


def consecutive_bars(count: int, *, last_open: datetime = TRIGGER_OPEN) -> list[OhlcvBar]:
    delta = interval_timedelta(Timeframe.M15)
    first_open = last_open - (delta * (count - 1))
    return [
        closed_bar(open_time=first_open + (delta * index), index=index) for index in range(count)
    ]


def trade(
    *,
    sequence: int,
    price: str,
    quantity: str,
    buyer_is_maker: bool,
    event_time: datetime,
    connection: UUID = CONNECTION,
    receive_at: datetime = EVALUATED_AT,
    instrument: InstrumentIdentity | None = None,
) -> TradeEvent:
    return build_trade_event(
        instrument=instrument or binance_usdm_btcusdt(),
        venue_trade_id=str(sequence),
        sequence=sequence,
        price=Decimal(price),
        quantity=Decimal(quantity),
        buyer_is_maker=buyer_is_maker,
        event_timestamp=event_time,
        receive_timestamp=receive_at,
        source_connection_id=connection,
        adapter_version=ADAPTER_VERSION,
    )


def spot_identity() -> EvidenceMarketIdentity:
    symbol = "BTCUSDT"
    instrument = InstrumentIdentity(
        venue=VenueId.BINANCE,
        market_type=MarketType.SPOT,
        product_family=ProductFamily.SPOT,
        contract_style=binance_usdm_btcusdt().contract_style,
        instrument_id=canonical_instrument_id(
            venue=VenueId.BINANCE,
            product_family=ProductFamily.SPOT,
            market_type=MarketType.SPOT,
            symbol=symbol,
        ),
        provider_symbol=symbol,
        base_asset="BTC",
        quote_asset="USDT",
        settlement_asset="USDT",
        contract_multiplier=Decimal("1"),
        price_unit="USDT",
        base_quantity_unit="BTC",
        quote_quantity_unit="USDT",
    )
    source = SourceIdentity(
        family=SourceFamily.BINANCE_USDM_FUTURES_PUBLIC,
        provider_name="binance-spot",
        adapter_version=ADAPTER_VERSION,
        aggressor_convention="none",
    )
    provenance = ProviderProvenance(
        provider_name="binance-spot",
        source_family=SourceFamily.BINANCE_USDM_FUTURES_PUBLIC,
        adapter_version=ADAPTER_VERSION,
        is_live=True,
        fallback_used=False,
        is_mock=False,
        detail="spot",
    )
    return EvidenceMarketIdentity(
        venue=VenueId.BINANCE,
        market_type=MarketType.SPOT,
        instrument=instrument,
        timeframe=Timeframe.M15,
        source=source,
        provenance=provenance,
    )


def eth_instrument() -> InstrumentIdentity:
    symbol = "ETHUSDT"
    return InstrumentIdentity(
        venue=VenueId.BINANCE,
        market_type=MarketType.PERPETUAL,
        product_family=ProductFamily.USDM_FUTURES,
        contract_style=binance_usdm_btcusdt().contract_style,
        instrument_id=canonical_instrument_id(
            venue=VenueId.BINANCE,
            product_family=ProductFamily.USDM_FUTURES,
            market_type=MarketType.PERPETUAL,
            symbol=symbol,
        ),
        provider_symbol=symbol,
        base_asset="ETH",
        quote_asset="USDT",
        settlement_asset="USDT",
        contract_multiplier=Decimal("1"),
        price_unit="USDT",
        base_quantity_unit="ETH",
        quote_quantity_unit="USDT",
    )


def new_connection() -> UUID:
    return uuid4()


def proven_snapshot(
    trades: list[TradeEvent],
    *,
    start: datetime,
    end: datetime,
    evidence_identity: EvidenceMarketIdentity | None = None,
    connection: UUID = CONNECTION,
    observed_at: datetime = EVALUATED_AT,
) -> TradeStreamSnapshot:
    """Build a retrieval-bound authoritative snapshot for behavior tests."""
    market_identity = evidence_identity or identity()
    coverage = build_complete_trade_window_coverage(
        identity=market_identity,
        lineage_id=connection,
        requested_start=start,
        requested_end=end,
        trades=trades,
    )
    batch = with_content_hash(
        OrderedTradeBatch(
            identity=market_identity,
            trades=trades,
            source_connection_id=connection,
            coverage=coverage,
            content_hash="0" * 64,
        )
    )
    assembler = TradeStreamAssembler(
        market_identity,
        connected_at=observed_at,
        connection_identity=connection,
        expected_contiguous_count=len(trades),
    )
    return assembler.ingest_batch(batch, observed_at=observed_at)
