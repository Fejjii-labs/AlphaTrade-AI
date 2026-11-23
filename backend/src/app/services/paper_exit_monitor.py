"""Stop and target exits for open canonical internal paper trades.

Decisions use closed candles supplied by the caller. A hit is recorded only
through :meth:`ExecutionService.close_canonical_paper_plan` (see the worker
adapter). This module does not update journal rows and does not call an
exchange. Manual BloFin demo trades and governed BloFin demo plans are outside
the query and the parser.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.db.canonical_candidates import CanonicalCandidateRow
from app.db.canonical_trade_plans import (
    PLAN_AUTHORITY_CANONICAL,
    CanonicalTradePlanLineageRow,
)
from app.db.models import JournalTrade, TradePlanRevision
from app.guardrails.redaction import redact_text
from app.market_contracts.identity import interval_timedelta
from app.schemas.common import JournalTradeStatus, Timeframe, TradeDirection
from app.schemas.execution_protocol import ClosePaperExitFill, ClosePaperPlanRequest
from app.services.paper_close_economics import allocate_by_quantity
from app.services.partial_exits.ladder import LadderRefusal
from app.services.partial_exits.paper_terms import (
    PartialExitTerms,
    is_ladder_payload,
    parse_partial_exit_terms,
)
from app.services.partial_exits.position import (
    ExitFill,
    PositionExitState,
    StopMove,
    apply_fill,
    apply_stop_move,
    plan_breakeven_move,
    rebuild_position_state,
)
from app.services.partial_exits.runner import trail_runner_stop

logger = structlog.get_logger(__name__)

INTERNAL_PAPER_VENUE = "PAPER_INTERNAL"
INTERNAL_PAPER_POLICY = "paper-internal-entry/v1"
EXIT_CHANNEL = "PAPER_EXIT_MONITOR"
EXIT_ACTOR = "paper_exit_monitor"
# A crossing whose bar ended long before this evaluation is a reconstruction.
# It is journaled and kept out of the breaker, the daily lock, and ADR-109.
# A close processed within this window is labelled live, including across UTC
# midnight. A losing live close that is already older than the loss pause or
# one candle does not use that bar's breaker clock (AT-ADR-128).
HISTORICAL_BACKFILL_AFTER = timedelta(hours=24)
# Repeated degraded or refused cycles. Well under the 24-hour backfill horizon.
DEGRADED_REPEAT_ALERT_AFTER = timedelta(minutes=15)
# Worker wall clock versus a trusted clock. Inside one 15-minute bar.
MAX_CLOCK_SKEW = timedelta(seconds=15)
# A fill recorded in this gap after a closed-bar boundary still belongs to
# that boundary. The bar that opens there is the first post-entry bar.
ENTRY_SETTLEMENT_GAP = timedelta(seconds=90)
EXIT_ORIGIN_BACKFILL = "backfill"
EXIT_ORIGIN_LIVE = "live"
CALIBRATION_EXCLUDED = "excluded"
CALIBRATION_ELIGIBLE = "eligible"
EXIT_REASON_STOP: Literal["stop_hit"] = "stop_hit"
EXIT_REASON_TARGET: Literal["target_hit"] = "target_hit"
SAME_BAR_ASSUMPTION = "stop_first"
SAME_BAR_NONE = "none"
GAP_THROUGH_OPEN = "gap_through_open"

ExitReason = Literal["stop_hit", "target_hit"]
_SKIP_CLOSE_REASONS = frozenset(
    {
        "already_closed",
        "idempotency_conflict",
        "demo_synthetic_close_forbidden",
    }
)
# A failed close waits before the next attempt on that trade, doubling per
# consecutive failure. The wait skips the candle read as well as the close.
CLOSE_FAILURE_BACKOFF_BASE = timedelta(minutes=1)
CLOSE_FAILURE_BACKOFF_MAX = timedelta(hours=1)
# Attribution identity conflicts do not heal on retry. The trade waits the
# maximum at once and is retried hourly so a code or data repair still lands.
_QUARANTINE_CLOSE_REASONS = frozenset(
    {
        "learning_venue_mode_conflict",
        "candidate_lifecycle_conflict",
        "lifecycle_candidate_conflict",
        "assessment_identity_conflict",
        "evidence_window_conflict",
        "uniqueness_tuple_conflict",
        "candidate_identity_conflict",
        "cross_tenant_attribution",
        "attribution_store_inconsistent",
        "reject_skip_cannot_execute",
    }
)


@dataclass(frozen=True, slots=True)
class ExitBar:
    """One closed candle the monitor is allowed to read."""

    interval_start: datetime
    interval_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    source_event_id: str
    content_hash: str
    venue: str = ""


@dataclass(frozen=True, slots=True)
class FetchedBars:
    """Closed bars plus whether they reach back to the trade entry."""

    bars: tuple[ExitBar, ...]
    covers_entry: bool
    source_name: str
    candle_venue: str = ""
    switched_from: str = ""
    # True when evaluation fell inside the post-close settlement guard, so the
    # venue refused the page. The read is deferred to the next cycle; it is
    # not a gap in the series.
    settling: bool = False


@dataclass(frozen=True, slots=True)
class OpenInternalPaperTrade:
    """One open canonical internal-paper fill with its immutable exit levels."""

    journal_trade_id: UUID
    organization_id: UUID
    user_id: UUID
    account_id: UUID
    revision_id: UUID
    command_id: UUID
    symbol: str
    timeframe: Timeframe
    direction: TradeDirection
    entry_price: Decimal
    entry_time: datetime
    stop: Decimal
    target: Decimal
    fees: Decimal
    funding: Decimal
    slippage: Decimal
    evidence_venue: str | None = None
    evidence_source_family: str | None = None
    evidence_is_live: bool | None = None
    evidence_is_mock: bool | None = None
    # Set only for a multi-target or runner plan with the paper partial-exit
    # flag on. ``stop`` is then the initial stop and ``target`` the last
    # scheduled target; the ladder decides exits, never ``decide_exit``.
    partial: PartialExitTerms | None = None


@dataclass(frozen=True, slots=True)
class ExitDecision:
    """The first closed bar that touched a level. Both-in-bar chooses the stop."""

    trade: OpenInternalPaperTrade
    reason: ExitReason
    exit_price: Decimal
    occurred_at: datetime
    evidence: dict[str, str]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ExitMonitorAlert:
    """A structured exit-monitor alert. A log line is not this object."""

    code: str
    severity: str
    message: str
    fields: dict[str, str]
    # Informational paper trade exits use ``message`` as the full Telegram body.
    telegram_verbatim: bool = False
    # Sent on a background thread after commit, so the caller never waits on Telegram.
    background: bool = False

    def as_log(self) -> dict[str, str]:
        payload = {"code": self.code, "severity": self.severity, "message": self.message}
        payload.update(self.fields)
        return payload


@dataclass(frozen=True, slots=True)
class MonitorReport:
    """One pass. ``disarmed`` performs no reads and no writes."""

    status: str
    examined: int = 0
    closed: int = 0
    replayed: int = 0
    would_close: int = 0
    skipped: int = 0
    refused: int = 0
    errors: int = 0
    error: str = ""
    alerts: tuple[ExitMonitorAlert, ...] = ()
    settling: int = 0
    organization_ids: tuple[UUID, ...] = ()
    backed_off: int = 0
    # Open trades whose close is quarantined (identity conflict): they stay
    # OPEN, hold a max_open_positions slot and their stale mark (M5).
    quarantined: int = 0
    # Partial-exit trades that recorded a reduction or stop move but stayed open.
    reduced: int = 0
    # Partial-exit trades held by the kill switch or a paused worker.
    held: int = 0


@dataclass(frozen=True, slots=True)
class CloseFailureBackoff:
    """Consecutive close failures for one trade and when it may be retried."""

    failures: int
    retry_at: datetime
    reason: str
    quarantined: bool = False


def next_close_backoff(
    previous: CloseFailureBackoff | None,
    *,
    failed_at: datetime,
    reason: str,
) -> CloseFailureBackoff:
    """Exponential wait after a failed close. Identity conflicts wait the maximum."""

    failures = 1 if previous is None else previous.failures + 1
    quarantined = reason in _QUARANTINE_CLOSE_REASONS
    if quarantined:
        delay = CLOSE_FAILURE_BACKOFF_MAX
    else:
        delay = min(
            CLOSE_FAILURE_BACKOFF_BASE * (2 ** min(failures - 1, 16)),
            CLOSE_FAILURE_BACKOFF_MAX,
        )
    return CloseFailureBackoff(
        failures=failures,
        retry_at=failed_at + delay,
        reason=reason,
        quarantined=quarantined,
    )


class PaperExitCloser:
    """Records one close. The production adapter calls the paper close service."""

    def close(self, request: ClosePaperPlanRequest) -> object:
        raise NotImplementedError


def exit_idempotency_key(command_id: UUID) -> str:
    """One stable close identity per execution command."""

    return f"paper-exit-monitor:{command_id}"


def exit_monitor_backfill(request: ClosePaperPlanRequest) -> bool:
    """True when the monitor labelled this close as a historical reconstruction."""

    evidence = request.exit_evidence
    if not evidence:
        return False
    return evidence.get("exit_origin") == EXIT_ORIGIN_BACKFILL


def counts_for_adr109_calibration(request: ClosePaperPlanRequest) -> bool:
    """ADR-109 samples include a live monitor close and exclude a backfill.

    A human close has no monitor evidence, so it is not a monitor sample.
    This predicate does not write a row. The close path does not invent a
    walk-forward fold, so no calibration sample is inserted here.
    """

    if exit_monitor_backfill(request):
        return False
    evidence = request.exit_evidence
    if evidence is None:
        return False
    return evidence.get("calibration_sample") == CALIBRATION_ELIGIBLE


def downtime_is_historical_backfill(occurred_at: datetime, evaluated_at: datetime) -> bool:
    """True when the worker was down, or not evaluating, for 24 hours or more.

    Downtime is the gap from the crossing bar's end to this evaluation.
    Exactly 24 hours is a backfill. A shorter gap is labelled live, including
    a bar that ended on the previous UTC date and is processed after
    midnight. A naive timestamp is a backfill so it cannot move the breaker.
    The close is still journaled. A backfill does not advance the
    consecutive-loss streak, does not sync the daily lock, and is not an
    ADR-109 sample. A backfill with a net loss alerts and refuses new entries
    instead. A losing live close older than the loss pause or one candle
    does the same, without changing this 24-hour rule (AT-ADR-128).
    """

    if occurred_at.tzinfo is None or evaluated_at.tzinfo is None:
        return True
    occurred = occurred_at.astimezone(UTC)
    evaluated = evaluated_at.astimezone(UTC)
    return evaluated - occurred >= HISTORICAL_BACKFILL_AFTER


def backfill_loss_needs_protection(
    request: ClosePaperPlanRequest,
    *,
    replayed: bool,
    net_pnl: Decimal | None,
) -> bool:
    """A journaled backfill with a net loss still has to protect the account.

    The live loss streak and the daily lock stay unchanged. A replay does
    not ask again. A win, a zero result, and a missing PnL do not. A
    non-finite PnL fails closed and does need protection.
    """

    if replayed or not exit_monitor_backfill(request):
        return False
    if not isinstance(net_pnl, Decimal) or not net_pnl.is_finite():
        return isinstance(net_pnl, Decimal)
    return net_pnl < 0


def database_now(session: Session) -> datetime | None:
    """Database ``now()``, or None when the session cannot return an aware time."""

    try:
        value = session.scalar(select(func.now()))
    except Exception:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        return None
    return value.astimezone(UTC)


def _counts_as_monitor_loss(net_pnl: Decimal | None) -> bool:
    """A negative result, or a non-finite decimal, counts as a loss.

    A missing value does not. A non-finite value fails closed once the close
    is otherwise a live monitor loss.
    """

    if not isinstance(net_pnl, Decimal):
        return False
    if not net_pnl.is_finite():
        return True
    return net_pnl < 0


def _is_labelled_live_monitor_close(request: ClosePaperPlanRequest) -> bool:
    """True when the monitor labelled this close live. A human close is not."""

    if exit_monitor_backfill(request):
        return False
    evidence = request.exit_evidence
    if not evidence:
        return False
    return evidence.get("exit_origin") == EXIT_ORIGIN_LIVE


def live_monitor_loss_needs_clock(
    request: ClosePaperPlanRequest,
    *,
    replayed: bool,
    net_pnl: Decimal | None,
) -> bool:
    """Whether staleness has to be measured against database ``now()``."""

    if replayed or not _is_labelled_live_monitor_close(request):
        return False
    return _counts_as_monitor_loss(net_pnl)


def _older_than(age: timedelta, window: timedelta) -> bool:
    """Strictly older than a positive window. Zero and negative windows do not match."""

    return window > timedelta(0) and age > window


def stale_live_loss_needs_protection(
    request: ClosePaperPlanRequest,
    *,
    replayed: bool,
    net_pnl: Decimal | None,
    evaluated_at: datetime | None,
    pause_window: timedelta,
    candle_period: timedelta | None,
) -> bool:
    """A live losing close whose bar clock can no longer protect the account.

    The pause window is the paper loss cooldown. The candle period is the
    trade timeframe. Older than either one, while the close is still labelled
    live, is stale. Equality with either bound stays on the live breaker
    clock. A labelled backfill, including exactly 24 hours, is decided by
    :func:`backfill_loss_needs_protection` and returns false here.

    A live label on a bar that is already 24 hours old still protects, so
    that bar's clock is not written into the breakers. A missing database
    clock, a naive evaluation time, or an unreadable candle period fails
    closed. A replay, a win, a zero, and a missing PnL do not.
    """

    if not live_monitor_loss_needs_clock(request, replayed=replayed, net_pnl=net_pnl):
        return False
    if candle_period is None or evaluated_at is None or evaluated_at.tzinfo is None:
        return True
    if request.occurred_at.tzinfo is None:
        return True
    age = evaluated_at.astimezone(UTC) - request.occurred_at.astimezone(UTC)
    if age >= HISTORICAL_BACKFILL_AFTER:
        # A live label must not write breakers from a bar that is already a
        # backfill. The labelled backfill path is the normal case and is
        # decided before this function.
        return True
    return _older_than(age, pause_window) or _older_than(age, candle_period)


def classify_exit_origin(occurred_at: datetime, evaluated_at: datetime) -> str:
    """Label a reconstruction from before the monitor tracked the trade.

    See :func:`downtime_is_historical_backfill`. The open October 6 trades
    close on a later armed run, more than a day after those bars.
    """

    if downtime_is_historical_backfill(occurred_at, evaluated_at):
        return EXIT_ORIGIN_BACKFILL
    return EXIT_ORIGIN_LIVE


def candle_series_block(
    bars: Sequence[ExitBar],
    *,
    timeframe: Timeframe,
    evaluated_at: datetime,
    entry_time: datetime | None = None,
) -> str | None:
    """Refuse a stop or target read when the closed series is unusable.

    A leading gap means the earliest closed bar starts after the trade
    entry, so a stretch at the start of the series is missing. An internal
    gap is two closed bars that do not meet, or a bar that is not one
    timeframe long. Stale means the newest closed bar ended at least one full
    timeframe before evaluation, so a later stop or target may be missing.
    No hole is filled. ``None`` means the series may be evaluated.
    """

    step = interval_timedelta(timeframe)
    if step.total_seconds() <= 0 or evaluated_at.tzinfo is None:
        return "gap"
    closed = [
        bar
        for bar in bars
        if bar.interval_start.tzinfo is not None
        and bar.interval_end.tzinfo is not None
        and bar.interval_end <= evaluated_at
    ]
    ordered = sorted(closed, key=lambda bar: (bar.interval_start, bar.interval_end))
    if entry_time is not None:
        if entry_time.tzinfo is None:
            return "leading_gap"
        entry = entry_time.astimezone(UTC)
        earliest = ordered[0].interval_start.astimezone(UTC) if ordered else None
        if earliest is None or earliest > entry:
            return "leading_gap"
    if not ordered:
        return "stale"
    previous: ExitBar | None = None
    for bar in ordered:
        if bar.interval_end - bar.interval_start != step:
            return "gap"
        if previous is not None and bar.interval_start != previous.interval_end:
            return "gap"
        previous = bar
    last = ordered[-1]
    if evaluated_at - last.interval_end >= step:
        return "stale"
    return None


@dataclass(frozen=True, slots=True)
class DegradationStreak:
    """How long degraded or refused cycles have continued in this process."""

    started_at: datetime
    cycles: int
    last_alert_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DegradationWatch:
    """The streak after one cycle, and the alert when the repeat is long enough."""

    streak: DegradationStreak | None
    alert: ExitMonitorAlert | None


_REPEATED_FAILURE = frozenset({"degraded", "refused"})
STATUS_BACKING_OFF = "backing_off"


def observe_repeated_degradation(
    streak: DegradationStreak | None,
    *,
    status: str,
    observed_at: datetime,
    threshold: timedelta,
    error: str = "",
    refused: int = 0,
) -> DegradationWatch:
    """Alert when degraded or refused cycles repeat for the configured duration.

    One cycle does not alert. A healthy cycle clears the streak. The
    threshold is the caller's value; settings keep it at or under one hour.
    A later alert waits for another full threshold so a long outage is
    visible without a row on every poll.
    """

    failed = status in _REPEATED_FAILURE or refused > 0
    if not failed and status == STATUS_SETTLING:
        # Deferred reads are neither healthy nor failed. Keep the streak as is.
        return DegradationWatch(streak=streak, alert=None)
    if not failed or observed_at.tzinfo is None:
        return DegradationWatch(streak=None, alert=None)
    if threshold <= timedelta(0):
        threshold = DEGRADED_REPEAT_ALERT_AFTER
    moment = observed_at.astimezone(UTC)
    if streak is None or streak.started_at.tzinfo is None:
        started = moment
        cycles = 1
        last_alert: datetime | None = None
    else:
        started = streak.started_at.astimezone(UTC)
        cycles = streak.cycles + 1
        last_alert = streak.last_alert_at
    elapsed = moment - started
    alert: ExitMonitorAlert | None = None
    due = cycles >= 2 and elapsed >= threshold
    if due and (last_alert is None or moment - last_alert >= threshold):
        alert = ExitMonitorAlert(
            code="degraded_repeat",
            severity="warning",
            message=(
                "Paper exit monitor cycles stayed degraded or refused "
                "past the configured threshold."
            ),
            fields={
                "status": status,
                "error": error[:200],
                "cycles": str(cycles),
                "elapsed_seconds": str(int(elapsed.total_seconds())),
                "threshold_seconds": str(int(threshold.total_seconds())),
                "refused": str(refused),
                "dedup_key": f"degraded_repeat:{started.isoformat()}",
            },
        )
        last_alert = moment
    return DegradationWatch(
        streak=DegradationStreak(started_at=started, cycles=cycles, last_alert_at=last_alert),
        alert=alert,
    )


def clock_refusal_reason(
    wall: datetime,
    trusted: Sequence[datetime],
    *,
    max_skew: timedelta,
) -> str | None:
    """Refuse when the wall clock is not within ``max_skew`` of every trusted time.

    No trusted time, or a naive timestamp, is ``clock_untrusted``. A larger
    gap, including trusted sources that disagree with each other, is
    ``clock_skew``. Equality with the threshold is still accepted.
    """

    if wall.tzinfo is None or max_skew < timedelta(0):
        return "clock_untrusted"
    if not trusted:
        return "clock_untrusted"
    wall_utc = wall.astimezone(UTC)
    normalized: list[datetime] = []
    for moment in trusted:
        if moment.tzinfo is None:
            return "clock_untrusted"
        normalized.append(moment.astimezone(UTC))
    for moment in normalized:
        if abs(wall_utc - moment) > max_skew:
            return "clock_skew"
    first = normalized[0]
    for moment in normalized[1:]:
        if abs(first - moment) > max_skew:
            return "clock_skew"
    return None


def build_clock_alert(
    *,
    reason: str,
    wall: datetime,
    trusted: Sequence[datetime],
    max_skew: timedelta,
    trusted_source: str,
) -> ExitMonitorAlert:
    """The structured alert for one refused clock check."""

    skew = 0.0
    if trusted and wall.tzinfo is not None and trusted[0].tzinfo is not None:
        skew = abs((wall.astimezone(UTC) - trusted[0].astimezone(UTC)).total_seconds())
    code = "clock_skew" if reason == "clock_skew" else "clock_untrusted"
    return ExitMonitorAlert(
        code=code,
        severity="critical",
        message=(
            "Paper exit monitor refused the cycle because the worker clock "
            "does not agree with a trusted time."
        ),
        fields={
            "reason": reason,
            "threshold_seconds": str(int(max_skew.total_seconds())),
            "skew_seconds": f"{skew:.3f}",
            "trusted_source": trusted_source,
            "refusal": "cycle_refused",
            "dedup_key": f"clock:{reason}",
        },
    )


def fill_entry_time(*, trigger_end: datetime, clock: datetime) -> datetime:
    """Journal entry time is the signal bar's end, not the watcher clock.

    The clock is the scan's evaluation instant, a few seconds after the bar
    close. Storing that instant makes the first post-entry bar look like it
    opened before the fill. A naive trigger end falls back to the clock.
    """

    if trigger_end.tzinfo is None:
        return clock
    return trigger_end.astimezone(UTC)


def entry_eligibility_time(entry_time: datetime, timeframe: Timeframe) -> datetime:
    """The earliest bar start that may exit this fill.

    A fill at a closed-bar boundary, or in the short settlement gap after it,
    uses that boundary. The bar which opens there is the first post-entry bar.
    A fill deeper inside a bar keeps its own timestamp, so that bar's range
    is not treated as having printed after the fill.
    """

    if entry_time.tzinfo is None:
        return entry_time
    moment = entry_time.astimezone(UTC)
    boundary = _boundary_at_or_before(moment, interval_timedelta(timeframe))
    if moment - boundary <= ENTRY_SETTLEMENT_GAP:
        return boundary
    return moment


def _boundary_at_or_before(moment: datetime, step: timedelta) -> datetime:
    size = step.total_seconds()
    if size <= 0:
        return moment
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = (moment - epoch).total_seconds()
    floored = elapsed - (elapsed % size)
    return epoch + timedelta(seconds=floored)


def is_internal_paper_plan(
    *,
    plan_authority: str,
    execution_venue: str,
    execution_policy_version: str,
) -> bool:
    """Canonical internal paper only. Demo and manual-demo plans are excluded."""

    return (
        plan_authority == PLAN_AUTHORITY_CANONICAL
        and execution_venue == INTERNAL_PAPER_VENUE
        and execution_policy_version == INTERNAL_PAPER_POLICY
    )


def open_internal_paper_statement() -> Select[
    tuple[JournalTrade, TradePlanRevision, dict[str, object]]
]:
    """Open journal rows joined to canonical internal-paper plan revisions."""

    return (
        select(JournalTrade, TradePlanRevision, CanonicalCandidateRow.evidence_identity)
        .join(TradePlanRevision, TradePlanRevision.id == JournalTrade.trade_plan_revision_id)
        .outerjoin(
            CanonicalTradePlanLineageRow,
            CanonicalTradePlanLineageRow.revision_id == TradePlanRevision.id,
        )
        .outerjoin(
            CanonicalCandidateRow,
            CanonicalCandidateRow.candidate_id == CanonicalTradePlanLineageRow.candidate_id,
        )
        .where(
            JournalTrade.status == JournalTradeStatus.OPEN,
            JournalTrade.execution_lifecycle_id.is_not(None),
            JournalTrade.trade_plan_revision_id.is_not(None),
            TradePlanRevision.plan_authority == PLAN_AUTHORITY_CANONICAL,
            TradePlanRevision.execution_venue == INTERNAL_PAPER_VENUE,
            TradePlanRevision.execution_policy_version == INTERNAL_PAPER_POLICY,
        )
        .order_by(JournalTrade.entry_time.asc())
    )


def load_open_internal_paper_trades(
    session: Session,
    *,
    partial_exits_enabled: bool = False,
) -> tuple[tuple[OpenInternalPaperTrade, ...], int]:
    """Return parsed open trades and how many scoped rows were rejected."""

    rows = session.execute(open_internal_paper_statement()).all()
    trades: list[OpenInternalPaperTrade] = []
    rejected = 0
    for trade, plan, evidence_identity in rows:
        parsed = parse_open_trade(
            journal_trade_id=trade.id,
            organization_id=trade.organization_id,
            user_id=trade.user_id,
            account_id=trade.account_id,
            command_id=trade.execution_lifecycle_id,
            direction=trade.direction,
            entry_price=trade.entry_price,
            entry_time=trade.entry_time,
            journal_symbol=trade.symbol,
            journal_timeframe=trade.timeframe,
            plan_authority=plan.plan_authority,
            execution_venue=plan.execution_venue,
            execution_policy_version=plan.execution_policy_version,
            execution_instrument=plan.execution_instrument,
            plan_organization_id=plan.organization_id,
            plan_user_id=plan.user_id,
            plan_account_id=plan.account_id,
            revision_id=plan.id,
            semantic_payload=plan.semantic_payload,
            evidence_identity=evidence_identity,
            position_size=trade.size,
            partial_exits_enabled=partial_exits_enabled,
        )
        if parsed is None:
            rejected += 1
            logger.info(
                "paper_exit_monitor_trade_rejected",
                journal_trade_id=str(trade.id),
            )
            continue
        trades.append(parsed)
    return tuple(trades), rejected


def parse_open_trade(
    *,
    journal_trade_id: UUID,
    organization_id: UUID,
    user_id: UUID,
    account_id: UUID | None,
    command_id: UUID | None,
    direction: TradeDirection,
    entry_price: Decimal | None,
    entry_time: datetime | None,
    journal_symbol: str,
    journal_timeframe: str,
    plan_authority: str,
    execution_venue: str,
    execution_policy_version: str,
    execution_instrument: str,
    plan_organization_id: UUID,
    plan_user_id: UUID,
    plan_account_id: UUID,
    revision_id: UUID,
    semantic_payload: object,
    evidence_identity: object | None = None,
    position_size: Decimal | None = None,
    partial_exits_enabled: bool = False,
) -> OpenInternalPaperTrade | None:
    """Build one monitor trade, or return None when the row is out of scope.

    A plan with one full-size target and a disabled runner takes the existing
    path, whatever the flag. A plan with several targets or a runner is out of
    scope unless ``partial_exits_enabled``; then its ladder must parse and
    size exactly against the recorded position, or the row is refused. A
    BloFin venue or policy is always left untouched.
    """

    if not is_internal_paper_plan(
        plan_authority=plan_authority,
        execution_venue=execution_venue,
        execution_policy_version=execution_policy_version,
    ):
        return None
    if account_id is None or command_id is None or entry_price is None or entry_time is None:
        return None
    if entry_time.tzinfo is None:
        return None
    if (
        organization_id != plan_organization_id
        or user_id != plan_user_id
        or account_id != plan_account_id
    ):
        return None
    if not isinstance(semantic_payload, dict):
        return None
    symbol = execution_instrument.strip().upper()
    if not symbol or symbol != journal_symbol.strip().upper():
        return None
    payload_timeframe = semantic_payload.get("timeframe")
    if not isinstance(payload_timeframe, str) or payload_timeframe != journal_timeframe:
        return None
    try:
        timeframe = Timeframe(payload_timeframe)
    except ValueError:
        return None
    levels = _exit_levels(semantic_payload)
    partial: PartialExitTerms | None = None
    if levels is None:
        partial = _partial_terms(
            semantic_payload,
            enabled=partial_exits_enabled,
            direction=direction,
            entry_price=entry_price,
            position_size=position_size,
            journal_trade_id=journal_trade_id,
        )
        if partial is None:
            return None
        levels = (
            partial.initial_stop,
            partial.final_target,
            partial.fees,
            partial.funding,
            partial.slippage,
        )
    stop, target, fees, funding, slippage = levels
    if direction is TradeDirection.LONG and not (stop < entry_price < target):
        return None
    if direction is TradeDirection.SHORT and not (target < entry_price < stop):
        return None
    if direction not in {TradeDirection.LONG, TradeDirection.SHORT}:
        return None
    venue, family, is_live, is_mock = evidence_flags(evidence_identity)
    return OpenInternalPaperTrade(
        journal_trade_id=journal_trade_id,
        organization_id=organization_id,
        user_id=user_id,
        account_id=account_id,
        revision_id=revision_id,
        command_id=command_id,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        entry_price=entry_price,
        entry_time=entry_time,
        stop=stop,
        target=target,
        fees=fees,
        funding=funding,
        slippage=slippage,
        evidence_venue=venue,
        evidence_source_family=family,
        evidence_is_live=is_live,
        evidence_is_mock=is_mock,
        partial=partial,
    )


def _partial_terms(
    payload: dict[str, object],
    *,
    enabled: bool,
    direction: TradeDirection,
    entry_price: Decimal,
    position_size: Decimal | None,
    journal_trade_id: UUID,
) -> PartialExitTerms | None:
    risk = payload.get("risk_and_exits")
    if not enabled or not isinstance(risk, dict) or not is_ladder_payload(risk):
        return None
    terms = parse_partial_exit_terms(
        payload,
        direction=direction,
        entry_price=entry_price,
        position_size=position_size,
    )
    if isinstance(terms, LadderRefusal):
        logger.warning(
            "paper_exit_monitor_ladder_refused",
            journal_trade_id=str(journal_trade_id),
            reason=terms.reason,
            **terms.detail,
        )
        return None
    if terms.ladder.merges:
        logger.info(
            "paper_exit_monitor_ladder_merged",
            journal_trade_id=str(journal_trade_id),
            merges=",".join(terms.ladder.merges),
        )
    return terms


def evidence_flags(
    identity: object | None,
) -> tuple[str | None, str | None, bool | None, bool | None]:
    """Venue, source family, is_live, and is_mock from a stored evidence identity."""

    if not isinstance(identity, dict):
        return None, None, None, None
    venue = identity.get("venue")
    source = identity.get("source")
    provenance = identity.get("provenance")
    family = source.get("family") if isinstance(source, dict) else None
    if not isinstance(family, str) and isinstance(provenance, dict):
        recorded = provenance.get("source_family")
        family = recorded if isinstance(recorded, str) else None
    is_live = provenance.get("is_live") if isinstance(provenance, dict) else None
    is_mock = provenance.get("is_mock") if isinstance(provenance, dict) else None
    return (
        venue if isinstance(venue, str) else None,
        family if isinstance(family, str) else None,
        is_live if isinstance(is_live, bool) else None,
        is_mock if isinstance(is_mock, bool) else None,
    )


def decide_exit(
    trade: OpenInternalPaperTrade,
    bars: Sequence[ExitBar],
    *,
    source_name: str,
    evaluated_at: datetime,
    candle_venue: str = "",
) -> ExitDecision | None:
    """First closed bar after the entry that touches a level.

    Live internal-paper entries are taken at a closed-bar boundary. The entry
    time is that bar's ``interval_end``, so the fill bar has an earlier
    ``interval_start`` and is not eligible. The next bar, whose
    ``interval_start`` equals the entry time, is the first bar that can exit
    the trade. A fill stored a few seconds later, from the watcher clock,
    still uses that boundary. A touch inside the fill bar is ignored. A fill
    recorded deeper inside a bar skips that bar, because its range is not
    known to have printed after the fill. A bar that opens through the stop
    exits at that open. When one eligible bar touches both levels without
    gapping through the stop, the stop is the exit and the evidence records
    ``stop_first``.
    """

    eligible_from = entry_eligibility_time(trade.entry_time, trade.timeframe)
    eligible = [
        bar
        for bar in bars
        if bar.interval_start >= eligible_from
        and bar.interval_end <= evaluated_at
        and bar.interval_start.tzinfo is not None
        and bar.interval_end.tzinfo is not None
    ]
    for bar in sorted(eligible, key=lambda item: item.interval_start):
        gapped = _opens_through_stop(trade, bar)
        touch = _touch(trade, bar)
        if touch is None and not gapped:
            continue
        stop_exit = gapped or touch in {"stop", "both"}
        reason: ExitReason = EXIT_REASON_STOP if stop_exit else EXIT_REASON_TARGET
        if gapped:
            price = bar.open
            assumption = GAP_THROUGH_OPEN
        else:
            price = trade.stop if stop_exit else trade.target
            assumption = SAME_BAR_ASSUMPTION if touch == "both" else SAME_BAR_NONE
        origin = classify_exit_origin(bar.interval_end, evaluated_at)
        evidence = _evidence(
            trade,
            bar,
            source_name=source_name,
            level="stop" if stop_exit else "target",
            assumption=assumption,
        )
        evidence["exit_origin"] = origin
        evidence["calibration_sample"] = (
            CALIBRATION_EXCLUDED if origin == EXIT_ORIGIN_BACKFILL else CALIBRATION_ELIGIBLE
        )
        if candle_venue:
            evidence["candle_venue"] = candle_venue
        return ExitDecision(
            trade=trade,
            reason=reason,
            exit_price=price,
            occurred_at=bar.interval_end,
            evidence=evidence,
            idempotency_key=exit_idempotency_key(trade.command_id),
        )
    return None


def close_request_for(decision: ExitDecision) -> ClosePaperPlanRequest:
    """The request ``POST /paper-plan/close`` would carry, plus the monitor identity."""

    trade = decision.trade
    return ClosePaperPlanRequest(
        organization_id=trade.organization_id,
        user_id=trade.user_id,
        account_id=trade.account_id,
        revision_id=trade.revision_id,
        command_id=trade.command_id,
        exit_price=decision.exit_price,
        fees=trade.fees,
        funding=trade.funding,
        slippage=trade.slippage,
        exit_reason=decision.reason,
        occurred_at=decision.occurred_at,
        idempotency_key=decision.idempotency_key,
        exit_channel=EXIT_CHANNEL,
        exit_actor=EXIT_ACTOR,
        exit_evidence=dict(decision.evidence),
    )


@dataclass(frozen=True, slots=True)
class PartialExitStep:
    """New reductions and stop moves from one pass, and the state after them.

    ``evidence`` is keyed by fill id or stop-move key.
    """

    trade: OpenInternalPaperTrade
    fills: tuple[ExitFill, ...]
    stop_moves: tuple[StopMove, ...]
    state: PositionExitState
    evidence: dict[str, dict[str, str]]


class PartialExitRecorder:
    """Durable partial-exit records. The production adapter writes PostgreSQL.

    ``load`` returns the recorded fills and stop moves. ``record`` persists a
    step idempotently and, when the step closes the position, records the
    close through the paper close service with every fill attached.
    """

    def load(
        self, trade: OpenInternalPaperTrade
    ) -> tuple[tuple[ExitFill, ...], tuple[StopMove, ...]]:
        raise NotImplementedError

    def record(self, trade: OpenInternalPaperTrade, step: PartialExitStep) -> object:
        raise NotImplementedError


def partial_fill_id(command_id: UUID, suffix: str) -> str:
    return f"paper-partial-exit:{command_id}:{suffix}"


def _state_as_of(state: PositionExitState) -> datetime | None:
    moments = [fill.occurred_at for fill in state.fills]
    moments.extend(move.effective_at for move in state.stop_moves)
    return max(moments) if moments else None


def _reaches(direction: TradeDirection, price: Decimal, bar: ExitBar) -> bool:
    if direction is TradeDirection.LONG:
        return bar.high >= price
    return bar.low <= price


def decide_partial_exits(
    trade: OpenInternalPaperTrade,
    state: PositionExitState,
    bars: Sequence[ExitBar],
    *,
    source_name: str,
    evaluated_at: datetime,
    candle_venue: str = "",
) -> PartialExitStep:
    """Walk closed bars after the last recorded event and reduce the ladder.

    Per bar, conservatively: a bar that opens through the current stop exits
    the rest at the open; a stop touch exits the rest at the stop, even when a
    target was also touched (``stop_first``). Otherwise every pending target
    the bar reached fills in order. After TP1 is complete the breakeven move
    is recorded at that bar's end, so it protects from the next bar only.
    Once no target is pending the runner trails the closed bars up to this
    one; that move also applies from the next bar. Forming bars are ignored.
    """

    terms = trade.partial
    if terms is None:
        raise ValueError("decide_partial_exits requires a ladder trade")
    eligible_from = entry_eligibility_time(trade.entry_time, trade.timeframe)
    closed = sorted(
        (
            bar
            for bar in bars
            if bar.interval_start.tzinfo is not None
            and bar.interval_end.tzinfo is not None
            and bar.interval_end <= evaluated_at
            and bar.interval_start >= eligible_from
        ),
        key=lambda bar: bar.interval_start,
    )
    as_of = _state_as_of(state)
    direction = trade.direction
    fills: list[ExitFill] = []
    moves: list[StopMove] = []
    evidence: dict[str, dict[str, str]] = {}

    def note(key: str, bar: ExitBar, level: str, assumption: str) -> None:
        row = _evidence(trade, bar, source_name=source_name, level=level, assumption=assumption)
        origin = classify_exit_origin(bar.interval_end, evaluated_at)
        row["exit_origin"] = origin
        row["calibration_sample"] = (
            CALIBRATION_EXCLUDED if origin == EXIT_ORIGIN_BACKFILL else CALIBRATION_ELIGIBLE
        )
        if candle_venue:
            row["candle_venue"] = candle_venue
        evidence[key] = row

    def take(fill: ExitFill) -> None:
        nonlocal state
        outcome = apply_fill(state, fill)
        if outcome.status != "applied":
            raise ValueError(f"partial exit fill not applied: {outcome.reason or outcome.status}")
        state = outcome.state
        fills.append(fill)

    def move_stop(move: StopMove) -> None:
        nonlocal state
        outcome = apply_stop_move(state, move)
        if outcome.status != "applied":
            raise ValueError(f"stop move not applied: {outcome.reason or outcome.status}")
        state = outcome.state
        moves.append(move)

    for index, bar in enumerate(closed):
        if state.is_closed:
            break
        if as_of is not None and bar.interval_start < as_of:
            continue
        stop = state.current_stop
        runner_phase = not state.pending_target_legs()
        if direction is TradeDirection.LONG:
            gapped = bar.open <= stop
            stop_hit = bar.low <= stop
        else:
            gapped = bar.open >= stop
            stop_hit = bar.high >= stop
        if gapped or stop_hit:
            target_touched = any(
                _reaches(direction, leg.price, bar)
                for leg in state.ladder.target_legs
                if leg.price is not None and leg.order in state.pending_target_legs()
            )
            assumption = (
                GAP_THROUGH_OPEN
                if gapped
                else SAME_BAR_ASSUMPTION
                if target_touched
                else SAME_BAR_NONE
            )
            fill = ExitFill(
                fill_id=partial_fill_id(trade.command_id, "stop"),
                kind="runner_stop" if runner_phase else "stop",
                leg_order=None,
                quantity=state.remaining_quantity,
                price=bar.open if gapped else stop,
                occurred_at=bar.interval_end,
            )
            take(fill)
            note(fill.fill_id, bar, "stop", assumption)
            break
        for order in state.pending_target_legs():
            leg = state.ladder.leg(order)
            if leg is None or leg.price is None or not _reaches(direction, leg.price, bar):
                break
            fill = ExitFill(
                fill_id=partial_fill_id(trade.command_id, f"leg:{order}"),
                kind="target",
                leg_order=order,
                quantity=leg.quantity - state.leg_filled(order),
                price=leg.price,
                occurred_at=bar.interval_end,
            )
            take(fill)
            note(fill.fill_id, bar, f"target:{order}", SAME_BAR_NONE)
        breakeven = plan_breakeven_move(state)
        if breakeven is not None:
            move_stop(breakeven)
            note(breakeven.key, bar, "breakeven", SAME_BAR_NONE)
        if state.is_closed or terms.runner_rule is None or state.pending_target_legs():
            continue
        trailed = trail_runner_stop(
            rule=terms.runner_rule,
            direction=direction,
            current_stop=state.current_stop,
            bars=closed[: index + 1],
            evaluated_at=evaluated_at,
        )
        if trailed != state.current_stop:
            trail = StopMove(
                key=f"trail:{bar.interval_end.isoformat()}",
                reason="trail",
                previous_stop=state.current_stop,
                new_stop=trailed,
                effective_at=bar.interval_end,
                tightened=True,
            )
            move_stop(trail)
            note(trail.key, bar, "trail", SAME_BAR_NONE)
    return PartialExitStep(
        trade=trade,
        fills=tuple(fills),
        stop_moves=tuple(moves),
        state=state,
        evidence=evidence,
    )


_EXIT_PRICE_QUANT = Decimal("0.00000001")


def partial_close_request_for(
    trade: OpenInternalPaperTrade, step: PartialExitStep
) -> ClosePaperPlanRequest:
    """The final close for a fully reduced ladder, every fill attached.

    Plan cost allowances are split by quantity (the last fill takes the
    remainder), so per-fill costs sum to the totals exactly. The exit price
    is the quantity-weighted average, for display; PnL is summed per fill.
    """

    state = step.state
    terms = trade.partial
    if terms is None or not state.is_closed:
        raise ValueError("partial close requires a fully reduced ladder trade")
    ordered = state.fills
    quantities = [fill.quantity for fill in ordered]
    fees = allocate_by_quantity(terms.fees, quantities)
    funding = allocate_by_quantity(terms.funding, quantities)
    slippage = allocate_by_quantity(terms.slippage, quantities)
    exit_fills = tuple(
        ClosePaperExitFill(
            fill_id=fill.fill_id,
            kind=fill.kind,
            leg_order=fill.leg_order,
            quantity=fill.quantity,
            price=fill.price,
            occurred_at=fill.occurred_at,
            fees=fees[index],
            funding=funding[index],
            slippage=slippage[index],
        )
        for index, fill in enumerate(ordered)
    )
    size = sum(quantities, Decimal("0"))
    weighted = sum((fill.price * fill.quantity for fill in ordered), Decimal("0"))
    last = ordered[-1]
    evidence = dict(step.evidence.get(last.fill_id, {}))
    evidence["partial_exit_fills"] = str(len(ordered))
    evidence["exit_price_basis"] = "quantity_weighted_average"
    return ClosePaperPlanRequest(
        organization_id=trade.organization_id,
        user_id=trade.user_id,
        account_id=trade.account_id,
        revision_id=trade.revision_id,
        command_id=trade.command_id,
        exit_price=(weighted / size).quantize(_EXIT_PRICE_QUANT),
        fees=terms.fees,
        funding=terms.funding,
        slippage=terms.slippage,
        exit_reason=EXIT_REASON_TARGET if last.kind == "target" else EXIT_REASON_STOP,
        occurred_at=last.occurred_at,
        idempotency_key=exit_idempotency_key(trade.command_id),
        exit_channel=EXIT_CHANNEL,
        exit_actor=EXIT_ACTOR,
        exit_evidence=evidence or None,
        exit_fills=exit_fills,
    )


STATUS_SETTLING = "settling"
# Consecutive settling deferrals one trade may take before the read is refused.
MAX_SETTLING_DEFERRALS = 3


def run_paper_exit_monitor(
    *,
    enabled: bool,
    dry_run: bool,
    trades: Sequence[OpenInternalPaperTrade],
    candles: Callable[[OpenInternalPaperTrade], FetchedBars],
    closer: PaperExitCloser,
    evaluated_at: datetime,
    rejected: int = 0,
    shutdown: Callable[[], bool] | None = None,
    settling_streaks: dict[UUID, int] | None = None,
    close_failures: dict[UUID, CloseFailureBackoff] | None = None,
    partial: PartialExitRecorder | None = None,
    partial_hold: Callable[[UUID], str | None] | None = None,
) -> MonitorReport:
    """Examine open trades once. Flag off does not load candles or close.

    ``settling_streaks`` carries consecutive settling deferrals per trade across
    cycles. After ``MAX_SETTLING_DEFERRALS`` the read is refused instead.
    ``close_failures`` carries the per-trade backoff after a failed close. A
    trade inside its wait is not read or closed and keeps the pass degraded.

    A ladder trade (``trade.partial``) needs ``partial`` or it is refused. It is
    held, before any candle read, while ``partial_hold`` names a reason for its
    organization (kill switch, paused worker); its bars are replayed from the
    last durable record once released. Single-target trades never touch either.
    """

    if not enabled:
        return MonitorReport(status="disarmed")
    closed = 0
    replayed = 0
    would_close = 0
    skipped = rejected
    refused = 0
    errors = 0
    settling = 0
    backed_off = 0
    reduced = 0
    held = 0
    error = ""
    alerts: list[ExitMonitorAlert] = []
    streaks = settling_streaks if settling_streaks is not None else {}
    failures = close_failures if close_failures is not None else {}
    ordered = sorted(
        trades,
        key=lambda trade: (trade.symbol, trade.timeframe.value, trade.entry_time),
    )
    for trade in ordered:
        if shutdown is not None and shutdown():
            break
        backoff = failures.get(trade.journal_trade_id)
        if backoff is not None and evaluated_at < backoff.retry_at:
            backed_off += 1
            continue
        if trade.partial is not None:
            if partial is None:
                refused += 1
                logger.warning(
                    "paper_exit_monitor_ladder_unmanaged",
                    journal_trade_id=str(trade.journal_trade_id),
                )
                continue
            hold = partial_hold(trade.organization_id) if partial_hold is not None else None
            if hold is not None:
                held += 1
                logger.info(
                    "paper_exit_monitor_ladder_held",
                    journal_trade_id=str(trade.journal_trade_id),
                    reason=hold,
                )
                continue
        try:
            fetched = candles(trade)
        except Exception as exc:
            errors += 1
            error = error or "candle_failed"
            logger.warning(
                "paper_exit_monitor_candle_failed",
                journal_trade_id=str(trade.journal_trade_id),
                error=redact_text(str(exc))[:200],
            )
            continue
        block: str | None = None
        if fetched.settling:
            deferrals = streaks.get(trade.journal_trade_id, 0) + 1
            streaks[trade.journal_trade_id] = deferrals
            if deferrals <= MAX_SETTLING_DEFERRALS:
                settling += 1
                logger.info(
                    "paper_exit_monitor_candles_settling",
                    journal_trade_id=str(trade.journal_trade_id),
                    symbol=trade.symbol,
                    timeframe=trade.timeframe.value,
                    deferrals=deferrals,
                )
                continue
            block = "settling_exhausted"
        else:
            streaks.pop(trade.journal_trade_id, None)
        if block is None and not fetched.covers_entry:
            block = "leading_gap"
        if block is None:
            block = candle_series_block(
                fetched.bars,
                timeframe=trade.timeframe,
                evaluated_at=evaluated_at,
                entry_time=trade.entry_time,
            )
        if block is not None:
            refused += 1
            logger.warning(
                "paper_exit_monitor_candles_unusable",
                journal_trade_id=str(trade.journal_trade_id),
                symbol=trade.symbol,
                timeframe=trade.timeframe.value,
                reason=block,
                entry_time=trade.entry_time.isoformat(),
                series_start=_series_start(fetched.bars),
            )
            continue
        if trade.partial is not None and partial is not None:
            try:
                recorded, recorded_moves = partial.load(trade)
                ladder_state = rebuild_position_state(
                    trade.partial.open_state(), fills=recorded, stop_moves=recorded_moves
                )
                step = decide_partial_exits(
                    trade,
                    ladder_state,
                    fetched.bars,
                    source_name=fetched.source_name,
                    evaluated_at=evaluated_at,
                    candle_venue=fetched.candle_venue,
                )
            except Exception as exc:
                errors += 1
                error = error or "partial_state_failed"
                logger.error(
                    "paper_exit_monitor_partial_state_failed",
                    journal_trade_id=str(trade.journal_trade_id),
                    error=redact_text(str(exc))[:200],
                )
                continue
            if not step.fills and not step.stop_moves:
                continue
            if dry_run:
                would_close += 1
                logger.info(
                    "paper_exit_monitor_would_reduce",
                    journal_trade_id=str(trade.journal_trade_id),
                    fills=len(step.fills),
                    stop_moves=len(step.stop_moves),
                    remaining=format(step.state.remaining_quantity, "f"),
                )
                continue
            try:
                result = partial.record(trade, step)
            except Exception as exc:
                reason = _close_skip_reason(exc)
                if reason is not None:
                    failures.pop(trade.journal_trade_id, None)
                    skipped += 1
                    continue
                errors += 1
                error = error or "partial_record_failed"
                previous = failures.get(trade.journal_trade_id)
                failures[trade.journal_trade_id] = next_close_backoff(
                    previous, failed_at=evaluated_at, reason=_close_failure_reason(exc)
                )
                logger.warning(
                    "paper_exit_monitor_partial_record_failed",
                    journal_trade_id=str(trade.journal_trade_id),
                    error=redact_text(str(exc))[:200],
                )
                continue
            failures.pop(trade.journal_trade_id, None)
            if not step.state.is_closed:
                reduced += 1
            elif bool(getattr(result, "replayed", False)):
                replayed += 1
            else:
                closed += 1
            logger.info(
                "paper_exit_monitor_reduced",
                journal_trade_id=str(trade.journal_trade_id),
                fills=len(step.fills),
                stop_moves=len(step.stop_moves),
                remaining=format(step.state.remaining_quantity, "f"),
                closed=step.state.is_closed,
            )
            continue
        decision = decide_exit(
            trade,
            fetched.bars,
            source_name=fetched.source_name,
            evaluated_at=evaluated_at,
            candle_venue=fetched.candle_venue,
        )
        if decision is None:
            continue
        if dry_run:
            would_close += 1
            logger.info("paper_exit_monitor_would_close", **_decision_log(decision))
            continue
        try:
            result = closer.close(close_request_for(decision))
        except Exception as exc:
            reason = _close_skip_reason(exc)
            if reason is not None:
                failures.pop(trade.journal_trade_id, None)
                skipped += 1
                logger.info(
                    "paper_exit_monitor_close_skipped",
                    journal_trade_id=str(trade.journal_trade_id),
                    reason=reason,
                )
                continue
            errors += 1
            error = error or "close_failed"
            previous = failures.get(trade.journal_trade_id)
            backoff = next_close_backoff(
                previous,
                failed_at=evaluated_at,
                reason=_close_failure_reason(exc),
            )
            failures[trade.journal_trade_id] = backoff
            logger.warning(
                "paper_exit_monitor_close_failed",
                journal_trade_id=str(trade.journal_trade_id),
                error=redact_text(str(exc))[:200],
                reason=backoff.reason,
                failures=backoff.failures,
                retry_at=backoff.retry_at.isoformat(),
            )
            if backoff.quarantined and (previous is None or not previous.quarantined):
                logger.error(
                    "paper_exit_monitor_close_quarantined",
                    journal_trade_id=str(trade.journal_trade_id),
                    organization_id=str(trade.organization_id),
                    command_id=str(trade.command_id),
                    reason=backoff.reason,
                    retry_at=backoff.retry_at.isoformat(),
                )
                # Once per quarantine streak (L7); the streak ends when the
                # trade closes or leaves the open book.
                alerts.append(close_quarantined_alert(trade, backoff))
            continue
        failures.pop(trade.journal_trade_id, None)
        if bool(getattr(result, "replayed", False)):
            replayed += 1
        else:
            closed += 1
        logger.info(
            "paper_exit_monitor_closed",
            replayed=bool(getattr(result, "replayed", False)),
            **_decision_log(decision),
        )
    open_ids = {trade.journal_trade_id for trade in ordered}
    for stale in [key for key in streaks if key not in open_ids]:
        del streaks[stale]
    for stale in [key for key in failures if key not in open_ids]:
        del failures[stale]
    quarantined = sum(
        1
        for trade in ordered
        if (state := failures.get(trade.journal_trade_id)) is not None and state.quarantined
    )
    if errors or refused:
        status = "degraded"
        if not error and refused:
            error = "candles_unusable"
    elif backed_off:
        # Waiting out a close backoff is not a new failure: it is reported in
        # the heartbeat (M5) but does not re-page every threshold (L7).
        status = STATUS_BACKING_OFF
        error = "close_quarantined" if quarantined else "close_backoff"
    elif dry_run:
        status = "dry_run"
    elif closed or replayed or reduced:
        status = "closed"
    elif settling:
        status = STATUS_SETTLING
    elif held:
        status = "held"
    else:
        status = "idle"
    return MonitorReport(
        status=status,
        examined=len(ordered),
        closed=closed,
        replayed=replayed,
        would_close=would_close,
        skipped=skipped,
        refused=refused,
        errors=errors,
        error=error,
        settling=settling,
        organization_ids=tuple(dict.fromkeys(trade.organization_id for trade in ordered)),
        backed_off=backed_off,
        quarantined=quarantined,
        alerts=tuple(alerts),
        reduced=reduced,
        held=held,
    )


def close_quarantined_alert(
    trade: OpenInternalPaperTrade, backoff: CloseFailureBackoff
) -> ExitMonitorAlert:
    """One page per quarantine streak: the trade stays OPEN until a human acts."""

    return ExitMonitorAlert(
        code="close_quarantined",
        severity="warning",
        message=(
            "A paper trade's automatic close was refused and is quarantined. It stays "
            "OPEN and counts against open-position caps until a human resolves it."
        ),
        fields={
            "journal_trade_id": str(trade.journal_trade_id),
            "organization_id": str(trade.organization_id),
            "command_id": str(trade.command_id),
            "reason": backoff.reason[:120],
            "retry_at": backoff.retry_at.isoformat(),
            "dedup_key": f"close_quarantined:{trade.journal_trade_id}",
        },
    )


def _exit_levels(
    payload: dict[str, object],
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal] | None:
    risk = payload.get("risk_and_exits")
    if not isinstance(risk, dict):
        return None
    runner = risk.get("runner")
    if not isinstance(runner, dict) or runner.get("enabled") is not False:
        return None
    targets = risk.get("targets")
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], dict):
        return None
    target_row = targets[0]
    fraction = _decimal(target_row.get("quantity_fraction"))
    if fraction != Decimal("1"):
        return None
    stop_row = risk.get("stop")
    price_row = target_row.get("price")
    if not isinstance(stop_row, dict) or not isinstance(price_row, dict):
        return None
    stop = _positive(stop_row.get("value"))
    target = _positive(price_row.get("value"))
    fees = _non_negative(_amount_value(risk.get("fee_allowance")))
    funding = _non_negative(_amount_value(risk.get("funding_allowance")))
    slippage = _non_negative(_amount_value(risk.get("slippage_allowance")))
    if None in {stop, target, fees, funding, slippage}:
        return None
    assert stop is not None and target is not None
    assert fees is not None and funding is not None and slippage is not None
    return stop, target, fees, funding, slippage


def _amount_value(value: object) -> object:
    if isinstance(value, dict):
        return value.get("value")
    return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    return amount


def _positive(value: object) -> Decimal | None:
    amount = _decimal(value)
    if amount is None or amount <= 0:
        return None
    return amount


def _non_negative(value: object) -> Decimal | None:
    amount = _decimal(value)
    if amount is None or amount < 0:
        return None
    return amount


def _opens_through_stop(trade: OpenInternalPaperTrade, bar: ExitBar) -> bool:
    """True when the bar open is already through the stop.

    The fill is the open. Later extrema in the same bar are not used.
    """

    if trade.direction is TradeDirection.LONG:
        return bar.open <= trade.stop
    if trade.direction is TradeDirection.SHORT:
        return bar.open >= trade.stop
    return False


def _touch(trade: OpenInternalPaperTrade, bar: ExitBar) -> Literal["stop", "target", "both"] | None:
    if trade.direction is TradeDirection.LONG:
        stop_hit = bar.low <= trade.stop
        target_hit = bar.high >= trade.target
    else:
        stop_hit = bar.high >= trade.stop
        target_hit = bar.low <= trade.target
    if stop_hit and target_hit:
        return "both"
    if stop_hit:
        return "stop"
    if target_hit:
        return "target"
    return None


def _evidence(
    trade: OpenInternalPaperTrade,
    bar: ExitBar,
    *,
    source_name: str,
    level: str,
    assumption: str,
) -> dict[str, str]:
    return {
        "bar_interval_start": bar.interval_start.isoformat(),
        "bar_interval_end": bar.interval_end.isoformat(),
        "bar_open": format(bar.open, "f"),
        "bar_high": format(bar.high, "f"),
        "bar_low": format(bar.low, "f"),
        "bar_close": format(bar.close, "f"),
        "source_event_id": bar.source_event_id,
        "content_hash": bar.content_hash,
        "timeframe": trade.timeframe.value,
        "symbol": trade.symbol,
        "level": level,
        "same_bar_assumption": assumption,
        "market_source": source_name,
        "direction": trade.direction.value,
    }


def _decision_log(decision: ExitDecision) -> dict[str, object]:
    return {
        "journal_trade_id": str(decision.trade.journal_trade_id),
        "command_id": str(decision.trade.command_id),
        "exit_reason": decision.reason,
        "exit_price": format(decision.exit_price, "f"),
        "bar_interval_end": decision.occurred_at.isoformat(),
        "same_bar_assumption": decision.evidence["same_bar_assumption"],
        "exit_origin": decision.evidence.get("exit_origin", ""),
        "exit_channel": EXIT_CHANNEL,
        "exit_actor": EXIT_ACTOR,
        "candle_venue": decision.evidence.get("candle_venue", ""),
    }


def _series_start(bars: Sequence[ExitBar]) -> str:
    if not bars:
        return ""
    earliest = min(bars, key=lambda bar: bar.interval_start)
    return earliest.interval_start.isoformat()


def _close_skip_reason(exc: Exception) -> str | None:
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        reason = details.get("reason")
        if isinstance(reason, str) and reason in _SKIP_CLOSE_REASONS:
            return reason
    name = type(exc).__name__
    if name == "JournalProjectionConflictError":
        return "close_conflict"
    return None


def _close_failure_reason(exc: Exception) -> str:
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        reason = details.get("reason")
        if isinstance(reason, str) and reason:
            return reason[:80]
    return type(exc).__name__[:80]
