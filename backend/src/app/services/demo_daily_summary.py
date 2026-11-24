"""One Telegram summary per UTC day of governed BloFin demo trading.

Opt-in like the open and close pages: it sends only when
``TELEGRAM_SAFETY_ALERT_ALLOWLIST`` lists ``demo_daily_summary`` and the existing
bot token and chat id are set (``TELEGRAM_ALERTS_ENABLED`` does not gate it).
From 00:05 UTC the paper worker summarizes the previous UTC day for the
configured demo organization and account, from the database only (no venue
call): opens, take-profit legs filled (``position_exit_events``), final closes
with R and the day's net R, and positions open at the end of the day. R is
gross, against the Journal's planned stop (projected from the approved plan),
before fees; no currency amount is shown. A quiet day still sends.

Once per organization per day: an audit row (``AUDIT_RESOURCE``, id = the day)
is written in the transaction that queues the send, under a lock on the
organization row, so a restart or a second worker does not send it again. The
send runs after commit on the safety-alert background thread (5 s HTTP
timeout), so the worker cycle never waits on Telegram. A failed send is logged
and not retried.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.models import AuditLog, JournalTrade, Organization, UserStrategy, UserStrategyVersion
from app.db.position_exit_events import PositionExitEventRow
from app.guardrails.redaction import redact_text
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    AuditEventType,
    JournalTradeSource,
    JournalTradeStatus,
    TradeDirection,
)
from app.services.audit_service import AuditService
from app.services.demo_trade_telegram import demo_trade_pages_enabled
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.partial_exits.flags import BLOFIN_DEMO_VENUE
from app.services.safety_alerts import queue_safety_alert

logger = structlog.get_logger(__name__)

DEMO_DAILY_SUMMARY = "demo_daily_summary"
AUDIT_RESOURCE = "demo_daily_telegram_summary"
SEND_AFTER = timedelta(minutes=5)  # lets the reconciler record closes from just before 00:00
_MAX_ITEMS = 15
_LOCK = threading.Lock()
_DONE: dict[UUID, date] = {}  # per-process shortcut; the audit row is the real guard


@dataclass(frozen=True, slots=True)
class DemoDaySummary:
    day: date
    opened: tuple[tuple[str, str], ...] = ()  # (symbol, strategy)
    tp_fills: tuple[tuple[str, int], ...] = ()  # (level, filled legs)
    closed: tuple[tuple[str, str, Decimal | None], ...] = ()  # (symbol, strategy, R)
    still_open: tuple[str, ...] = ()


def reset_demo_daily_summary_memo() -> None:
    with _LOCK:
        _DONE.clear()


def final_r(
    direction: TradeDirection, entry: Decimal, stop: Decimal, close: Decimal
) -> Decimal | None:
    """Gross R of the exit against the stop, or None when the risk is not positive."""

    long = direction is TradeDirection.LONG
    risk = entry - stop if long else stop - entry
    if risk <= 0:
        return None
    return ((close - entry if long else entry - close) / risk).quantize(Decimal("0.01"))


def count_tp_fills(legs: Iterable[tuple[UUID, int | None]]) -> tuple[tuple[str, int], ...]:
    """Filled target legs per level; a leg filled in several pieces counts once."""

    counts = Counter(leg for _position, leg in set(legs) if leg is not None)
    return tuple((f"TP{leg}", counts[leg]) for leg in sorted(counts))


def _capped(items: list[str]) -> list[str]:
    extra = len(items) - _MAX_ITEMS
    return items[:_MAX_ITEMS] + ([f"and {extra} more"] if extra > 0 else [])


def format_demo_daily_summary_text(summary: DemoDaySummary) -> str:
    lines = [f"AlphaTrade DEMO daily summary, {summary.day.isoformat()} (UTC)."]
    if not summary.opened and not summary.closed:
        lines.append("No demo trades opened or closed.")
    if summary.opened:
        lines.append(f"Opened: {len(summary.opened)}")
        lines += _capped([f"- {symbol} ({strategy})" for symbol, strategy in summary.opened])
    total = sum(count for _level, count in summary.tp_fills)
    levels = ", ".join(f"{level} {count}" for level, count in summary.tp_fills)
    lines.append(f"TP fills: {total} ({levels})" if total else "TP fills: 0")
    if summary.closed:
        lines.append(f"Closed: {len(summary.closed)}")
        lines += _capped(
            [
                f"- {s} ({name}): {'R unknown' if r is None else f'{r:+}R'}"
                for s, name, r in summary.closed
            ]
        )
        known = [r for _s, _name, r in summary.closed if r is not None]
        missing = len(summary.closed) - len(known)
        net = f"Net R (gross, before fees): {sum(known, Decimal('0')):+}R"
        lines.append(net + (f" ({missing} without R)" if missing else ""))
    still_open = ", ".join(_capped(list(summary.still_open)))
    lines.append(
        f"Still open: {len(summary.still_open)}" + (f" ({still_open})" if still_open else "")
    )
    return "\n".join(lines)


def collect_demo_day_summary(
    session: Session, *, organization_id: UUID, account_id: UUID, day: date
) -> DemoDaySummary:
    """Read the day from the Journal and the partial-exit records. No venue call."""

    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    rows = session.execute(
        select(JournalTrade, UserStrategy.name, UserStrategyVersion.version)
        .outerjoin(UserStrategyVersion, UserStrategyVersion.id == JournalTrade.strategy_version_id)
        .outerjoin(
            UserStrategy,
            and_(
                UserStrategy.id == UserStrategyVersion.strategy_id,
                UserStrategy.organization_id == organization_id,
            ),
        )
        .where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.account_id == account_id,
            JournalTrade.source == JournalTradeSource.PAPER_EXECUTION,
            JournalTrade.execution_lifecycle_id.is_not(None),
            JournalTrade.status != JournalTradeStatus.CANCELLED,
            JournalTrade.entry_time < end,
            or_(JournalTrade.exit_time.is_(None), JournalTrade.exit_time >= start),
        )
        .order_by(JournalTrade.entry_time, JournalTrade.id)
    ).all()
    opened: list[tuple[str, str]] = []
    closed: list[tuple[str, str, Decimal | None]] = []
    still_open: list[str] = []
    for trade, name, version in rows:
        label = f"{name} v{version}" if name and version is not None else trade.strategy_label
        strategy = redact_text((label or "").strip())[:80] or "unlabeled"
        exit_time = None if trade.exit_time is None else _aware(trade.exit_time)
        if start <= _aware(trade.entry_time) < end:
            opened.append((trade.symbol, strategy))
        if (exit_time is not None and exit_time >= end) or (
            exit_time is None and trade.status is JournalTradeStatus.OPEN
        ):
            still_open.append(trade.symbol)
        elif exit_time is not None and trade.status is JournalTradeStatus.CLOSED:
            entry, stop, exit_price = trade.entry_price, trade.planned_stop_price, trade.exit_price
            r = None
            if entry is not None and stop is not None and exit_price is not None:
                r = final_r(trade.direction, entry, stop, exit_price)
            closed.append((trade.symbol, strategy, r))
    legs = session.execute(
        select(PositionExitEventRow.execution_lifecycle_id, PositionExitEventRow.leg_order).where(
            PositionExitEventRow.organization_id == organization_id,
            PositionExitEventRow.venue == BLOFIN_DEMO_VENUE,
            PositionExitEventRow.event_kind == "fill",
            PositionExitEventRow.fill_kind == "target",
            PositionExitEventRow.leg_order.is_not(None),
            PositionExitEventRow.occurred_at >= start,
            PositionExitEventRow.occurred_at < end,
        )
    ).all()
    return DemoDaySummary(
        day,
        tuple(opened),
        count_tp_fills(row.tuple() for row in legs),
        tuple(closed),
        tuple(still_open),
    )


def send_demo_daily_summary_if_due(
    settings: Settings, session_factory: sessionmaker[Session], *, now: datetime
) -> bool:
    """Queue the previous UTC day's summary once. True when queued; never raises."""

    if not demo_trade_pages_enabled(settings, DEMO_DAILY_SUMMARY):
        return False
    try:
        return _send_if_due(settings, session_factory, now=_aware(now))
    except Exception:
        logger.warning("demo_daily_summary_failed", exc_info=True)
        return False


