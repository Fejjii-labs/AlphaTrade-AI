"""Per-symbol tick size for live evidence bundles. Replay keeps the first-slice default."""

from __future__ import annotations

from decimal import Decimal

from app.market_contracts.enums import VenueId
from app.market_contracts.errors import MarketContractError
from app.market_contracts.provider_contracts import ContractBook, default_contract_book
from app.signal_fusion.first_slice_types import FIRST_SLICE_TICK_SIZE

INSTRUMENT_TICK_SIZE_UNAVAILABLE = "instrument_tick_size_unavailable"


def resolve_bundle_tick_size(
    *,
    symbol: str,
    venue: VenueId,
    book: ContractBook | None,
    replay: bool,
) -> Decimal:
    """Return the verified price tick for ``symbol`` on ``venue``.

    Replay fixtures keep ``FIRST_SLICE_TICK_SIZE``. Live assembly fails closed
    when the contract book does not name a positive tick for the symbol.
    """

    if replay:
        return FIRST_SLICE_TICK_SIZE
    tick = _verified_tick_from_book(book, symbol, venue)
    if tick is None:
        tick = _verified_tick_from_book(default_contract_book(), symbol, venue)
    if tick is None:
        raise MarketContractError(INSTRUMENT_TICK_SIZE_UNAVAILABLE)
    return tick


def _verified_tick_from_book(
    book: ContractBook | None,
    symbol: str,
    venue: VenueId,
) -> Decimal | None:
    if book is None:
        return None
    contract = book.contract_for(symbol, venue)
    if contract is None or not contract.tick_size:
        return None
    tick = Decimal(contract.tick_size)
    if tick <= 0:
        return None
    return tick
