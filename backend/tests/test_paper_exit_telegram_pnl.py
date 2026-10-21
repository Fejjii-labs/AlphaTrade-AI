"""Paper exit Telegram realized P&L formatting."""

from __future__ import annotations

from decimal import Decimal

from app.schemas.common import TradeDirection
from app.services.paper_exit_telegram import (
    format_paper_exit_close_telegram_text,
    format_realized_pnl_display,
    realized_pnl_amount,
    realized_pnl_display_for_trade,
)


def _trade_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "BTCUSDT",
        "direction": TradeDirection.LONG,
        "timeframe": "15m",
        "entry_price": Decimal("100"),
        "exit_price": Decimal("90"),
        "size": Decimal("1"),
        "fees": Decimal("0.10"),
        "funding": Decimal("0"),
        "slippage": Decimal("0"),
    }
    base.update(overrides)
    return base


class _TradeStub:
    def __init__(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_long_stop_loss_includes_realized_pnl_next_to_r() -> None:
    pnl = realized_pnl_amount(
        direction=TradeDirection.LONG,
        entry_price=Decimal("100"),
        exit_price=Decimal("90"),
        size=Decimal("2"),
        fees=Decimal("0.50"),
        funding=Decimal("0"),
        slippage=Decimal("0"),
    )
    assert pnl == Decimal("-20.50")
    display = format_realized_pnl_display(pnl, quote_currency="USDT")
    text = format_paper_exit_close_telegram_text(
        symbol="BTCUSDT",
        direction=TradeDirection.LONG,
        r_multiple="-1.00",
        timeframe="15m",
        exit_reason="stop_hit",
        pnl_display=display,
    )
    assert "Outcome: stop-out" in text
    assert "R: -1.00 (-20.50 USDT)" in text
    assert "Entry:" not in text
    assert "Exit:" not in text


def test_short_take_profit_includes_realized_pnl_next_to_r() -> None:
    pnl = realized_pnl_amount(
        direction=TradeDirection.SHORT,
        entry_price=Decimal("200"),
        exit_price=Decimal("180"),
        size=Decimal("0.5"),
        fees=Decimal("1"),
        funding=Decimal("0"),
        slippage=Decimal("0"),
    )
    assert pnl == Decimal("9.00")
    display = format_realized_pnl_display(
        pnl,
        quote_currency="USDT",
        account_equity=Decimal("10000"),
    )
    assert display == "+9.00 USDT, +0.09% of account"
    text = format_paper_exit_close_telegram_text(
        symbol="ETHUSDT",
        direction=TradeDirection.SHORT,
        r_multiple="2.00",
        timeframe="1h",
        exit_reason="target_hit",
        pnl_display=display,
    )
    assert "Outcome: take-profit" in text
    assert "R: 2.00 (+9.00 USDT, +0.09% of account)" in text


def test_missing_size_omits_pnl_display() -> None:
    trade = _TradeStub(**_trade_kwargs(size=None))
    assert realized_pnl_display_for_trade(trade) is None
    text = format_paper_exit_close_telegram_text(
        symbol="BTCUSDT",
        direction=TradeDirection.LONG,
        r_multiple="-1.00",
        timeframe="15m",
        exit_reason="stop_hit",
        pnl_display=realized_pnl_display_for_trade(trade),
    )
    assert text.endswith("R: -1.00")
    assert "R: -1.00 (" not in text


def test_gross_pnl_when_fees_not_recorded() -> None:
    pnl = realized_pnl_amount(
        direction=TradeDirection.LONG,
        entry_price=Decimal("50"),
        exit_price=Decimal("55"),
        size=Decimal("3"),
        fees=None,
        funding=None,
        slippage=None,
    )
    assert pnl == Decimal("15.00")
