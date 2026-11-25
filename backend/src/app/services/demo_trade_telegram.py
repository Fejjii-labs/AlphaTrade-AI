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

Stop, size and strategy family come from the immutable trade plan, not the
editable Journal row. Pages are plain text: coin, direction, strategy family,
prices, dollar risk/result and local times; never IDs, hashes or exit codes.
The close result is in dollars after fees (the verified Journal fee total, or
an estimate at the BloFin taker rate, labelled as such) and in R against the
plan stop. Funding is not included. The final price is the size-weighted
average exit price.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.candidate_alerts.content import ALERT_TIMEZONE
from app.core.config import Settings
from app.db.models import JournalTrade
from app.guardrails.redaction import redact_text
from app.schemas.common import TradeDirection
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.safety_alerts import (
    queue_safety_alert,
    safety_alert_telegram_allowed,
    telegram_safety_configured,
)

DEMO_TRADE_OPENED = "demo_trade_opened"
DEMO_TRADE_CLOSED = "demo_trade_closed"
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


def _exit_text(exit_reason: str) -> str:
    """``target_hit`` -> ``Target hit``: the page shows words, not exit codes."""

    words = _text(exit_reason, 60).replace("_", " ").replace(":", ": ").strip() or "closed"
    return words[:1].upper() + words[1:]


_VERIFIED_EXIT_PREFIX = "verified_blofin_demo_"
_FAMILY_NAMES = {
    "nested_continuation": "Nested Continuation",
    "sfp": "Swing Failure Pattern",
    "d_line": "D-line",
}
# BloFin taker fee (market entry and triggered TP/SL), used only when the
# journal has no verified fee total; the page then labels fees as estimated.
_ESTIMATED_TAKER_FEE = Decimal("0.0006")


def strategy_family_name(setup_type: object) -> str:
    """``nested_continuation`` -> ``Nested Continuation``; no names, hashes or versions."""

    key = str(getattr(setup_type, "value", setup_type) or "").strip().lower()
    if key in _FAMILY_NAMES:
        return _FAMILY_NAMES[key]
    words = [w for w in key.replace("-", "_").split("_") if w]
    return " ".join(w.capitalize() for w in words)[:60]


def _coin(symbol: str) -> str:
    """``TAO-USDT`` / ``TAOUSDT`` -> ``TAO``."""

    compact = symbol.rsplit(":", 1)[-1].upper().replace("-", "").replace("_", "")
    for quote in ("USDT", "USDC", "USD"):
        if compact.endswith(quote) and len(compact) > len(quote):
            return compact[: -len(quote)]
    return compact or symbol


def _money(value: Decimal) -> str:
    sign = "+" if value > 0 else "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _signed_r(value: Decimal) -> str:
    return f"{'+' if value > 0 else ''}{value.quantize(_R_QUANT)}R"


def _risk_per_unit(direction: TradeDirection, entry: Decimal, stop: Decimal) -> Decimal:
    return entry - stop if direction is TradeDirection.LONG else stop - entry


def _clock(at: datetime | None, *, with_day: bool = False, zone: bool = True) -> str | None:
    if at is None:
        return None
    local = at.astimezone(ALERT_TIMEZONE)
    return local.strftime(("%a " if with_day else "") + "%H:%M" + (" %Z" if zone else ""))


