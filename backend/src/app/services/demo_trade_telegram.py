"""Telegram pages for governed BloFin demo trade opens and final closes.

Opt-in. ``demo_trade_opened`` and ``demo_trade_closed`` are optional
safety-alert codes: they send only when listed in
``TELEGRAM_SAFETY_ALERT_ALLOWLIST`` and the existing ``telegram_bot_token``
and ``telegram_chat_id`` are set. No new secret is added.

Pages are queued on the caller's session and sent after commit, so a
rolled-back write never notifies. They are sent on a background thread
(HTTP timeout at most 5 seconds), so a worker cycle never waits on Telegram.
Partial take-profit fills are not paged; they stay in the journal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import JournalTrade
from app.schemas.common import TradeDirection
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.paper_exit_telegram import (
    realized_pnl_display_for_trade,
    realized_r_multiple_label,
)
from app.services.safety_alerts import queue_safety_alert

DEMO_TRADE_OPENED = "demo_trade_opened"
DEMO_TRADE_CLOSED = "demo_trade_closed"
_HEADLINE_OPENED = "AlphaTrade DEMO trade opened."
_HEADLINE_CLOSED = "AlphaTrade DEMO trade closed."
_R_QUANT = Decimal("0.01")


def _price(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def target_r_multiples(
    *,
    direction: TradeDirection,
    entry_price: Decimal,
    stop_price: Decimal,
    targets: Sequence[Mapping[str, object]],
) -> list[tuple[str, str]]:
    """``(label, R)`` per planned target, measured from the actual entry, before fees."""

    risk = (
        entry_price - stop_price if direction is TradeDirection.LONG else stop_price - entry_price
    )
    rows: list[tuple[str, str]] = []
    for index, target in enumerate(targets, start=1):
        label = str(target.get("label") or f"TP{index}")[:12]
        try:
            price = Decimal(str(target.get("price")))
        except (InvalidOperation, ValueError):
            rows.append((label, "unknown"))
            continue
        if risk <= 0 or not price.is_finite():
            rows.append((label, "unknown"))
            continue
        reward = price - entry_price if direction is TradeDirection.LONG else entry_price - price
        rows.append((label, f"{(reward / risk).quantize(_R_QUANT)}R"))
    return rows


def format_demo_trade_opened_text(
    *,
    symbol: str,
    strategy: str,
    timeframe: str,
    direction: TradeDirection,
    entry_price: Decimal,
    stop_price: Decimal,
    targets: Sequence[Mapping[str, object]],
) -> str:
    rr = target_r_multiples(
        direction=direction, entry_price=entry_price, stop_price=stop_price, targets=targets
    )
    rr_line = ", ".join(f"{label} {value}" for label, value in rr) or "no targets"
    return "\n".join(
        (
            _HEADLINE_OPENED,
            f"Symbol: {symbol}",
            f"Strategy: {strategy}",
            f"Timeframe: {timeframe}",
            f"Direction: {direction.value}",
            f"Entry: {_price(entry_price)}",
            f"Stop: {_price(stop_price)}",
            f"R:R: {rr_line}",
        )
    )


def format_demo_trade_closed_text(
    *,
    symbol: str,
    strategy: str,
    timeframe: str,
    direction: TradeDirection,
    exit_reason: str,
    r_multiple: str,
    pnl_display: str | None,
) -> str:
    r_line = f"Final R: {r_multiple}"
    if pnl_display:
        r_line = f"{r_line} ({pnl_display})"
    return "\n".join(
        (
            _HEADLINE_CLOSED,
            f"Symbol: {symbol}",
            f"Strategy: {strategy}",
            f"Timeframe: {timeframe}",
            f"Direction: {direction.value}",
            f"Exit: {exit_reason or 'closed'}",
            r_line,
        )
    )


def _strategy(trade: JournalTrade) -> str:
    return (trade.strategy_label or "").strip()[:80] or "unlabeled"


def _queue(session: Session, settings: Settings, code: str, trade: JournalTrade, body: str) -> None:
    queue_safety_alert(
        session,
        settings,
        ExitMonitorAlert(
            code=code,
            severity="info",
            message=body,
            fields={"dedup_key": f"{code}:{trade.id}"},
            telegram_verbatim=True,
            background=True,
        ),
    )


def queue_demo_trade_opened_telegram(
    session: Session, settings: Settings, trade: JournalTrade
) -> bool:
    """Queue the open page. False when the trade has no entry or stop yet."""

    if trade.entry_price is None or trade.planned_stop_price is None:
        return False
    body = format_demo_trade_opened_text(
        symbol=trade.symbol,
        strategy=_strategy(trade),
        timeframe=trade.timeframe,
        direction=trade.direction,
        entry_price=trade.entry_price,
        stop_price=trade.planned_stop_price,
        targets=list(trade.planned_targets or ()),
    )
    _queue(session, settings, DEMO_TRADE_OPENED, trade, body)
    return True


def queue_demo_trade_closed_telegram(
    session: Session, settings: Settings, trade: JournalTrade
) -> bool:
    """Queue the final-close page. False unless the trade has its exit facts."""

    if trade.entry_price is None or trade.exit_price is None or trade.exit_time is None:
        return False
    r_multiple = realized_r_multiple_label(
        direction=trade.direction,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        stop_price=trade.planned_stop_price,
        planned_risk_amount=trade.planned_risk_amount,
        net_pnl=trade.net_pnl,
    )
    body = format_demo_trade_closed_text(
        symbol=trade.symbol,
        strategy=_strategy(trade),
        timeframe=trade.timeframe,
        direction=trade.direction,
        exit_reason=(trade.exit_reason or "").strip()[:60],
        r_multiple=r_multiple,
        pnl_display=realized_pnl_display_for_trade(trade),
    )
    _queue(session, settings, DEMO_TRADE_CLOSED, trade, body)
    return True
