"""Account daily equity stop (AT-ADR-152).

The one loss rule that arms the organization kill switch. Stop equity is
``default_account_balance`` plus all-time realized PnL plus the unrealized
PnL of open legacy positions (:func:`read_stop_equity`); canonical open
paper trades carry no mark yet. The 00:00 UTC baseline
and every later check use that one function, so the user's risk-day
timezone never enters it. Equity below ``baseline * (1 - threshold)`` arms
the switch. Unreadable equity, a stale unrealized mark, or a stale
baseline past the grace window arms it too. A never-snapshotted row seeds
its baseline without tripping. A balance edit shifts today's baseline by
the edit (:func:`rebaseline_after_balance_edit`). A single losing close and
a losing streak do not arm it.

The switch arms at most once per UTC day, so a human deactivation sticks
for the rest of that day. A new day's baseline does not clear it. This
module never calls deactivate and does not place an order.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.risk_policy.errors import RiskPolicyRejected

if TYPE_CHECKING:
    from sqlalchemy.engine import Row

    from app.services.paper_exit_monitor import ExitMonitorAlert

logger = structlog.get_logger(__name__)

ACCOUNT_DAILY_DRAWDOWN = "ACCOUNT_DAILY_DRAWDOWN"
KILL_SWITCH_SOURCE = "account_equity_stop"

# Runtime-status rows: the sweep heartbeat, and the alert dedupe ledger.
EQUITY_STOP_SWEEP_COMPONENT = "account_equity_stop_sweep"
EQUITY_STOP_ALERT_COMPONENT = "account_equity_stop_alert"
SWEEP_STALE_CODE = "account_equity_stop_sweep_stale"
SWEEP_MISSING_CODE = "account_equity_stop_sweep_missing"
SWEEP_FAILED_CODE = "account_equity_stop_sweep_failed"
ORPHAN_ROWS_CODE = "account_equity_stop_orphan_breaker_rows"

EquityCause = Literal["equity_unreadable", "equity_stale"]
TripCause = Literal[
    "breach", "equity_unreadable", "equity_stale", "baseline_missing", "baseline_stale"
]


@dataclass(frozen=True)
class StopEquityReading:
    """Stop equity, or None with the reason it cannot be used."""

    equity: Decimal | None
    cause: EquityCause | None = None


@dataclass(frozen=True)
class EquityStopVerdict:
    trip: bool
    cause: TripCause | None
    day: date
    baseline: Decimal | None
    equity: Decimal | None
    drawdown_percent: Decimal | None
    # Baseline to store for ``day``. Set at the rollover snapshot, and when a
    # missing or stale baseline trips, so the stop guards the rest of the day.
    new_baseline: Decimal | None


def _valid_money(value: object) -> Decimal | None:
    if isinstance(value, Decimal) and value.is_finite() and value > 0:
        return value
    return None


def evaluate_equity_stop(
    *,
    threshold_percent: Decimal,
    grace: timedelta,
    baseline: object,
    baseline_day: date | None,
    equity: object,
    now: datetime,
    first_sight: bool = False,
    equity_cause: EquityCause = "equity_unreadable",
) -> EquityStopVerdict:
    """Pure verdict for one account. Does not write and does not arm.

    ``first_sight`` is an account that was never snapshotted. Its first
    reading is its baseline; there is no earlier day to be stale against.
    ``equity_cause`` names why ``equity`` is unusable when the reader knows.
    """

    if now.tzinfo is None:
        raise RiskPolicyRejected("timestamp_must_be_aware")
    if not isinstance(threshold_percent, Decimal) or not (
        Decimal("0") < threshold_percent < Decimal("100")
    ):
        raise RiskPolicyRejected("equity_stop_threshold_invalid")
    moment = now.astimezone(UTC)
    day = moment.date()
    if baseline_day is not None and day < baseline_day:
        # A profile close is evaluated at its bar time, which can fall on the
        # day before the stored baseline. It must not re-date or trip it.
        return EquityStopVerdict(False, None, day, None, None, None, None)
    current = _valid_money(equity)
    start = _valid_money(baseline) if baseline_day == day else None
    if current is None:
        return EquityStopVerdict(True, equity_cause, day, start, None, None, None)
    if start is None:
        in_grace = moment - datetime.combine(day, time(), UTC) <= grace
        if first_sight or in_grace:
            return EquityStopVerdict(False, None, day, current, current, Decimal("0"), current)
        stale = baseline_day is not None and baseline_day != day
        cause: TripCause = "baseline_stale" if stale else "baseline_missing"
        return EquityStopVerdict(True, cause, day, None, current, None, current)
    drawdown = (start - current) / start * Decimal("100")
    floor = start * (Decimal("1") - threshold_percent / Decimal("100"))
    breached = current < floor
    return EquityStopVerdict(
        breached, "breach" if breached else None, day, start, current, drawdown, None
    )


def enforce_account_equity_stop(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    account_id: UUID,
    equity: object,
    now: datetime,
    settings: Settings | None = None,
    equity_cause: EquityCause = "equity_unreadable",
) -> bool:
    """Evaluate the stored baseline, snapshot or arm. Returns True when it armed now.

    The breaker row must exist. A row whose ``day_start_on`` is NULL was
    never snapshotted; its first reading seeds the baseline without
    tripping, so a first deploy does not arm every organization. A breach
    already armed today does not arm again, so an owner deactivation is
    not undone by the next scan.
    """

    from app.core.config import get_settings
    from app.services.audit_service import AuditService
    from app.services.risk_policy.persistence import RiskPolicyService

    resolved = get_settings() if settings is None else settings
    row = RiskPolicyService(session, AuditService(session)).load_breaker(
        organization_id=organization_id, account_id=account_id
    )
    if row is None:
        raise RiskPolicyRejected("equity_stop_breaker_row_missing")
    verdict = evaluate_equity_stop(
        threshold_percent=resolved.account_equity_stop_threshold_percent,
        grace=timedelta(seconds=int(resolved.account_equity_stop_baseline_grace_seconds)),
        baseline=row.day_start_equity,
        baseline_day=row.day_start_on,
        equity=equity,
        now=now,
        first_sight=row.day_start_on is None,
        equity_cause=equity_cause,
    )
    if verdict.new_baseline is not None:
        row.day_start_equity = verdict.new_baseline
        row.day_start_on = verdict.day
    armed = verdict.trip and row.equity_stop_tripped_on != verdict.day
    if armed:
        row.equity_stop_tripped_on = verdict.day
    session.flush()
    if armed:
        _arm(
            session,
            organization_id=organization_id,
            user_id=user_id,
            account_id=account_id,
            verdict=verdict,
            threshold_percent=resolved.account_equity_stop_threshold_percent,
            settings=resolved,
        )
    return armed


def read_stop_equity(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    now: datetime,
    settings: Settings,
) -> StopEquityReading:
    """Stop equity: balance + all-time realized + unrealized of open positions.

    This is the one equity function for the stop. The 00:00 UTC baseline
    and every later check read it. It does not use the risk-settings
    timezone. Only legacy ``positions`` rows carry an unrealized mark;
    canonical open paper trades add none, so for them the stop is
    realized-only until a mark feed exists (AT-ADR-152). An open position whose row was last
    written more than ``account_equity_stop_mark_max_age_seconds`` before
    ``now`` is a stale mark, and the reading is unusable (``equity_stale``).
    """

    from app.db.models import Position
    from app.schemas.common import PositionStatus
    from app.services.audit_service import AuditService
    from app.services.risk.daily_risk_accounting import DailyRiskAccounting
    from app.services.risk.settings_service import RiskSettingsService

    risk_settings = RiskSettingsService(session, AuditService(session))
    balance = risk_settings.get(
        organization_id=organization_id, user_id=user_id
    ).default_account_balance
    realized = DailyRiskAccounting(session, risk_settings).cumulative_realized_pnl_for_utc_stop(
        organization_id=organization_id, user_id=user_id
    )
    marks = session.execute(
        select(Position.unrealized_pnl, Position.updated_at).where(
            Position.organization_id == organization_id,
            Position.user_id == user_id,
            Position.status == PositionStatus.OPEN,
        )
    ).all()
    max_age = timedelta(seconds=int(settings.account_equity_stop_mark_max_age_seconds))
    moment = _aware(now)
    unrealized = Decimal("0")
    for value, marked_at in marks:
        if marked_at is None or moment - _aware(marked_at) > max_age:
            return StopEquityReading(None, "equity_stale")
        if not isinstance(value, Decimal) or not value.is_finite():
            return StopEquityReading(None, "equity_unreadable")
        unrealized += value
    return StopEquityReading(balance + realized + unrealized)


def enforce_account_equity_stop_from_books(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    account_id: UUID,
    now: datetime,
    settings: Settings | None = None,
) -> bool:
    """Read stop equity, then enforce. A read failure fails closed."""

    from app.core.config import get_settings

    resolved = get_settings() if settings is None else settings
    reading = StopEquityReading(None, "equity_unreadable")
    try:
        with session.begin_nested():
            reading = read_stop_equity(
                session,
                organization_id=organization_id,
                user_id=user_id,
                now=now,
                settings=resolved,
            )
    except Exception as exc:
        # Unreadable equity fails closed below; the savepoint keeps the session.
        logger.warning(
            "account_equity_stop_equity_unreadable",
            organization_id=str(organization_id),
            error_type=type(exc).__name__,
        )
    return enforce_account_equity_stop(
        session,
        organization_id=organization_id,
        user_id=user_id,
        account_id=account_id,
        equity=reading.equity,
        now=now,
        settings=resolved,
        equity_cause=reading.cause or "equity_unreadable",
    )


def _account_pairs(
    session: Session, organization_id: UUID | None = None
) -> list[Row[tuple[UUID, UUID, UUID]]]:
    from app.db.models import ExecutionAccount
    from app.db.risk_policy import RiskPolicyBreakerState

    stmt = select(
        RiskPolicyBreakerState.organization_id,
        RiskPolicyBreakerState.account_id,
        ExecutionAccount.user_id,
    ).join(
        ExecutionAccount,
        (ExecutionAccount.id == RiskPolicyBreakerState.account_id)
        & (ExecutionAccount.organization_id == RiskPolicyBreakerState.organization_id),
    )
    if organization_id is not None:
        stmt = stmt.where(RiskPolicyBreakerState.organization_id == organization_id)
    return list(session.execute(stmt).all())


def orphan_breaker_rows(session: Session) -> list[Row[tuple[UUID, UUID]]]:
    """Breaker rows with no ExecutionAccount in their organization.

    The sweep cannot resolve a user for these, so they are never snapshotted
    or enforced. They guard no account that can still trade, so the entry
    gate ignores them; the worker pages on them instead (AT-ADR-152).
    """

    from app.db.models import ExecutionAccount
    from app.db.risk_policy import RiskPolicyBreakerState

    stmt = (
        select(RiskPolicyBreakerState.organization_id, RiskPolicyBreakerState.account_id)
        .outerjoin(
            ExecutionAccount,
            (ExecutionAccount.id == RiskPolicyBreakerState.account_id)
            & (ExecutionAccount.organization_id == RiskPolicyBreakerState.organization_id),
        )
        .where(ExecutionAccount.id.is_(None))
        .order_by(RiskPolicyBreakerState.organization_id, RiskPolicyBreakerState.account_id)
    )
    return list(session.execute(stmt).all())


def orphan_breaker_rows_alert(rows: list[Row[tuple[UUID, UUID]]]) -> ExitMonitorAlert | None:
    """A mandatory page naming the orphan breaker rows, or None when there are none."""

    from app.services.paper_exit_monitor import ExitMonitorAlert

    if not rows:
        return None
    return ExitMonitorAlert(
        code=ORPHAN_ROWS_CODE,
        severity="critical",
        message=(
            "Account equity stop breaker rows have no matching execution account. "
            "They are not swept and the entry gate ignores them; review and "
            "remove or re-link them."
        ),
        fields={
            "count": str(len(rows)),
            "rows": ",".join(f"{org}:{account}" for org, account in rows[:10]),
            "dedup_key": ORPHAN_ROWS_CODE,
        },
    )


def sweep_account_equity_stops(session: Session, *, now: datetime, settings: Settings) -> int:
    """Snapshot and enforce every account with a breaker row. Returns the arm count.

    The paper worker runs this each cycle, so the 00:00 UTC baseline is
    taken inside the grace window and an idle account is still guarded.
    """

    armed = 0
    for organization_id, account_id, user_id in _account_pairs(session):
        if enforce_account_equity_stop_from_books(
            session,
            organization_id=organization_id,
            user_id=user_id,
            account_id=account_id,
            now=now,
            settings=settings,
        ):
            armed += 1
    return armed


def entry_baseline_overdue(
    session: Session,
    *,
    organization_id: UUID,
    now: datetime,
    settings: Settings | None = None,
) -> bool:
    """True when an account's baseline is for an earlier UTC day past the grace window.

    The entry gate refuses new trades on this even when the sweep has not
    run, so a stalled exit monitor cannot leave entries unguarded. A row
    that was never snapshotted (NULL day) is first sight, not overdue. An
    orphan row (see :func:`orphan_breaker_rows`) is ignored here.
    """

    from app.core.config import get_settings
    from app.db.models import ExecutionAccount
    from app.db.risk_policy import RiskPolicyBreakerState

    resolved = get_settings() if settings is None else settings
    moment = _aware(now)
    day = moment.date()
    grace = timedelta(seconds=int(resolved.account_equity_stop_baseline_grace_seconds))
    if moment - datetime.combine(day, time(), UTC) <= grace:
        return False
    # An orphan row (no matching ExecutionAccount) is never swept, so its day
    # would age and refuse the whole organization; it is paged instead.
    days = session.scalars(
        select(RiskPolicyBreakerState.day_start_on)
        .join(
            ExecutionAccount,
            (ExecutionAccount.id == RiskPolicyBreakerState.account_id)
            & (ExecutionAccount.organization_id == RiskPolicyBreakerState.organization_id),
        )
        .where(
            RiskPolicyBreakerState.organization_id == organization_id,
            RiskPolicyBreakerState.day_start_on.is_not(None),
        )
    ).all()
    overdue = any(stored < day for stored in days if stored is not None)
    if overdue:
        logger.warning(
            "account_equity_stop_baseline_overdue",
            organization_id=str(organization_id),
            day=day.isoformat(),
        )
    return overdue


def rebaseline_after_balance_edit(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    before_balance: Decimal,
    after_balance: Decimal,
    now: datetime,
    actor_role: str | None = None,
) -> int:
    """Shift today's baseline by the ``default_account_balance`` edit.

    A balance change moves stop equity by exactly ``after - before``, so the
    baseline moves by the same amount: ``day_start_equity += delta``. A lower
    balance does not false-trip, a higher one does not mask a loss, and the
    day's losses so far stay counted, so an edit-and-revert forgives
    nothing. Only rows already snapshotted for today are shifted. A NULL
    (first sight) row seeds from post-edit equity on its next check, and an
    earlier-day row is snapshotted or fails closed at the next check as
    before. Every one of the user's breaker rows gets an audit row naming
    the outcome. This never deactivates the kill switch and does not touch
    ``equity_stop_tripped_on``. Returns the number of rows shifted.
    """

    from app.db.models import ExecutionAccount
    from app.db.risk_policy import RiskPolicyBreakerState
    from app.schemas.audit import AuditRecordCreate
    from app.schemas.common import ActorType, AuditEventType, AuditResult, AuditSeverity
    from app.services.audit_service import AuditService

    delta = after_balance - before_balance
    day = _aware(now).date()
    rows = session.scalars(
        select(RiskPolicyBreakerState)
        .join(
            ExecutionAccount,
            (ExecutionAccount.id == RiskPolicyBreakerState.account_id)
            & (ExecutionAccount.organization_id == RiskPolicyBreakerState.organization_id),
        )
        .where(
            RiskPolicyBreakerState.organization_id == organization_id,
            ExecutionAccount.user_id == user_id,
        )
        .order_by(RiskPolicyBreakerState.account_id)
        .with_for_update(of=RiskPolicyBreakerState)
    ).all()
    audit = AuditService(session)
    request_id = uuid.uuid4().hex
    shifted = 0
    for row in rows:
        old = row.day_start_equity
        if row.day_start_on is None:
            outcome = "first_sight_unchanged"
        elif row.day_start_on != day or old is None:
            outcome = "not_today_unchanged"
        else:
            # A non-positive result is stored as is; the next check fails closed.
            row.day_start_equity = old + delta
            outcome = "shifted"
            shifted += 1
        metadata = {
            "action": "equity_stop_baseline_shift",
            "outcome": outcome,
            "actor_user_id": str(user_id),
            "actor_role": actor_role or "",
            "account_id": str(row.account_id),
            "day": day.isoformat(),
            "baseline_day": "" if row.day_start_on is None else row.day_start_on.isoformat(),
            "old_balance": _text(before_balance),
            "new_balance": _text(after_balance),
            "delta": _text(delta),
            "old_baseline": _text(old),
            "new_baseline": _text(row.day_start_equity),
        }
        audit.record(
            AuditRecordCreate(
                request_id=request_id,
                trace_id=request_id,
                event_type=AuditEventType.RISK_SETTINGS_UPDATED,
                resource_type="account_equity_stop_baseline",
                actor_type=ActorType.USER,
                result=AuditResult.SUCCESS,
                severity=AuditSeverity.MEDIUM,
                user_id=user_id,
                organization_id=organization_id,
                resource_id=str(row.account_id),
                metadata=metadata,
            )
        )
        logger.info("account_equity_stop_baseline_shift", **metadata)
    session.flush()
    return shifted


def equity_stop_sweep_alert(
    row: object | None,
    *,
    now: datetime,
    stale_after_seconds: int,
) -> ExitMonitorAlert | None:
    """Alert when the sweep heartbeat is missing or older than the bound."""

    from app.services.paper_exit_monitor import ExitMonitorAlert

    heartbeat = getattr(row, "heartbeat_at", None) if row is not None else None
    if isinstance(heartbeat, datetime):
        age = (_aware(now) - _aware(heartbeat)).total_seconds()
        if age <= stale_after_seconds:
            return None
        code, message = (
            SWEEP_STALE_CODE,
            "Account equity stop sweep is stale. The daily stop has not been "
            "checked inside its freshness window.",
        )
    else:
        age = None
        code, message = (
            SWEEP_MISSING_CODE,
            "Account equity stop sweep heartbeat is missing. The daily stop "
            "has not reported a successful sweep.",
        )
    return ExitMonitorAlert(
        code=code,
        severity="critical",
        message=message,
        fields={
            "component": EQUITY_STOP_SWEEP_COMPONENT,
            "heartbeat_age_seconds": "" if age is None else f"{age:.3f}",
            "threshold_seconds": str(stale_after_seconds),
            "refusal": "heartbeat",
            "dedup_key": code,
        },
    )


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _text(value: Decimal | None, places: str = "0.01") -> str:
    return "unavailable" if value is None else format(value.quantize(Decimal(places)), "f")


def _arm(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    account_id: UUID,
    verdict: EquityStopVerdict,
    threshold_percent: Decimal,
    settings: Settings,
) -> None:
    """Audit ACCOUNT_DAILY_DRAWDOWN, then arm the switch as the system."""

    from app.schemas.audit import AuditRecordCreate
    from app.schemas.common import (
        ActorType,
        AuditEventType,
        AuditResult,
        AuditSeverity,
    )
    from app.schemas.risk import KillSwitchMutationRequest
    from app.services.audit_service import AuditService
    from app.services.risk.kill_switch import KillSwitchService

    metadata = {
        "reason": ACCOUNT_DAILY_DRAWDOWN,
        "cause": verdict.cause or "",
        "day": verdict.day.isoformat(),
        "baseline": _text(verdict.baseline),
        "equity": _text(verdict.equity),
        "drawdown_percent": _text(verdict.drawdown_percent, "0.0001"),
        "threshold_percent": _text(threshold_percent, "0.0001"),
        "account_id": str(account_id),
    }
    request_id = uuid.uuid4().hex
    audit = AuditService(session)
    audit.record(
        AuditRecordCreate(
            request_id=request_id,
            trace_id=request_id,
            event_type=AuditEventType.KILL_SWITCH_TRIGGERED,
            resource_type="account_equity_stop",
            actor_type=ActorType.SYSTEM,
            result=AuditResult.BLOCKED,
            severity=AuditSeverity.CRITICAL,
            user_id=user_id,
            organization_id=organization_id,
            resource_id=str(account_id),
            metadata=metadata,
        )
    )
    reason = (
        f"{ACCOUNT_DAILY_DRAWDOWN} ({verdict.cause}): baseline={metadata['baseline']} "
        f"equity={metadata['equity']} drawdown_pct={metadata['drawdown_percent']} "
        f"threshold_pct={metadata['threshold_percent']} day={metadata['day']}. "
        "New entries stay blocked until a human deactivates the kill switch."
    )
    KillSwitchService(session, audit, settings).activate(
        organization_id=organization_id,
        actor_user_id=user_id,
        payload=KillSwitchMutationRequest(confirm=True, reason=reason),
        actor_type=ActorType.SYSTEM,
        source=KILL_SWITCH_SOURCE,
    )
    logger.warning("account_equity_stop_tripped", **metadata)
