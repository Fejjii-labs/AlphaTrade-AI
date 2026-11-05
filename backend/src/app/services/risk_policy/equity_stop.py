"""Account daily equity stop (AT-ADR-152).

The one loss rule that arms the organization kill switch. Total account
equity, unrealized PnL included, is snapshotted at the 00:00 UTC rollover.
Equity below ``baseline * (1 - threshold)`` arms the switch. Unreadable
equity, or a missing or stale baseline past the grace window, arms it too.
A single losing close and a losing streak do not.

The switch arms at most once per UTC day, so a human deactivation sticks
for the rest of that day. A new day's baseline does not clear it. This
module never calls deactivate and does not place an order.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.risk_policy.errors import RiskPolicyRejected

logger = structlog.get_logger(__name__)

ACCOUNT_DAILY_DRAWDOWN = "ACCOUNT_DAILY_DRAWDOWN"
KILL_SWITCH_SOURCE = "account_equity_stop"

TripCause = Literal["breach", "equity_unreadable", "baseline_missing", "baseline_stale"]


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
) -> EquityStopVerdict:
    """Pure verdict for one account. Does not write and does not arm.

    ``first_sight`` is an account with no breaker row yet. Its first
    reading is its baseline; there is no earlier day to be stale against.
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
        return EquityStopVerdict(True, "equity_unreadable", day, start, None, None, None)
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
    first_sight: bool = False,
) -> bool:
    """Evaluate the stored baseline, snapshot or arm. Returns True when it armed now.

    The breaker row must exist. A breach already armed today does not arm
    again, so an owner deactivation is not undone by the next scan.
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
        first_sight=first_sight,
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


def sweep_account_equity_stops(session: Session, *, now: datetime, settings: Settings) -> int:
    """Snapshot and enforce every account with a breaker row. Returns the arm count.

    The paper worker runs this each cycle, so the 00:00 UTC baseline is
    taken inside the grace window and an idle account is still guarded.
    """

    from app.db.models import ExecutionAccount
    from app.db.risk_policy import RiskPolicyBreakerState
    from app.services.audit_service import AuditService
    from app.services.risk.daily_risk_accounting import DailyRiskAccounting
    from app.services.risk.settings_service import RiskSettingsService

    pairs = session.execute(
        select(RiskPolicyBreakerState.organization_id, RiskPolicyBreakerState.account_id)
    ).all()
    armed = 0
    for organization_id, account_id in pairs:
        user_id = session.scalar(
            select(ExecutionAccount.user_id).where(
                ExecutionAccount.id == account_id,
                ExecutionAccount.organization_id == organization_id,
            )
        )
        if user_id is None:
            continue
        equity: object = None
        try:
            with session.begin_nested():
                equity = (
                    DailyRiskAccounting(
                        session,
                        RiskSettingsService(session, AuditService(session)),
                        clock=lambda: now,
                    )
                    .sync_from_portfolio(organization_id=organization_id, user_id=user_id)
                    .account_equity
                )
        except Exception as exc:
            # Unreadable equity fails closed below; the savepoint keeps the session.
            logger.warning(
                "account_equity_stop_equity_unreadable",
                organization_id=str(organization_id),
                error_type=type(exc).__name__,
            )
        if enforce_account_equity_stop(
            session,
            organization_id=organization_id,
            user_id=user_id,
            account_id=account_id,
            equity=equity,
            now=now,
            settings=settings,
        ):
            armed += 1
    return armed


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
