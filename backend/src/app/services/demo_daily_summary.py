"""One Telegram summary per UTC day of governed BloFin demo trading.

Opt-in like the open and close pages: it sends only when
``TELEGRAM_SAFETY_ALERT_ALLOWLIST`` lists ``demo_daily_summary`` and the existing
bot token and chat id are set (``TELEGRAM_ALERTS_ENABLED`` does not gate it).
From 00:15 UTC, right after a governed demo reconcile that reached the venue
for every pending command, the paper worker summarizes the previous UTC day for
the configured demo organization and account, from the database only (no venue
call): opens, take-profit legs filled (``position_exit_events``), final closes
with R and the day's result, and positions open at the end of the day. Each
trade reads as coin, direction, strategy family and chart (no names, hashes or
versions). R is after the estimated 0.06% taker fee on entry and exit, against
the Journal's planned stop; dollars are about that R times the planned risk.
A quiet day still sends.

Once per organization per day: an audit row (``AUDIT_RESOURCE``, id = the day)
is written in the transaction that queues the send, under a PostgreSQL advisory
lock on (organization, day) with a short ``lock_timeout``, so a restart or a
second worker does not send it again. The send runs after commit on the
safety-alert background thread (5 s HTTP timeout), so the worker cycle never
waits on Telegram. A failed send is logged and not retried. Events recorded
after the summary is queued are not re-sent, and a day that gets no summary on
the next UTC day is skipped.

On Mondays (the summary of a Sunday) the page also lists the past seven UTC days
by strategy family and timeframe: closed trades, wins, net R after estimated taker
fees, and about how many dollars that is (net R times the Journal's planned risk),
to compare the demo with the backtest report.
"""

from __future__ import annotations

import hashlib
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import structlog
from sqlalchemy import and_, func, or_, select, text
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
from app.services.demo_trade_telegram import demo_trade_pages_enabled, strategy_family_name
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.partial_exits.flags import BLOFIN_DEMO_VENUE
from app.services.safety_alerts import queue_safety_alert

logger = structlog.get_logger(__name__)

DEMO_DAILY_SUMMARY = "demo_daily_summary"
AUDIT_RESOURCE = "demo_daily_telegram_summary"
SEND_AFTER = timedelta(minutes=15)  # late venue events from just before 00:00 get recorded first
# Reconcile reasons that mean a pending command's venue state was not read this cycle.
VENUE_MISS_MARKERS = ("unavailable", "unreadable")
_MAX_ITEMS = 15
WEEK = timedelta(days=7)
_TAKER_FEE = Decimal("0.0006")  # BloFin taker, entry and exit; the Journal has no bot fee total
_LOCK = threading.Lock()
_DONE: dict[UUID, date] = {}  # per-process shortcut; the audit row is the real guard
_MISSING_LOGGED: set[UUID] = set()


@dataclass(frozen=True, slots=True)
class DemoDaySummary:
    day: date
    opened: tuple[tuple[str, str], ...] = ()  # ("BTC short", "D-line 30m")
    tp_fills: tuple[tuple[str, int], ...] = ()  # (level, filled legs)
    # ("ZEC short", "D-line 30m", net R after fees, about-dollars)
    closed: tuple[tuple[str, str, Decimal | None, Decimal | None], ...] = ()
    still_open: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class StrategyWeekRow:
    family: str  # "D-line", "Nested Continuation", ...
    timeframe: str
    trades: int
    wins: int
    net_r: Decimal  # after estimated taker fees, summed over trades with a known R
    dollars: Decimal  # about: net R times the planned risk
    without_r: int = 0


def net_r_after_fees(
    direction: TradeDirection, entry: Decimal, stop: Decimal, close: Decimal
) -> Decimal | None:
    """R after the 0.06% taker fee on entry and exit. Size cancels out, so none is needed."""

    long = direction is TradeDirection.LONG
    risk = entry - stop if long else stop - entry
    if risk <= 0:
        return None
    move = close - entry if long else entry - close
    return ((move - (entry + close) * _TAKER_FEE) / risk).quantize(Decimal("0.01"))


