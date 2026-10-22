"""Side-aware internal paper fill price. No I/O and no trading authority."""

from __future__ import annotations

from decimal import Decimal

# Half of a 2bp spread. Settings.paper_fill_spread_haircut defaults to this.
DEFAULT_PAPER_FILL_SPREAD_HAIRCUT = Decimal("0.0001")


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
