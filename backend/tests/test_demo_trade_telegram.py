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
    format_demo_trade_closed_text,
    format_demo_trade_opened_text,
    queue_demo_trade_closed_telegram,
    queue_demo_trade_opened_telegram,
    target_r_multiples,
)
from app.services.safety_alerts import (
    deliver_queued_safety_alerts,
    reset_safety_alert_dedup,
    safety_alert_telegram_allowed,
)

_TOKEN = "123456:simulated-token"
_CHAT = "424242"
_TARGETS = [
    {"label": "TP1", "price": "102.5", "size_fraction": "0.15"},
    {"label": "TP2", "price": "110", "size_fraction": "0.5"},
    {"label": "TP3", "price": "115", "size_fraction": "0.2"},
]


def _trade(**overrides: object) -> Any:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "direction": TradeDirection.LONG,
        "strategy_label": "D-line 15m long",
        "entry_price": Decimal("100"),
        "planned_stop_price": Decimal("95"),
        "planned_targets": _TARGETS,
        "planned_risk_amount": Decimal("10"),
        "exit_price": None,
        "exit_time": None,
        "exit_reason": None,
        "net_pnl": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_open_text_has_coin_strategy_direction_entry_stop_and_r_per_target() -> None:
    text = format_demo_trade_opened_text(
        symbol="BTCUSDT",
        strategy="D-line 15m long",
        timeframe="15m",
        direction=TradeDirection.LONG,
        entry_price=Decimal("100.00"),
        stop_price=Decimal("95.0"),
        targets=_TARGETS,
    )
    assert text.splitlines() == [
        "AlphaTrade DEMO trade opened.",
        "Symbol: BTCUSDT",
        "Strategy: D-line 15m long",
        "Timeframe: 15m",
        "Direction: long",
        "Entry: 100",
        "Stop: 95",
        "R:R: TP1 0.50R, TP2 2.00R, TP3 3.00R",
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


def test_close_text_has_final_r_and_net_pnl() -> None:
    text = format_demo_trade_closed_text(
        symbol="ETHUSDT",
        strategy="D-line 1h short",
        timeframe="1h",
        direction=TradeDirection.SHORT,
        exit_reason="target_hit",
        r_multiple="1.85",
        pnl_display="+18.50 USDT",
    )
    assert "AlphaTrade DEMO trade closed." in text
    assert "Exit: target_hit" in text
    assert "Final R: 1.85 (+18.50 USDT)" in text


def test_demo_trade_codes_are_opt_in() -> None:
    off = Settings(_env_file=None)
    on = Settings(
        _env_file=None,
        telegram_safety_alert_allowlist=["demo_trade_opened", "demo_trade_closed"],
    )
    for code in ("demo_trade_opened", "demo_trade_closed"):
        assert not safety_alert_telegram_allowed(off, code)
        assert safety_alert_telegram_allowed(on, code)


def test_queue_refuses_a_trade_without_entry_stop_or_exit() -> None:
    session: Any = SimpleNamespace(info={})
    settings = Settings(_env_file=None)
    assert not queue_demo_trade_opened_telegram(session, settings, _trade(entry_price=None))
    assert not queue_demo_trade_opened_telegram(
        session, settings, _trade(planned_stop_price=None)
    )
    assert not queue_demo_trade_closed_telegram(session, settings, _trade())
    assert session.info.get("alphatrade_safety_alerts", []) == []


def test_pages_send_after_commit_on_a_background_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_safety_alert_dedup()
    sent: list[tuple[str, str, bool]] = []
    done = threading.Event()

    def _deliver(
        self: TelegramAlertDeliveryProvider,
        payload: object,
        *,
        bypass_enable_check: bool = False,
    ) -> AlertDeliveryResult:
        sent.append(
            (
                payload.message,  # type: ignore[attr-defined]
                threading.current_thread().name,
                bypass_enable_check,
            )
        )
        if len(sent) == 2:
            done.set()
        return AlertDeliveryResult(success=True, channel=AlertDeliveryChannel.TELEGRAM)

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _deliver)
    settings = Settings(
        _env_file=None,
        telegram_bot_token=_TOKEN,
        telegram_chat_id=_CHAT,
        telegram_safety_alert_allowlist=["demo_trade_opened", "demo_trade_closed"],
    )
    session: Any = SimpleNamespace(info={})
    opened = _trade()
    closed = _trade(
        exit_price=Decimal("110"),
        exit_time=object(),
        exit_reason="target_hit",
        net_pnl=Decimal("18.5"),
    )
    assert queue_demo_trade_opened_telegram(session, settings, opened)
    assert queue_demo_trade_closed_telegram(session, settings, closed)
    assert sent == []  # nothing leaves before commit
    deliver_queued_safety_alerts(session)
    assert done.wait(timeout=5)
    assert [name.startswith("telegram-alert") for _, name, _ in sent] == [True, True]
    assert "AlphaTrade DEMO trade opened." in sent[0][0]
    assert "R:R: TP1 0.50R, TP2 2.00R, TP3 3.00R" in sent[0][0]
    assert "Final R: 1.85 (+18.50 USDT)" in sent[1][0]
    assert all(_TOKEN not in message and _CHAT not in message for message, _, _ in sent)
