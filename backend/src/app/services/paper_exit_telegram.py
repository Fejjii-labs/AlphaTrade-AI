"""Telegram bodies for actionable internal-paper stop and target exits."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ValidationAppError
from app.db.models import JournalTrade
from app.schemas.common import TradeDirection
from app.services.paper_close_economics import paper_close_pnl
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.safety_alerts import queue_safety_alert

_HEADLINE_STOP = "AlphaTrade PAPER informational stop-out."
_HEADLINE_TARGET = "AlphaTrade PAPER informational take-profit hit."
_PNL_QUANT = Decimal("0.01")
_PCT_QUANT = Decimal("0.01")


def format_paper_exit_close_telegram_text(
    *,
    symbol: str,
    direction: TradeDirection,
    r_multiple: str,
    timeframe: str,
    exit_reason: str,
    pnl_display: str | None = None,
) -> str:
    """Deterministic private-chat text aligned with setup informational alerts."""

    headline = _HEADLINE_TARGET if exit_reason == "target_hit" else _HEADLINE_STOP
    outcome = "take-profit" if exit_reason == "target_hit" else "stop-out"
    r_line = f"R: {r_multiple}"
    if pnl_display:
        r_line = f"{r_line} ({pnl_display})"
    return "\n".join(
        (
            headline,
            f"Symbol: {symbol}",
            f"Timeframe: {timeframe}",
            f"Direction: {direction.value}",
            f"Outcome: {outcome}",
            r_line,
        )
    )


def quote_currency_for_symbol(symbol: str) -> str | None:
    """Quote asset for linear USDT symbols used in internal paper."""

    token = symbol.strip().upper()
    if token.endswith("USDT") and len(token) > 4 and token.isalnum():
        return "USDT"
    return None


def realized_pnl_amount(
    *,
    direction: TradeDirection,
    entry_price: Decimal | None,
    exit_price: Decimal | None,
    size: Decimal | None,
    fees: Decimal | None,
    funding: Decimal | None,
    slippage: Decimal | None,
) -> Decimal | None:
    """Realized P&L from stored fill fields. Omits when required inputs are missing."""

    if entry_price is None or exit_price is None or size is None:
        return None
    if not all(isinstance(v, Decimal) and v.is_finite() for v in (entry_price, exit_price, size)):
        return None
    if fees is None:
        delta = exit_price - entry_price
        if direction is TradeDirection.SHORT:
            delta = -delta
        elif direction is not TradeDirection.LONG:
            return None
        return (delta * size).quantize(_PNL_QUANT)
    try:
        economics = paper_close_pnl(
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            size=size,
            fees=fees,
            funding=funding if funding is not None else Decimal("0"),
            slippage=slippage if slippage is not None else Decimal("0"),
        )
    except ValidationAppError:
        return None
    return economics.net_pnl.quantize(_PNL_QUANT)


def format_realized_pnl_display(
    pnl: Decimal,
    *,
    quote_currency: str,
    account_equity: Decimal | None = None,
) -> str:
    """Signed quote P&L, optionally with percent of account equity."""

    amount = pnl.quantize(_PNL_QUANT)
    if amount > 0:
        signed = f"+{format(amount, 'f')}"
    else:
        signed = format(amount, "f")
    base = f"{signed} {quote_currency}"
    if account_equity is not None and account_equity > 0:
        pct = (amount / account_equity * Decimal("100")).quantize(_PCT_QUANT)
        if pct > 0:
            pct_text = f"+{format(pct, 'f')}%"
        else:
            pct_text = f"{format(pct, 'f')}%"
        return f"{base}, {pct_text} of account"
    return base


def realized_pnl_display_for_trade(
    trade: JournalTrade,
    *,
    account_equity: Decimal | None = None,
) -> str | None:
    """P&L fragment for Telegram when inputs are complete; otherwise None."""

    quote = quote_currency_for_symbol(trade.symbol)
    if quote is None:
        return None
    amount = realized_pnl_amount(
        direction=trade.direction,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        size=trade.size,
        fees=trade.fees,
        funding=trade.funding,
        slippage=trade.slippage,
    )
    if amount is None:
        return None
    return format_realized_pnl_display(
        amount,
        quote_currency=quote,
        account_equity=account_equity,
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


def _account_equity_for_trade(session: Session, trade: JournalTrade) -> Decimal | None:
    """Post-close paper equity when the authoritative daily snapshot is available."""

    from app.services.risk.daily_risk_accounting import DailyRiskAccounting
    from app.services.risk.settings_service import RiskSettingsService

    try:
        accounting = DailyRiskAccounting(session, RiskSettingsService(session))
        snapshot = accounting.sync_from_portfolio(
            organization_id=trade.organization_id,
            user_id=trade.user_id,
        )
    except Exception:
        return None
    equity = snapshot.account_equity
    if isinstance(equity, Decimal) and equity.is_finite() and equity > 0:
        return equity
    return None


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
    pnl_display = realized_pnl_display_for_trade(
        trade,
        account_equity=_account_equity_for_trade(session, trade),
    )
    body = format_paper_exit_close_telegram_text(
        symbol=trade.symbol,
        direction=trade.direction,
        r_multiple=r_multiple,
        timeframe=trade.timeframe,
        exit_reason=exit_reason,
        pnl_display=pnl_display,
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