def _send_if_due(settings: Settings, factory: sessionmaker[Session], *, now: datetime) -> bool:
    try:
        organization_id = UUID(settings.governed_blofin_demo_organization_id)
        account_id = UUID(settings.governed_blofin_demo_account_id)
    except ValueError:
        return False
    if now - datetime.combine(now.date(), time.min, tzinfo=UTC) < SEND_AFTER:
        return False
    day = now.date() - timedelta(days=1)
    with _LOCK:
        if _DONE.get(organization_id) == day:
            return False
    with factory() as session:
        # A second worker (deploy overlap) waits here, then sees the first one's row.
        session.execute(
            select(Organization.id).where(Organization.id == organization_id).with_for_update()
        )
        claimed = session.scalar(
            select(AuditLog.id).where(
                AuditLog.organization_id == organization_id,
                AuditLog.resource_type == AUDIT_RESOURCE,
                AuditLog.resource_id == day.isoformat(),
            )
        )
        if claimed is None:
            summary = collect_demo_day_summary(
                session, organization_id=organization_id, account_id=account_id, day=day
            )
            AuditService(session, strict_mode=True).record(
                AuditRecordCreate(
                    request_id=f"{AUDIT_RESOURCE}:{day.isoformat()}",
                    trace_id=uuid4().hex,
                    event_type=AuditEventType.DEMO_DAILY_SUMMARY_QUEUED,
                    resource_type=AUDIT_RESOURCE,
                    organization_id=organization_id,
                    resource_id=day.isoformat(),
                    metadata={"opened": len(summary.opened), "closed": len(summary.closed)},
                    timestamp=now,
                )
            )
            key = f"{DEMO_DAILY_SUMMARY}:{organization_id}:{day.isoformat()}"
            alert = ExitMonitorAlert(
                code=DEMO_DAILY_SUMMARY,
                severity="info",
                message=format_demo_daily_summary_text(summary),
                fields={"dedup_key": key, "organization_id": str(organization_id)},
                telegram_verbatim=True,
                background=True,
            )
            queue_safety_alert(session, settings, alert)  # sent after commit, in the background
        session.commit()
    with _LOCK:
        _DONE[organization_id] = day
    return claimed is None


def _aware(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
