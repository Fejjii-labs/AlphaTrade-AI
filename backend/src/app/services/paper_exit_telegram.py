"""Telegram bodies for actionable internal-paper stop and target exits."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import JournalTrade
from app.schemas.common import TradeDirection
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.safety_alerts import queue_safety_alert

_HEADLINE_STOP = "AlphaTrade PAPER informational stop-out."
_HEADLINE_TARGET = "AlphaTrade PAPER informational take-profit hit."


def format_paper_exit_close_telegram_text(
    *,
    symbol: str,
    direction: TradeDirection,
    entry_price: Decimal,
    exit_price: Decimal,
    net_pnl: Decimal,
    r_multiple: str,
    timeframe: str,
    exit_reason: str,
) -> str:
    """Deterministic private-chat text aligned with setup informational alerts."""

    headline = _HEADLINE_TARGET if exit_reason == "target_hit" else _HEADLINE_STOP
    return "\n".join(
        (
            headline,
            f"Symbol: {symbol}",
            f"Timeframe: {timeframe}",
            f"Direction: {direction.value}",
            f"Entry: {format(entry_price, 'f')}",
            f"Exit: {format(exit_price, 'f')}",
            f"R: {r_multiple}",
            f"P&L: {format(net_pnl, 'f')}",
        )
    )


def realized_r_multiple_label(
    *,
    direction: TradeDirection,
    entry_price: Decimal,
    exit_price: Decimal,
    stop_price: Decimal | None,
    planned_risk_amount: Decimal | None,
    net_pnl: Decimal,
) -> str:
    """One R label for Telegram. Uses planned risk when present."""

    if planned_risk_amount is not None and planned_risk_amount > 0:
        return format(net_pnl / planned_risk_amount, ".2f")
    if stop_price is None:
        return "unknown"
    if direction is TradeDirection.LONG:
        risk_per_unit = entry_price - stop_price
        move = exit_price - entry_price
    else:
        risk_per_unit = stop_price - entry_price
        move = entry_price - exit_price
    if risk_per_unit <= 0:
        return "unknown"
    return format(move / risk_per_unit, ".2f")


def queue_paper_exit_close_telegram(
    session: Session,
    settings: Settings,
    trade: JournalTrade,
    *,
    replayed: bool,
) -> None:
    """Queue one stop or target Telegram page after a committed monitor close."""

    if replayed:
        return
    exit_reason = (trade.exit_reason or "").strip()
    if exit_reason not in {"stop_hit", "target_hit"}:
        return
    if (
        trade.entry_price is None
        or trade.exit_price is None
        or trade.net_pnl is None
        or trade.result is None
    ):
        return
    code = "stop_hit" if exit_reason == "stop_hit" else "tp_hit"
    r_multiple = realized_r_multiple_label(
        direction=trade.direction,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        stop_price=trade.planned_stop_price,
        planned_risk_amount=trade.planned_risk_amount,
        net_pnl=trade.net_pnl,
    )
    body = format_paper_exit_close_telegram_text(
        symbol=trade.symbol,
        direction=trade.direction,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        net_pnl=trade.net_pnl,
        r_multiple=r_multiple,
        timeframe=trade.timeframe,
        exit_reason=exit_reason,
    )
    queue_safety_alert(
        session,
        settings,
        ExitMonitorAlert(
            code=code,
            severity="info",
            message=body,
            fields={"dedup_key": f"{code}:{trade.id}"},
            telegram_verbatim=True,
        ),
    )