def _duration(start: datetime, end: datetime) -> str:
    minutes = max(0, int((end - start).total_seconds() // 60))
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def _exit_words(exit_reason: str) -> str:
    if not exit_reason.startswith(_VERIFIED_EXIT_PREFIX):
        return _exit_text(exit_reason).lower()
    kinds = set(exit_reason[len(_VERIFIED_EXIT_PREFIX) :].split("_"))
    if "sl" in kinds and kinds & {"tp", "normal"}:
        return "stop hit after a partial take-profit"
    if "sl" in kinds:
        return "stop-loss hit"
    if "tp" in kinds:
        return "take-profit hit"
    return "closed by the bot"


def _targets_line(rr: Sequence[tuple[str, str]], targets: Sequence[Mapping[str, object]]) -> str:
    prices = [_text(str(t.get("price", "")), 24) for t in targets]
    if len(rr) == 1:
        return f"Target: {prices[0]} ({rr[0][1]})"
    if not rr:
        return "Target: none"
    return "Targets: " + ", ".join(
        f"{label} {price} ({value})" for (label, value), price in zip(rr, prices, strict=False)
    )


def format_demo_trade_opened_text(
    *,
    symbol: str,
    strategy: str,
    timeframe: str,
    direction: TradeDirection,
    entry_price: Decimal,
    stop_price: Decimal,
    targets: Sequence[Mapping[str, object]],
    quantity: Decimal | None = None,
    opened_at: datetime | None = None,
) -> str:
    rr = target_r_multiples(
        direction=direction, entry_price=entry_price, stop_price=stop_price, targets=targets
    )
    risk = _risk_per_unit(direction, entry_price, stop_price)
    stop_line = f"Stop: {_price(stop_price)}"
    if quantity is not None and quantity > 0 and risk > 0:
        stop_line += f" (risk {_money(risk * quantity).lstrip('+')})"
    opened = _clock(opened_at)
    lines = [
        f"🟢 Demo trade opened: {_coin(symbol)} {direction.value}",
        f"Strategy: {_text(strategy, 60) or 'unlabeled'}, {timeframe}",
        f"Entry: {_price(entry_price)}",
        stop_line,
        _targets_line(rr, targets),
        *([f"Opened: {opened}"] if opened else []),
    ]
    return "\n".join(lines)


def format_demo_trade_closed_text(
    *,
    symbol: str,
    strategy: str,
    timeframe: str,
    direction: TradeDirection,
    exit_reason: str,
    entry_price: Decimal,
    exit_price: Decimal,
    stop_price: Decimal,
    targets: Sequence[Mapping[str, object]] = (),
    quantity: Decimal | None = None,
    fees: Decimal | None = None,
    opened_at: datetime | None = None,
    closed_at: datetime | None = None,
) -> str:
    """Plain close page: outcome, dollars after fees, R, planned R:R and times."""

    risk = _risk_per_unit(direction, entry_price, stop_price)
    long = direction is TradeDirection.LONG
    move = exit_price - entry_price if long else entry_price - exit_price
    sized = quantity is not None and quantity > 0
    fee_line: str | None = None
    if sized:
        assert quantity is not None
        estimated = fees is None or not fees.is_finite()
        paid = (
            (entry_price + exit_price) * quantity * _ESTIMATED_TAKER_FEE if estimated else abs(fees)  # type: ignore[arg-type]
        )
        net = move * quantity - paid
        result = f"Result: {_money(net)} after fees"
        if risk > 0:
            result += f" ({_signed_r(net / (risk * quantity))})"
        fee_line = f"Fees: {_money(paid).lstrip('+')}" + (" (estimated)" if estimated else "")
        outcome = "win" if net > 0 else "loss" if net < 0 else "break-even"
    else:
        r = move / risk if risk > 0 else None
        result = f"Result: {_signed_r(r)} before fees" if r is not None else "Result: unknown"
        outcome = "win" if move > 0 else "loss" if move < 0 else "break-even"
    icon = {"win": "✅", "loss": "❌", "break-even": "⚪"}[outcome]
    rr = target_r_multiples(
        direction=direction, entry_price=entry_price, stop_price=stop_price, targets=targets
    )
    planned = ", ".join(value for _, value in rr)
    held: str | None = None
    if opened_at is not None and closed_at is not None:
        opened_day = opened_at.astimezone(ALERT_TIMEZONE).date()
        other_day = opened_day != closed_at.astimezone(ALERT_TIMEZONE).date()
        held = (
            f"Held: {_clock(opened_at, with_day=other_day, zone=False)} → "
            f"{_clock(closed_at, with_day=other_day)} ({_duration(opened_at, closed_at)})"
        )
    elif closed_at is not None:
        held = f"Closed: {_clock(closed_at)}"
    lines = [
        f"{icon} Demo trade closed: {_coin(symbol)} {direction.value} ({outcome})",
        f"Strategy: {_text(strategy, 60) or 'unlabeled'}, {timeframe}",
        f"Exit: {_exit_words(exit_reason)}",
        f"Price: {_price(entry_price)} → {_price(exit_price)}",
        result,
        *([fee_line] if fee_line else []),
        *([f"Planned reward:risk: {planned}"] if planned else []),
        *([held] if held else []),
    ]
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
    quantity: Decimal | None = None,
) -> bool:
    """Queue the open page. False when off or the trade has no entry yet.

    ``quantity`` is the position size in coins (from the immutable plan); the
    page shows the dollar risk only when it is known.
    """

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
        quantity=quantity,
        opened_at=getattr(trade, "entry_time", None),
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
    targets: Sequence[Mapping[str, object]] = (),
    quantity: Decimal | None = None,
) -> bool:
    """Queue the final-close page. False when off or the trade has no exit facts."""

    if not demo_trade_pages_enabled(settings, DEMO_TRADE_CLOSED):
        return False
    if trade.entry_price is None or trade.exit_price is None:
        return False
    fees = getattr(trade, "fees", None)
    body = format_demo_trade_closed_text(
        symbol=trade.symbol,
        strategy=strategy,
        timeframe=trade.timeframe,
        direction=trade.direction,
        exit_reason=trade.exit_reason or "",
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        stop_price=stop_price,
        targets=targets,
        quantity=quantity,
        fees=fees if isinstance(fees, Decimal) else None,
        opened_at=getattr(trade, "entry_time", None),
        closed_at=getattr(trade, "exit_time", None),
    )
    _queue(session, settings, DEMO_TRADE_CLOSED, trade, body)
    return True
