"""Live evidence bundles use verified per-symbol ticks; replay keeps the slice default."""

from decimal import Decimal

import pytest

from app.evidence_pipeline.instrument_tick import (
    INSTRUMENT_TICK_SIZE_UNAVAILABLE,
    resolve_bundle_tick_size,
)
from app.market_contracts.enums import VenueId
from app.market_contracts.errors import MarketContractError
from app.market_contracts.provider_contracts import ContractBook, default_contract_book
from app.signal_fusion.first_slice_types import FIRST_SLICE_TICK_SIZE


def test_replay_keeps_first_slice_tick_even_when_book_differs() -> None:
    book = default_contract_book()
    assert (
        resolve_bundle_tick_size(
            symbol="BTCUSDT",
            venue=VenueId.BINANCE,
            book=book,
            replay=True,
        )
        == FIRST_SLICE_TICK_SIZE
    )


def test_live_uses_verified_contract_tick_not_the_slice_default() -> None:
    book = default_contract_book()
    hype = resolve_bundle_tick_size(
        symbol="HYPEUSDT",
        venue=VenueId.BINANCE,
        book=book,
        replay=False,
    )
    assert hype == Decimal("0.00100")
    assert hype != FIRST_SLICE_TICK_SIZE


def test_live_fails_closed_when_tick_metadata_is_missing() -> None:
    # Listing-only books without ticks still resolve known symbols from verified defaults.
    assert resolve_bundle_tick_size(
        symbol="BTCUSDT",
        venue=VenueId.BINANCE,
        book=ContractBook(()),
        replay=False,
    ) == Decimal("0.10")
    with pytest.raises(MarketContractError, match=INSTRUMENT_TICK_SIZE_UNAVAILABLE):
        resolve_bundle_tick_size(
            symbol="UNKNOWNUSDT",
            venue=VenueId.BINANCE,
            book=default_contract_book(),
            replay=False,
        )
    with pytest.raises(MarketContractError, match=INSTRUMENT_TICK_SIZE_UNAVAILABLE):
        resolve_bundle_tick_size(
            symbol="UNKNOWNUSDT",
            venue=VenueId.BINANCE,
            book=None,
            replay=False,
        )


def test_symbol_market_factory_wires_contract_book_into_assembler() -> None:
    from types import SimpleNamespace

    from app.core.config import Settings
    from app.workers.watcher_market import SymbolMarketFactory

    settings = Settings(_env_file=None, perpetual_evidence_source="replay")
    factory = SymbolMarketFactory(settings)
    port = factory(None, SimpleNamespace(), "BTCUSDT")
    assert port._assembler._contract_book is factory._contract_book_for_assembler()
