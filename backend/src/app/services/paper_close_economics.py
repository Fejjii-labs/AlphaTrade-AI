"""Deterministic paper-close PnL. No market data and no exchange calls."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from app.core.errors import ValidationAppError
from app.schemas.common import TradeDirection, TradeResult

_QUANT = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class PaperCloseEconomics:
    """Gross and net PnL for one already-filled paper trade."""

    gross_pnl: Decimal
    net_pnl: Decimal
    result: TradeResult


def paper_close_pnl(
    *,
    direction: TradeDirection,
    entry_price: Decimal,
    exit_price: Decimal,
    size: Decimal,
    fees: Decimal,
    funding: Decimal,
    slippage: Decimal,
) -> PaperCloseEconomics:
    """Compute paper PnL from recorded entry, explicit exit, and recorded costs.

    Long PnL is ``(exit - entry) * size``. Short PnL flips the price delta.
    Net PnL subtracts fees, funding, and slippage. The result follows the sign
    of net PnL. Missing or non-finite inputs fail closed.
    """

    entry = _positive_money(entry_price, field="entry_price")
    exit_ = _positive_money(exit_price, field="exit_price")
    quantity = _positive_money(size, field="size")
    fee_cost = _non_negative_money(fees, field="fees")
    funding_cost = _non_negative_money(funding, field="funding")
    slippage_cost = _non_negative_money(slippage, field="slippage")
    delta = exit_ - entry
    if direction is TradeDirection.SHORT:
        delta = -delta
    elif direction is not TradeDirection.LONG:
        raise ValidationAppError(
            "Paper close requires a long or short direction.",
            details={"reason": "invalid_direction"},
        )
    gross = _money(delta * quantity)
    net = _money(gross - fee_cost - funding_cost - slippage_cost)
    return PaperCloseEconomics(gross_pnl=gross, net_pnl=net, result=_result_for(net))


@dataclass(frozen=True, slots=True)
class ExitFillInput:
    """One partial exit with the costs already attributed to it."""

    quantity: Decimal
    price: Decimal
    fees: Decimal = Decimal("0")
    funding: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")


def allocate_by_quantity(total: Decimal, quantities: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """Split a cost pro rata. Each share rounds down; the last takes the remainder.

    The shares always sum to ``total`` exactly.
    """

    amount = _non_negative_money(total, field="total")
    size = sum(quantities, Decimal("0"))
    if not quantities or size <= 0 or any(quantity <= 0 for quantity in quantities):
        raise ValidationAppError(
            "Cost allocation needs positive quantities.",
            details={"reason": "invalid_allocation"},
        )
    shares = [
        (amount * quantity / size).quantize(_QUANT, rounding=ROUND_FLOOR)
        for quantity in quantities[:-1]
    ]
    shares.append(amount - sum(shares, Decimal("0")))
    return tuple(shares)


def paper_close_pnl_from_fills(
    *,
    direction: TradeDirection,
    entry_price: Decimal,
    size: Decimal,
    fills: Sequence[ExitFillInput],
) -> tuple[PaperCloseEconomics, tuple[PaperCloseEconomics, ...]]:
    """Total and per-fill PnL. The total is the exact sum of the fills.

    Fill quantities must sum to the recorded size, so a partial set cannot
    close the trade and an overlapping set cannot close it twice.
    """

    quantity = _positive_money(size, field="size")
    if not fills or sum((fill.quantity for fill in fills), Decimal("0")) != quantity:
        raise ValidationAppError(
            "Exit fills must add up to the recorded position size.",
            details={"reason": "exit_fills_do_not_match_size"},
        )
    per_fill = tuple(
        paper_close_pnl(
            direction=direction,
            entry_price=entry_price,
            exit_price=fill.price,
            size=fill.quantity,
            fees=fill.fees,
            funding=fill.funding,
            slippage=fill.slippage,
        )
        for fill in fills
    )
    gross = sum((row.gross_pnl for row in per_fill), Decimal("0"))
    net = sum((row.net_pnl for row in per_fill), Decimal("0"))
    total = PaperCloseEconomics(
        gross_pnl=_money(gross), net_pnl=_money(net), result=_result_for(net)
    )
    return total, per_fill


def _result_for(net_pnl: Decimal) -> TradeResult:
    if net_pnl > 0:
        return TradeResult.WIN
    if net_pnl < 0:
        return TradeResult.LOSS
    return TradeResult.BREAKEVEN


def _money(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationAppError(
            "Paper close monetary value must be a finite decimal.",
            details={"reason": "invalid_money"},
        )
    return value.quantize(_QUANT)


def _positive_money(value: Decimal, *, field: str) -> Decimal:
    amount = _money(value)
    if amount <= 0:
        raise ValidationAppError(
            "Paper close price and size must be positive.",
            details={"reason": "non_positive_money", "field": field},
        )
    return amount


def _non_negative_money(value: Decimal, *, field: str) -> Decimal:
    amount = _money(value)
    if amount < 0:
        raise ValidationAppError(
            "Paper close costs cannot be negative.",
            details={"reason": "negative_cost", "field": field},
        )
    return amount
