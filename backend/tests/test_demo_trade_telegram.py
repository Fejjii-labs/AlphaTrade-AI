"""Governed BloFin demo trade open and final-close Telegram pages."""

from __future__ import annotations

import threading
import uuid
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import Settings
from app.providers.alert_delivery.base import AlertDeliveryResult
from app.providers.alert_delivery.telegram import TelegramAlertDeliveryProvider
from app.schemas.common import AlertDeliveryChannel, TradeDirection
from app.services.demo_trade_telegram import (
    demo_trade_pages_enabled,
    format_demo_trade_closed_text,
    format_demo_trade_opened_text,
    queue_demo_trade_closed_telegram,
    queue_demo_trade_opened_telegram,
    target_r_multiples,
)
from app.services.safety_alerts import deliver_queued_safety_alerts, reset_safety_alert_dedup

_TOKEN = "123456:simulated-token"
_CHAT = "424242"
_STOP = Decimal("95")
_TARGETS = [
    {"label": "TP1", "price": "102.5", "size_fraction": "0.15"},
    {"label": "TP2", "price": "110", "size_fraction": "0.5"},
    {"label": "TP3", "price": "115", "size_fraction": "0.2"},
]


def _settings(*, on: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        telegram_bot_token=_TOKEN,
        telegram_chat_id=_CHAT,
        telegram_safety_alert_allowlist=["demo_trade_opened", "demo_trade_closed"] if on else [],
    )


def _trade(**overrides: object) -> Any:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "direction": TradeDirection.LONG,
        "entry_price": Decimal("100"),
        "exit_price": None,
        "exit_reason": None,
        "net_pnl": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_open_text_has_coin_strategy_direction_entry_stop_and_r_per_target() -> None:
    text = format_demo_trade_opened_text(
        symbol="BTCUSDT",
        strategy="D-line BTCUSDT long 15m abc v1",
        timeframe="15m",
        direction=TradeDirection.LONG,
        entry_price=Decimal("100.00"),
        stop_price=Decimal("95.0"),
        targets=_TARGETS,
    )
    assert text.splitlines() == [
        "AlphaTrade DEMO trade opened.",
        "Symbol: BTCUSDT",
        "Strategy: D-line BTCUSDT long 15m abc v1",
        "Timeframe: 15m",
        "Direction: long",
        "Entry: 100",
        "Stop: 95",
        "R:R (before fees): TP1 0.50R, TP2 2.00R, TP3 3.00R",
    ]


def test_short_r_uses_the_actual_entry_and_bad_risk_is_unknown() -> None:
    short = target_r_multiples(
        direction=TradeDirection.SHORT,
        entry_price=Decimal("100"),
        stop_price=Decimal("104"),
        targets=[{"label": "TP1", "price": "92"}, {"price": "bad"}],
    )
    assert short == [("TP1", "2.00R"), ("TP2", "unknown")]
    inverted = target_r_multiples(
        direction=TradeDirection.LONG,
        entry_price=Decimal("100"),
        stop_price=Decimal("101"),
        targets=[{"label": "TP1", "price": "110"}],
    )
    assert inverted == [("TP1", "unknown")]


def test_close_text_labels_gross_r_and_shows_net_pnl_only_when_stored() -> None:
    text = format_demo_trade_closed_text(
        symbol="ETHUSDT",
        strategy="",
        timeframe="1h",
        direction=TradeDirection.SHORT,
        exit_reason="target_hit",
        final_r="1.85R",
        pnl_display=None,
    )
    assert "Strategy: unlabeled" in text
    assert "Exit: Target hit" in text
    assert "Final R (gross, before fees): 1.85R" in text
    assert "Net P&L" not in text
    with_pnl = format_demo_trade_closed_text(
        symbol="ETHUSDT",
        strategy="s",
        timeframe="1h",
        direction=TradeDirection.SHORT,
        exit_reason="",
        final_r="1.85R",
        pnl_display="+18.50 USDT",
    )
    assert "Exit: Closed" in with_pnl
    assert with_pnl.endswith("Net P&L: +18.50 USDT")


