"""Side-aware internal paper fill price. No I/O and no trading authority."""

from __future__ import annotations

from decimal import Decimal

# Production quotes are the last trade, so the mid path is the one that runs.
# Non-majors pay 5bp. BTC and ETH pay 1bp. A caller-supplied book still wins.
DEFAULT_PAPER_FILL_SPREAD_HAIRCUT = Decimal("0.0005")
DEFAULT_PAPER_FILL_MAJOR_SPREAD_HAIRCUT = Decimal("0.0001")
PAPER_FILL_MAJOR_BASES = frozenset({"BTC", "ETH"})
_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "USD")
_CONTRACT_SUFFIXES = ("SWAP", "PERP")


def paper_fill_base_asset(symbol: str) -> str:
    """Base asset of a perpetual symbol. ``ETHFI`` does not match ``ETH``."""

    compact = "".join(character for character in symbol.upper() if character.isalnum())
    for suffix in _CONTRACT_SUFFIXES:
        if compact.endswith(suffix) and len(compact) > len(suffix):
            compact = compact[: -len(suffix)]
            break
    for suffix in _QUOTE_SUFFIXES:
        if compact.endswith(suffix) and len(compact) > len(suffix):
            return compact[: -len(suffix)]
    return compact


def paper_fill_spread_haircut_for_symbol(
    symbol: str | None,
    *,
    major_haircut: Decimal = DEFAULT_PAPER_FILL_MAJOR_SPREAD_HAIRCUT,
    default_haircut: Decimal = DEFAULT_PAPER_FILL_SPREAD_HAIRCUT,
) -> Decimal:
    """Adverse mid haircut for one symbol. Missing symbols use the wider default."""

    if not isinstance(symbol, str) or not symbol.strip():
        return default_haircut
    if paper_fill_base_asset(symbol) in PAPER_FILL_MAJOR_BASES:
        return major_haircut
    return default_haircut


def _positive_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        return None
    if not value.is_finite() or value <= 0:
        return None
    return value


def paper_fill_price(
    quote: object | None,
    *,
    is_long: bool,
    spread_haircut: Decimal,
) -> Decimal | None:
    """Adverse fill from a live quote, or None when the quote cannot be used.

    A two-sided quote fills a long at the ask and a short at the bid. A quote
    that only has a mid (``price``) moves against the trader by
    ``spread_haircut``. One usable side is used when it is the side being
    lifted or hit. A missing quote, a crossed book, or a non-positive result
    returns None so the caller can fail closed.
    """

    if quote is None or not isinstance(spread_haircut, Decimal) or not spread_haircut.is_finite():
        return None
    if spread_haircut < 0:
        return None
    bid = _positive_decimal(getattr(quote, "bid", None))
    ask = _positive_decimal(getattr(quote, "ask", None))
    if bid is not None and ask is not None:
        if bid > ask:
            return None
        return ask if is_long else bid
    side = ask if is_long else bid
    if side is not None:
        return side
    mid = _positive_decimal(getattr(quote, "price", None))
    if mid is None:
        return None
    factor = Decimal(1) + spread_haircut if is_long else Decimal(1) - spread_haircut
    filled = mid * factor
    if not filled.is_finite() or filled <= 0:
        return None
    return filled
