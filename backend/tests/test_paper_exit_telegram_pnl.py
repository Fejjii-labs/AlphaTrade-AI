"""Paper exit Telegram realized P&L formatting."""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import JournalTrade
from app.schemas.common import TradeDirection, TradeResult
from app.services import paper_exit_telegram as paper_exit_telegram_module
from app.services.paper_exit_telegram import (
    format_paper_exit_close_telegram_text,
    format_realized_pnl_display,
    queue_paper_exit_close_telegram,
    realized_pnl_display_for_trade,
)
from tests.support.postgres_persistence import requires_postgres
from tests.test_paper_exit_close_concurrency_postgres import (
    _close_labeled,
    _factory,
    _loss_exit,
    _open_fill,
)


class _TradeStub:
    def __init__(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_long_stop_loss_includes_stored_net_pnl_next_to_r() -> None:
    display = format_realized_pnl_display(Decimal("-20.50"), quote_currency="USDT")
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
    assert "% of account" not in text


def test_short_take_profit_includes_stored_net_pnl_next_to_r() -> None:
    trade = _TradeStub(
        symbol="ETHUSDT",
        direction=TradeDirection.SHORT,
        net_pnl=Decimal("9.00"),
    )
    display = realized_pnl_display_for_trade(trade)
    assert display == "+9.00 USDT"
    text = format_paper_exit_close_telegram_text(
        symbol="ETHUSDT",
        direction=TradeDirection.SHORT,
        r_multiple="2.00",
        timeframe="1h",
        exit_reason="target_hit",
        pnl_display=display,
    )
    assert "Outcome: take-profit" in text
    assert "R: 2.00 (+9.00 USDT)" in text
    assert "%" not in text.split("R:")[-1]


def test_missing_net_pnl_omits_pnl_display() -> None:
    trade = _TradeStub(symbol="BTCUSDT", net_pnl=None)
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


def test_non_finite_net_pnl_omits_pnl_display() -> None:
    trade = _TradeStub(symbol="BTCUSDT", net_pnl=Decimal("NaN"))
    assert realized_pnl_display_for_trade(trade) is None


def test_queue_builds_alert_without_session_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MagicMock()
    session.flush.side_effect = AssertionError("session flush during alert formatting")
    monkeypatch.setattr(
        "app.services.paper_exit_telegram.queue_safety_alert",
        lambda *_args, **_kwargs: None,
    )
    trade = _TradeStub(
        id=uuid.uuid4(),
        symbol="BTCUSDT",
        direction=TradeDirection.LONG,
        timeframe="15m",
        entry_price=Decimal("100"),
        exit_price=Decimal("90"),
        planned_stop_price=Decimal("90"),
        planned_risk_amount=Decimal("10"),
        net_pnl=Decimal("-10"),
        exit_reason="stop_hit",
        result=TradeResult.LOSS,
    )
    settings = Settings(_env_file=None)
    queue_paper_exit_close_telegram(session, settings, trade, replayed=False)  # type: ignore[arg-type]
    session.flush.assert_not_called()


@requires_postgres
def test_postgres_close_commits_when_telegram_omits_pnl(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _factory()
    envelope, command_id = _open_fill(factory, key="exit-telegram-pnl-omit")
    with factory() as session:
        open_trade = session.scalar(
            select(JournalTrade).where(JournalTrade.execution_lifecycle_id == command_id)
        )
        assert open_trade is not None
        assert open_trade.entry_price is not None
        exit_price = _loss_exit(open_trade.direction, open_trade.entry_price)

    messages: list[str] = []
    real_queue = paper_exit_telegram_module.queue_paper_exit_close_telegram

    def _queue_safety_alert(_session: object, _settings: object, alert: object) -> None:
        del _session, _settings
        messages.append(alert.message)  # type: ignore[attr-defined]

    def _queue_with_missing_net_pnl(
        session: object,
        settings: object,
        trade: object,
        *,
        replayed: bool,
    ) -> None:
        backup = trade.net_pnl  # type: ignore[attr-defined]
        trade.net_pnl = None  # type: ignore[attr-defined]
        try:
            real_queue(session, settings, trade, replayed=replayed)  # type: ignore[arg-type]
        finally:
            trade.net_pnl = backup  # type: ignore[attr-defined]

    monkeypatch.setattr(
        "app.services.paper_exit_telegram.queue_safety_alert",
        _queue_safety_alert,
    )
    monkeypatch.setattr(
        "app.services.paper_exit_telegram.queue_paper_exit_close_telegram",
        _queue_with_missing_net_pnl,
    )

    result = _close_labeled(
        factory,
        envelope,
        command_id,
        exit_price=exit_price,
        origin="live",
    )
    assert result.net_pnl is not None  # type: ignore[attr-defined]

    with factory() as session:
        closed = session.scalar(
            select(JournalTrade).where(JournalTrade.execution_lifecycle_id == command_id)
        )
        assert closed is not None
        assert closed.net_pnl is not None
        exit_reason = (closed.exit_reason or "").strip()
        assert exit_reason == "stop_hit"
        expected = format_paper_exit_close_telegram_text(
            symbol=closed.symbol,
            direction=closed.direction,
            r_multiple=paper_exit_telegram_module.realized_r_multiple_label(
                direction=closed.direction,
                entry_price=closed.entry_price,  # type: ignore[arg-type]
                exit_price=closed.exit_price,  # type: ignore[arg-type]
                stop_price=closed.planned_stop_price,
                planned_risk_amount=closed.planned_risk_amount,
                net_pnl=None,
            ),
            timeframe=closed.timeframe,
            exit_reason=exit_reason,
            pnl_display=None,
        )

    assert len(messages) == 1
    assert messages[0] == expected
    assert messages[0].splitlines()[-1].startswith("R: ")
    assert "(" not in messages[0].splitlines()[-1]