def collect_strategy_week(
    session: Session, *, organization_id: UUID, account_id: UUID, start: datetime, end: datetime
) -> tuple[StrategyWeekRow, ...]:
    """Bot demo trades closed in ``[start, end)``, per strategy family and timeframe."""

    rows = session.execute(
        select(JournalTrade, UserStrategy.setup_type)
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
            JournalTrade.status == JournalTradeStatus.CLOSED,
            JournalTrade.exit_time >= start,
            JournalTrade.exit_time < end,
        )
    ).all()
    groups: dict[tuple[str, str], list[tuple[Decimal | None, Decimal | None]]] = {}
    for trade, setup_type in rows:
        family = strategy_family_name(setup_type) if setup_type is not None else "Other"
        r = None
        if None not in (trade.entry_price, trade.planned_stop_price, trade.exit_price):
            r = net_r_after_fees(
                trade.direction, trade.entry_price, trade.planned_stop_price, trade.exit_price
            )
        groups.setdefault((family, str(trade.timeframe)), []).append((r, trade.planned_risk_amount))
    out = []
    for (family, timeframe), items in sorted(groups.items()):
        known = [(r, risk) for r, risk in items if r is not None]
        out.append(
            StrategyWeekRow(
                family=family,
                timeframe=timeframe,
                trades=len(items),
                wins=sum(1 for r, _risk in known if r > 0),
                net_r=sum((r for r, _risk in known), Decimal("0")),
                dollars=sum((r * risk for r, risk in known if risk is not None), Decimal("0")),
                without_r=len(items) - len(known),
            )
        )
    return tuple(out)


def format_strategy_week_lines(
    rows: Iterable[StrategyWeekRow], *, first_day: date, last_day: date
) -> list[str]:
    rows = list(rows)
    span = f"{first_day.day} {first_day:%b} \u2013 {last_day.day} {last_day:%b}"
    lines = [f"\U0001f4c5 Last 7 days by strategy ({span})"]
    if not rows:
        return [*lines, "No demo trades closed."]
    for row in rows:
        rate = round(100 * row.wins / row.trades) if row.trades else 0
        known = row.trades - row.without_r
        avg = f", {_r_text(row.net_r / known)} per trade" if known else ""
        lines.append(
            f"\u2022 {row.family} {row.timeframe}: {row.trades} "
            f"{'trade' if row.trades == 1 else 'trades'}, {row.wins} won ({rate}%), "
            f"{_r_text(row.net_r)}{avg} (\u2248 {_usd_text(row.dollars)})"
            + (f", {row.without_r} without R" if row.without_r else "")
        )
    total_r = sum((row.net_r for row in rows), Decimal("0"))
    total_usd = sum((row.dollars for row in rows), Decimal("0"))
    trades = sum(row.trades for row in rows)
    lines.append(
        f"Week total: {trades} {'trade' if trades == 1 else 'trades'}, {_r_text(total_r)} "
        f"after fees (\u2248 {_usd_text(total_usd)})"
    )
    return lines


_LOSS_REASON_WORDS = {
    "stop_hunted": "stop hunted",
    "level_broke": "level broke",
    "gave_back": "gave back profit",
    "no_follow_through": "no follow-through",
}


def collect_week_loss_reasons(
    session: Session, *, organization_id: UUID, account_id: UUID, start: datetime, end: datetime
) -> dict[str, int]:
    """Loss reasons from the trade reviews (``review:<code>`` tags) of the week's closes."""

    tags = session.scalars(
        select(JournalTrade.tags).where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.account_id == account_id,
            JournalTrade.source == JournalTradeSource.PAPER_EXECUTION,
            JournalTrade.status == JournalTradeStatus.CLOSED,
            JournalTrade.exit_time >= start,
            JournalTrade.exit_time < end,
        )
    ).all()
    counts: Counter[str] = Counter()
    for row in tags:
        for tag in row or []:
            code = str(tag).removeprefix("review:")
            if str(tag).startswith("review:") and code in _LOSS_REASON_WORDS:
                counts[code] += 1
    return dict(counts)


def format_loss_reasons_line(reasons: dict[str, int]) -> str:
    parts = [
        f"{count} {_LOSS_REASON_WORDS[code]}"
        for code, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))
    ]
    return "\U0001f50d Why trades lost: " + ", ".join(parts)


