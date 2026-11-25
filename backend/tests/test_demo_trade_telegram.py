"""Governed BloFin demo trade open and final-close Telegram pages."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import Settings
from app.providers.alert_delivery.base import AlertDeliveryResult
from app.providers.alert_delivery.telegram import TelegramAlertDeliveryProvider
from app.schemas.common import AlertDeliveryChannel, StrategyId, TradeDirection
from app.services.demo_trade_telegram import (
    demo_trade_pages_enabled,
    format_demo_trade_closed_text,
    format_demo_trade_opened_text,
    queue_demo_trade_closed_telegram,
    queue_demo_trade_opened_telegram,
    strategy_family_name,
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


def test_open_text_is_plain_with_dollar_risk_targets_and_local_time() -> None:
    text = format_demo_trade_opened_text(
        symbol="BTC-USDT",
        strategy="D-line",
        timeframe="15m",
        direction=TradeDirection.LONG,
        entry_price=Decimal("100.00"),
        stop_price=Decimal("95.0"),
        targets=_TARGETS,
        quantity=Decimal("2"),
        opened_at=datetime(2026, 11, 25, 2, 4, tzinfo=UTC),
    )
    assert text.splitlines() == [
        "🟢 Demo trade opened: BTC long",
        "Strategy: D-line, 15m",
        "Entry: 100",
        "Stop: 95 (risk $10.00)",
        "Targets: TP1 102.5 (0.50R), TP2 110 (2.00R), TP3 115 (3.00R)",
        "Opened: 03:04 CET",
    ]


def test_open_text_single_target_and_unknown_size() -> None:
    text = format_demo_trade_opened_text(
        symbol="TAOUSDT",
        strategy="Nested Continuation",
        timeframe="15m",
        direction=TradeDirection.SHORT,
        entry_price=Decimal("178.72"),
        stop_price=Decimal("181.41"),
        targets=[{"label": "tp", "price": "174.47"}],
    )
    assert text.splitlines() == [
        "🟢 Demo trade opened: TAO short",
        "Strategy: Nested Continuation, 15m",
        "Entry: 178.72",
        "Stop: 181.41",
        "Target: 174.47 (1.58R)",
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


def _close(**overrides: object) -> str:
    values: dict[str, object] = {
        "symbol": "TAO-USDT",
        "strategy": "Nested Continuation",
        "timeframe": "15m",
        "direction": TradeDirection.SHORT,
        "exit_reason": "verified_blofin_demo_tp",
        "entry_price": Decimal("178.72"),
        "exit_price": Decimal("174.40"),
        "stop_price": Decimal("181.41"),
        "targets": [{"label": "tp", "price": "174.47"}],
        "quantity": Decimal("180"),
        "opened_at": datetime(2026, 11, 25, 2, 4, 26, tzinfo=UTC),
        "closed_at": datetime(2026, 11, 25, 7, 37, 12, tzinfo=UTC),
    }
    values.update(overrides)
    return format_demo_trade_closed_text(**values)  # type: ignore[arg-type]


def test_close_text_shows_dollars_after_fees_r_planned_rr_and_times() -> None:
    # The staging TAO trade: 180 TAO short, closed at the take-profit.
    assert _close().splitlines() == [
        "✅ Demo trade closed: TAO short (win)",
        "Strategy: Nested Continuation, 15m",
        "Exit: take-profit hit",
        "Price: 178.72 → 174.4",
        "Result: +$739.46 after fees (+1.53R)",
        "Fees: $38.14 (estimated)",
        "Planned reward:risk: 1.58R",
        "Held: 03:04 → 08:37 CET (5h 32m)",
    ]


def test_close_text_uses_verified_fees_and_shows_a_loss() -> None:
    text = _close(
        exit_reason="verified_blofin_demo_sl",
        exit_price=Decimal("181.41"),
        fees=Decimal("-39.00"),
        closed_at=datetime(2026, 11, 26, 9, 0, tzinfo=UTC),
    )
    lines = text.splitlines()
    assert lines[0] == "❌ Demo trade closed: TAO short (loss)"
    assert lines[2] == "Exit: stop-loss hit"
    assert lines[4] == "Result: -$523.20 after fees (-1.08R)"
    assert lines[5] == "Fees: $39.00"
    assert lines[-1] == "Held: Wed 03:04 → Thu 10:00 CET (1d 6h)"


def test_close_text_without_size_falls_back_to_r_and_words() -> None:
    text = _close(quantity=None, exit_reason="target_hit", opened_at=None, closed_at=None)
    assert "Result: +1.61R before fees" in text
    assert "Exit: target hit" in text
    assert "$" not in text
    partial = _close(exit_reason="verified_blofin_demo_normal_sl_tp")
    assert "Exit: stop hit after a partial take-profit" in partial


def test_close_text_never_shows_ids_hashes_or_codes() -> None:
    text = _close()
    for dropped in ("verified_blofin", "a68ae8e5a20c", " v1", "Ref", "Symbol:", "gross"):
        assert dropped not in text


def test_strategy_family_name_hides_names_and_hashes() -> None:
    assert strategy_family_name("nested_continuation") == "Nested Continuation"
    assert strategy_family_name(StrategyId.SFP) == "Swing Failure Pattern"
    assert strategy_family_name(StrategyId.D_LINE) == "D-line"
    assert strategy_family_name("htf_trend_pullback") == "Htf Trend Pullback"


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
    assert ["opened: BTC long" in sent[0][0], "opened: ETH long" in sent[1][0]] == [True, True]
    assert "Targets: TP1 102.5 (0.50R), TP2 110 (2.00R), TP3 115 (3.00R)" in sent[0][0]
    assert "Result: +2.00R before fees" in sent[2][0]
    assert all(_TOKEN not in message and _CHAT not in message for message, _ in sent)
