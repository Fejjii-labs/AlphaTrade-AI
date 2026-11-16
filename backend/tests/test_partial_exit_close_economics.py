"""Per-fill fees and PnL reconcile to the position total (Griller condition 6)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationAppError
from app.schemas.common import TradeDirection, TradeResult
from app.services.paper_close_economics import (
    ExitFillInput,
    allocate_by_quantity,
    paper_close_pnl,
    paper_close_pnl_from_fills,
)


def test_cost_allocation_sums_exactly_and_the_last_fill_takes_the_remainder() -> None:
    shares = allocate_by_quantity(
        Decimal("1.00000001"),
        (Decimal("0.333"), Decimal("0.333"), Decimal("0.334")),
    )
    assert sum(shares) == Decimal("1.00000001")
    assert shares[0] == shares[1]
    assert all(share >= 0 for share in shares)


def test_fills_reconcile_to_the_position_total() -> None:
    fills = (
        ExitFillInput(
            quantity=Decimal("0.5"),
            price=Decimal("110"),
            fees=Decimal("0.11"),
            funding=Decimal("0.01"),
            slippage=Decimal("0.02"),
        ),
        ExitFillInput(
            quantity=Decimal("0.3"),
            price=Decimal("120"),
            fees=Decimal("0.07"),
            funding=Decimal("0"),
            slippage=Decimal("0.01"),
        ),
        ExitFillInput(
            quantity=Decimal("0.2"),
            price=Decimal("100.3"),
            fees=Decimal("0.05"),
            funding=Decimal("0"),
            slippage=Decimal("0"),
        ),
    )
    total, per_fill = paper_close_pnl_from_fills(
        direction=TradeDirection.LONG,
        entry_price=Decimal("100"),
        size=Decimal("1.0"),
        fills=fills,
    )
    assert [row.gross_pnl for row in per_fill] == [
        Decimal("5.00000000"),
        Decimal("6.00000000"),
        Decimal("0.06000000"),
    ]
    assert total.gross_pnl == sum(row.gross_pnl for row in per_fill) == Decimal("11.06000000")
    assert total.net_pnl == sum(row.net_pnl for row in per_fill)
    assert total.net_pnl == Decimal("11.06") - Decimal("0.27")
    assert total.result is TradeResult.WIN


def test_short_fills_reconcile_and_match_the_single_close_formula() -> None:
    fills = (
        ExitFillInput(quantity=Decimal("0.4"), price=Decimal("90"), fees=Decimal("0.1")),
        ExitFillInput(quantity=Decimal("0.6"), price=Decimal("90"), fees=Decimal("0.2")),
    )
    total, _ = paper_close_pnl_from_fills(
        direction=TradeDirection.SHORT,
        entry_price=Decimal("100"),
        size=Decimal("1"),
        fills=fills,
    )
    single = paper_close_pnl(
        direction=TradeDirection.SHORT,
        entry_price=Decimal("100"),
        exit_price=Decimal("90"),
        size=Decimal("1"),
        fees=Decimal("0.3"),
        funding=Decimal("0"),
        slippage=Decimal("0"),
    )
    assert total == single


def test_fills_that_do_not_cover_the_position_are_refused() -> None:
    with pytest.raises(ValidationAppError) as raised:
        paper_close_pnl_from_fills(
            direction=TradeDirection.LONG,
            entry_price=Decimal("100"),
            size=Decimal("1"),
            fills=(ExitFillInput(quantity=Decimal("0.5"), price=Decimal("110")),),
        )
    assert raised.value.details["reason"] == "exit_fills_do_not_match_size"
    with pytest.raises(ValidationAppError):
        paper_close_pnl_from_fills(
            direction=TradeDirection.LONG,
            entry_price=Decimal("100"),
            size=Decimal("1"),
            fills=(
                ExitFillInput(quantity=Decimal("0.7"), price=Decimal("110")),
                ExitFillInput(quantity=Decimal("0.7"), price=Decimal("110")),
            ),
        )