def _month_in_review(
    session: Session, settings: Settings, organization_id: UUID, account_id: UUID, day: date
) -> str:
    """The month ending on ``day``: per strategy, loss reasons, lessons (saved for review)."""

    from app.services.demo_monthly_lessons import (
        format_month_lines,
        lessons_from,
        month_review_tags,
        save_lessons,
    )

    start = datetime.combine(day.replace(day=1), time.min, tzinfo=UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC)
    rows = collect_strategy_week(
        session, organization_id=organization_id, account_id=account_id, start=start, end=end
    )
    reviews = month_review_tags(
        session, organization_id=organization_id, account_id=account_id, start=start, end=end
    )
    lessons = lessons_from(reviews, list(rows))
    try:
        user_id = UUID(settings.governed_blofin_demo_user_id)
    except ValueError:
        user_id = None
    if lessons and user_id is not None:
        save_lessons(
            session, organization_id=organization_id, user_id=user_id, month=day, lessons=lessons
        )
    strategy_lines = (
        format_strategy_week_lines(rows, first_day=start.date(), last_day=day) if rows else []
    )
    lines = format_month_lines(day, reviews, lessons, strategy_lines)
    reasons = {code: n for code, n in reviews.items() if code in _LOSS_REASON_WORDS}
    if reasons:
        lines.insert(len(lines) - len(lessons) - 1, format_loss_reasons_line(reasons))
    return "\n".join(lines)


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


def _r_text(r: Decimal) -> str:
    return f"{r:+.2f}R".replace("-", "\u2212")


def _usd_text(value: Decimal) -> str:
    sign = "+" if value > 0 else "\u2212" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def format_demo_daily_summary_text(summary: DemoDaySummary) -> str:
    day = summary.day
    lines = [f"\U0001f4ca AlphaTrade demo \u00b7 {day:%a} {day.day} {day:%b %Y}"]
    if not summary.opened and not summary.closed:
        lines += ["", "No demo trades opened or closed today."]
    if summary.opened:
        lines += ["", f"\U0001f7e2 Opened ({len(summary.opened)})"]
        lines += _capped(
            [f"\u2022 {trade} \u00b7 {strategy}" for trade, strategy in summary.opened]
        )
    total = sum(count for _level, count in summary.tp_fills)
    levels = ", ".join(f"{level} \u00d7{count}" for level, count in summary.tp_fills)
    lines += ["", f"\U0001f3af Take-profits hit: {total}" + (f" ({levels})" if total else "")]
    if summary.closed:
        lines += ["", f"\U0001f3c1 Closed ({len(summary.closed)})"]
        rows = []
        for trade, strategy, r, usd in summary.closed:
            mark = "\u2754" if r is None else "\u2705" if r > 0 else "\u274c" if r < 0 else "\u2796"
            result = "R unknown" if r is None else _r_text(r)
            if usd is not None:
                result += f" (\u2248 {_usd_text(usd)})"
            rows.append(f"{mark} {trade} \u00b7 {strategy} \u00b7 {result}")
        lines += _capped(rows)
        known = [(r, usd) for _t, _s, r, usd in summary.closed if r is not None]
        net = sum((r for r, _ in known), Decimal("0"))
        dollars = [usd for _, usd in known if usd is not None]
        wins = sum(1 for r, _ in known if r > 0)
        losses = sum(1 for r, _ in known if r < 0)
        head = "\U0001f4c8" if net >= 0 else "\U0001f4c9"
        result = f"{head} Day result: {_r_text(net)} after fees"
        if dollars and len(dollars) == len(known):
            result += f" (\u2248 {_usd_text(sum(dollars, Decimal('0')))})"
        result += f" \u00b7 {wins} {'win' if wins == 1 else 'wins'}, "
        result += f"{losses} {'loss' if losses == 1 else 'losses'}"
        missing = len(summary.closed) - len(known)
        lines.append(result + (f" ({missing} without R)" if missing else ""))
    lines += ["", f"\u23f3 Still open ({len(summary.still_open)})"]
    lines += _capped(
        [f"\u2022 {trade} \u00b7 {strategy}" for trade, strategy in summary.still_open]
    ) or ["None"]
    return "\n".join(lines)


