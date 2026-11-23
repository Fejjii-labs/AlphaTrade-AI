"""Telegram pages for governed BloFin demo trade opens and final closes.

Opt-in. ``demo_trade_opened`` and ``demo_trade_closed`` are optional
safety-alert codes: they send only when listed in
``TELEGRAM_SAFETY_ALERT_ALLOWLIST`` and the existing ``telegram_bot_token``
and ``telegram_chat_id`` are set (``TELEGRAM_ALERTS_ENABLED`` does not gate
them, like every safety-alert code). No new secret is added. When off,
nothing is queued.

Pages are queued on the caller's session and sent after commit, so a
rolled-back write never notifies. They are sent at most once, on a background
thread (each HTTP attempt times out within 5 seconds), so a worker cycle never
waits on Telegram; a failed send is logged and not retried. Partial
take-profit fills are not paged; they stay in the journal.

Stop and strategy come from the immutable trade plan, not the editable
Journal row. R is measured from prices against the plan stop: gross, before
fees and funding. The final R uses the size-weighted average exit price.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import JournalTrade
from app.guardrails.redaction import redact_text
from app.schemas.common import TradeDirection
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.paper_exit_telegram import realized_pnl_display_for_trade
from app.services.safety_alerts import (
    queue_safety_alert,
    safety_alert_telegram_allowed,
    telegram_safety_configured,
)

DEMO_TRADE_OPENED = "demo_trade_opened"
DEMO_TRADE_CLOSED = "demo_trade_closed"
_HEADLINE_OPENED = "AlphaTrade DEMO trade opened."
_HEADLINE_CLOSED = "AlphaTrade DEMO trade closed."
_R_QUANT = Decimal("0.01")


def demo_trade_pages_enabled(settings: Settings, code: str) -> bool:
    """True when this page is allowlisted and the bot token and chat id are set."""

    return safety_alert_telegram_allowed(settings, code) and telegram_safety_configured(settings)


def _price(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _text(value: str, limit: int) -> str:
    return redact_text(value.strip())[:limit]


def r_multiple(
    *, direction: TradeDirection, entry_price: Decimal, stop_price: Decimal, price: Decimal
) -> str:
    """Gross R of ``price`` from the entry against the stop, or ``unknown``."""

    long = direction is TradeDirection.LONG
    risk = entry_price - stop_price if long else stop_price - entry_price
    if risk <= 0 or not price.is_finite():
        return "unknown"
    reward = price - entry_price if long else entry_price - price
    return f"{(reward / risk).quantize(_R_QUANT)}R"


def target_r_multiples(
    *,
    direction: TradeDirection,
    entry_price: Decimal,
    stop_price: Decimal,
    targets: Sequence[Mapping[str, object]],
) -> list[tuple[str, str]]:
    """``(label, R)`` per planned target, measured from the actual entry, before fees."""

    rows: list[tuple[str, str]] = []
    for index, target in enumerate(targets, start=1):
        label = _text(str(target.get("label") or f"TP{index}"), 12)
        try:
            price = Decimal(str(target.get("price")))
        except (InvalidOperation, ValueError):
            rows.append((label, "unknown"))
            continue
        rows.append(
            (
                label,
                r_multiple(
                    direction=direction, entry_price=entry_price, stop_price=stop_price, price=price
                ),
            )
        )
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
            f"Strategy: {_text(strategy, 120) or 'unlabeled'}",
            f"Timeframe: {timeframe}",
            f"Direction: {direction.value}",
            f"Entry: {_price(entry_price)}",
            f"Stop: {_price(stop_price)}",
            f"R:R (before fees): {rr_line}",
        )
    )


def format_demo_trade_closed_text(
    *,
    symbol: str,
    strategy: str,
    timeframe: str,
    direction: TradeDirection,
    exit_reason: str,
    final_r: str,
    pnl_display: str | None,
) -> str:
    lines = [
        _HEADLINE_CLOSED,
        f"Symbol: {symbol}",
        f"Strategy: {_text(strategy, 120) or 'unlabeled'}",
        f"Timeframe: {timeframe}",
        f"Direction: {direction.value}",
        f"Exit: {_text(exit_reason, 60) or 'closed'}",
        f"Final R (gross, before fees): {final_r}",
    ]
    if pnl_display:
        lines.append(f"Net P&L: {pnl_display}")
    return "\n".join(lines)


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
    session: Session,
    settings: Settings,
    trade: JournalTrade,
    *,
    strategy: str,
    stop_price: Decimal,
    targets: Sequence[Mapping[str, object]],
) -> bool:
    """Queue the open page. False when off or the trade has no entry yet."""

    if not demo_trade_pages_enabled(settings, DEMO_TRADE_OPENED) or trade.entry_price is None:
        return False
    body = format_demo_trade_opened_text(
        symbol=trade.symbol,
        strategy=strategy,
        timeframe=trade.timeframe,
        direction=trade.direction,
        entry_price=trade.entry_price,
        stop_price=stop_price,
        targets=targets,
    )
    _queue(session, settings, DEMO_TRADE_OPENED, trade, body)
    return True


def queue_demo_trade_closed_telegram(
    session: Session,
    settings: Settings,
    trade: JournalTrade,
    *,
    strategy: str,
    stop_price: Decimal,
) -> bool:
    """Queue the final-close page. False when off or the trade has no exit facts."""

    if not demo_trade_pages_enabled(settings, DEMO_TRADE_CLOSED):
        return False
    if trade.entry_price is None or trade.exit_price is None:
        return False
    body = format_demo_trade_closed_text(
        symbol=trade.symbol,
        strategy=strategy,
        timeframe=trade.timeframe,
        direction=trade.direction,
        exit_reason=trade.exit_reason or "",
        final_r=r_multiple(
            direction=trade.direction,
            entry_price=trade.entry_price,
            stop_price=stop_price,
            price=trade.exit_price,
        ),
        pnl_display=realized_pnl_display_for_trade(trade),
    )
    _queue(session, settings, DEMO_TRADE_CLOSED, trade, body)
    return True