def test_free_text_is_redacted() -> None:
    text = format_demo_trade_opened_text(
        symbol="BTCUSDT",
        strategy="api_key=supersecret1",
        timeframe="15m",
        direction=TradeDirection.LONG,
        entry_price=Decimal("100"),
        stop_price=_STOP,
        targets=[{"label": "api_key=x", "price": "110"}],
    )
    assert "supersecret1" not in text
    assert "api_key=x" not in text


def test_off_by_default_and_nothing_is_queued_when_off() -> None:
    session: Any = SimpleNamespace(info={})
    for code in ("demo_trade_opened", "demo_trade_closed"):
        assert not demo_trade_pages_enabled(Settings(_env_file=None), code)
        assert not demo_trade_pages_enabled(_settings(on=False), code)
        assert demo_trade_pages_enabled(_settings(), code)
    unconfigured = _settings().model_copy(update={"telegram_bot_token": ""})
    assert not demo_trade_pages_enabled(unconfigured, "demo_trade_opened")
    closed = _trade(exit_price=Decimal("110"))
    assert not queue_demo_trade_opened_telegram(
        session, _settings(on=False), _trade(), strategy="s", stop_price=_STOP, targets=_TARGETS
    )
    assert not queue_demo_trade_closed_telegram(
        session, _settings(on=False), closed, strategy="s", stop_price=_STOP
    )
    assert session.info.get("alphatrade_safety_alerts", []) == []


def test_queue_refuses_a_trade_without_entry_or_exit() -> None:
    session: Any = SimpleNamespace(info={})
    settings = _settings()
    assert not queue_demo_trade_opened_telegram(
        session, settings, _trade(entry_price=None), strategy="s", stop_price=_STOP, targets=[]
    )
    assert not queue_demo_trade_closed_telegram(
        session, settings, _trade(), strategy="s", stop_price=_STOP
    )
    assert session.info.get("alphatrade_safety_alerts", []) == []


def test_pages_send_after_commit_on_a_background_thread_once_per_trade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_safety_alert_dedup()
    sent: list[tuple[str, str]] = []
    done = threading.Event()

    def _deliver(
        self: TelegramAlertDeliveryProvider,
        payload: object,
        *,
        bypass_enable_check: bool = False,
    ) -> AlertDeliveryResult:
        sent.append((payload.message, threading.current_thread().name))  # type: ignore[attr-defined]
        if len(sent) == 3:
            done.set()
        return AlertDeliveryResult(success=True, channel=AlertDeliveryChannel.TELEGRAM)

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _deliver)
    settings = _settings()
    session: Any = SimpleNamespace(info={})
    first, second = _trade(), _trade(symbol="ETHUSDT")
    closed = _trade(id=first.id, exit_price=Decimal("110"), exit_reason="target_hit")
    for trade in (first, second):
        assert queue_demo_trade_opened_telegram(
            session, settings, trade, strategy="D-line v1", stop_price=_STOP, targets=_TARGETS
        )
    assert queue_demo_trade_closed_telegram(
        session, settings, closed, strategy="D-line v1", stop_price=_STOP
    )
    assert sent == []  # nothing leaves before commit
    deliver_queued_safety_alerts(session)
    assert done.wait(timeout=5)
    assert all(name.startswith("telegram-alert") for _, name in sent)
    # Two different trades opening together each get a page (per-trade dedup key).
    assert ["Symbol: BTCUSDT" in sent[0][0], "Symbol: ETHUSDT" in sent[1][0]] == [True, True]
    assert "R:R (before fees): TP1 0.50R, TP2 2.00R, TP3 3.00R" in sent[0][0]
    assert "Final R (gross, before fees): 2.00R" in sent[2][0]
    assert all(_TOKEN not in message and _CHAT not in message for message, _ in sent)