def _trade_label(trade: JournalTrade) -> str:
    from app.services.demo_trade_telegram import _coin

    side = "long" if trade.direction is TradeDirection.LONG else "short"
    return f"{_coin(trade.symbol)} {side}"


def _strategy_label(setup_type: object, timeframe: object) -> str:
    family = strategy_family_name(setup_type) if setup_type is not None else "Other"
    return f"{family} {timeframe}".strip()


def collect_demo_day_summary(
    session: Session, *, organization_id: UUID, account_id: UUID, day: date
) -> DemoDaySummary:
    """Read the day from the Journal and the partial-exit records. No venue call."""

    start = datetime.combine(day, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    rows = session.execute(
        select(JournalTrade, UserStrategy.setup_type)
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
    closed: list[tuple[str, str, Decimal | None, Decimal | None]] = []
    still_open: list[tuple[str, str]] = []
    for trade, setup_type in rows:
        label = _trade_label(trade)
        strategy = redact_text(_strategy_label(setup_type, trade.timeframe))[:60]
        exit_time = None if trade.exit_time is None else _aware(trade.exit_time)
        if start <= _aware(trade.entry_time) < end:
            opened.append((label, strategy))
        if (exit_time is not None and exit_time >= end) or (
            exit_time is None and trade.status is JournalTradeStatus.OPEN
        ):
            still_open.append((label, strategy))
        elif exit_time is not None and trade.status is JournalTradeStatus.CLOSED:
            entry, stop, exit_price = trade.entry_price, trade.planned_stop_price, trade.exit_price
            r = usd = None
            if entry is not None and stop is not None and exit_price is not None:
                r = net_r_after_fees(trade.direction, entry, stop, exit_price)
                if r is not None and trade.planned_risk_amount is not None:
                    usd = r * trade.planned_risk_amount
            closed.append((label, strategy, r, usd))
    legs = session.execute(
        select(PositionExitEventRow.execution_lifecycle_id, PositionExitEventRow.leg_order).where(
            PositionExitEventRow.organization_id == organization_id,
            PositionExitEventRow.venue == BLOFIN_DEMO_VENUE,
            PositionExitEventRow.execution_lifecycle_id.in_(
                select(JournalTrade.execution_lifecycle_id).where(
                    JournalTrade.organization_id == organization_id,
                    JournalTrade.account_id == account_id,
                )
            ),
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
        count_tp_fills((lifecycle, leg) for lifecycle, leg in legs),
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
        if session.get(Organization, organization_id) is None:
            if organization_id not in _MISSING_LOGGED:
                _MISSING_LOGGED.add(organization_id)
                logger.warning("demo_daily_summary_organization_missing")
            return False
        if session.get_bind().dialect.name == "postgresql":
            # A second worker (deploy overlap) waits here, then sees the first one's row.
            digest = hashlib.sha256(f"{AUDIT_RESOURCE}:{organization_id}:{day}".encode()).digest()
            session.execute(text("SET LOCAL lock_timeout = '5s'"))
            session.execute(
                select(func.pg_advisory_xact_lock(int.from_bytes(digest[:8], "big", signed=True)))
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
            message = format_demo_daily_summary_text(summary)
            if day.weekday() == 6:  # Monday's page (Sunday's summary) adds the week
                end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC)
                week = collect_strategy_week(
                    session,
                    organization_id=organization_id,
                    account_id=account_id,
                    start=end - WEEK,
                    end=end,
                )
                first = day - timedelta(days=6)
                message += "\n\n" + "\n".join(
                    format_strategy_week_lines(week, first_day=first, last_day=day)
                )
                reasons = collect_week_loss_reasons(
                    session,
                    organization_id=organization_id,
                    account_id=account_id,
                    start=end - WEEK,
                    end=end,
                )
                if reasons:
                    message += "\n" + format_loss_reasons_line(reasons)
            if (day + timedelta(days=1)).day == 1:  # the 1st's page adds the month
                message += "\n\n" + _month_in_review(
                    session, settings, organization_id, account_id, day
                )
            key = f"{DEMO_DAILY_SUMMARY}:{organization_id}:{day.isoformat()}"
            alert = ExitMonitorAlert(
                code=DEMO_DAILY_SUMMARY,
                severity="info",
                message=message,
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
