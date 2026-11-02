"""Worker recovery for aged, unresolved manual BloFin demo entries.

``has_demo_entry_history`` fails closed while any ALLOW BloFin demo command lacks
an immutable lifecycle resolution. Governed reconciliation only revisits
governed-policy plans, so an owner's manual demo test whose exit was never
resolved blocks every later governed demo entry indefinitely.

This pass resolves such an entry only through the existing audited
``ManualDemoService.resolve`` path, which re-reads the venue and refuses unless
the exit is verified and the account is flat and idle. It adds two stricter
preconditions: the governed demo worker arm and credential gate are closed to
anything but ``paper_exchange_demo``, and the latest persisted BloFin demo
snapshot is fresh, healthy, not stale and shows no position. It never deletes
rows, never sends venue orders, and records an explicit audit event per outcome.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

import structlog
from sqlalchemy import String, select
from sqlalchemy.orm import Session

from app.core.config import ExchangeMode, Settings
from app.core.errors import AppError
from app.core.execution_credentials import (
    blofin_execution_authorized,
    governed_demo_worker_access_requested,
)
from app.db.models import (
    AuditLog,
    BloFinDemoSyncSnapshot,
    ExecutionCommand,
    ManualDemoLifecycleResolution,
    Membership,
    TradePlanRevision,
)
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import AuditEventType, MembershipRole
from app.schemas.execution_protocol import ExecutionCommandOutcome
from app.security.tenant import TenantContext
from app.services.audit_service import AuditService

if TYPE_CHECKING:
    from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider

logger = structlog.get_logger(__name__)

STALE_ENTRY_AGE = timedelta(hours=1)
SNAPSHOT_MAX_AGE = timedelta(minutes=10)
MAX_PER_PASS = 5
CANDIDATE_SCAN = 50
RETRY_BASE = timedelta(minutes=15)
RETRY_CAP = timedelta(hours=6)
MAX_ATTEMPTS = 8
OPERATOR_HOLD = "stale_entry_operator_hold"
OPERATION = "stale_manual_demo_entry_reconciliation"


def stale_entry_refusal(settings: Settings) -> str | None:
    """Refuse outside the armed, credential-gated BloFin demo posture."""
    if settings.exchange_mode is not ExchangeMode.PAPER_EXCHANGE_DEMO:
        return "stale_entry_not_demo_mode"
    if settings.enable_real_trading:
        return "stale_entry_live_refused"
    if not governed_demo_worker_access_requested(settings):
        return "stale_entry_demo_worker_disarmed"
    if not blofin_execution_authorized(settings):
        return "stale_entry_credential_gate_closed"
    return None


def snapshot_flat_refusal(
    session: Session, *, organization_id: UUID, user_id: UUID, now: datetime
) -> str | None:
    """Return None only when the latest demo snapshot proves a fresh flat account."""
    snapshot = session.scalar(
        select(BloFinDemoSyncSnapshot)
        .where(
            BloFinDemoSyncSnapshot.organization_id == organization_id,
            BloFinDemoSyncSnapshot.user_id == user_id,
            BloFinDemoSyncSnapshot.provider == "blofin_demo",
        )
        .order_by(BloFinDemoSyncSnapshot.synced_at.desc())
        .limit(1)
    )
    if snapshot is None:
        return "stale_entry_snapshot_missing"
    synced_at = snapshot.synced_at
    if synced_at.tzinfo is None:
        synced_at = synced_at.replace(tzinfo=now.tzinfo)
    if (
        snapshot.is_stale
        or snapshot.health_status != "ok"
        or snapshot.exchange_mode != ExchangeMode.PAPER_EXCHANGE_DEMO.value
        or now - synced_at > SNAPSHOT_MAX_AGE
        or synced_at > now + timedelta(minutes=1)
    ):
        return "stale_entry_snapshot_not_fresh"
    items = (snapshot.positions_snapshot or {}).get("items")
    if (
        snapshot.position_count != 0
        or not isinstance(items, list)
        or items
        or (snapshot.positions_snapshot or {}).get("truncated")
    ):
        return "stale_entry_snapshot_position_open"
    return None


def stale_manual_demo_command_ids(
    session: Session, settings: Settings, *, now: datetime
) -> list[UUID]:
    return list(
        session.scalars(
            select(ExecutionCommand.id)
            .join(TradePlanRevision, TradePlanRevision.id == ExecutionCommand.revision_id)
            .outerjoin(
                ManualDemoLifecycleResolution,
                ManualDemoLifecycleResolution.command_id == ExecutionCommand.id,
            )
            .where(
                ExecutionCommand.organization_id
                == UUID(settings.governed_blofin_demo_organization_id),
                ExecutionCommand.user_id == UUID(settings.governed_blofin_demo_user_id),
                ExecutionCommand.account_id == UUID(settings.governed_blofin_demo_account_id),
                ExecutionCommand.outcome == ExecutionCommandOutcome.ALLOW,
                TradePlanRevision.execution_venue == "BLOFIN_DEMO",
                TradePlanRevision.plan_authority == "manual_demo_test",
                ManualDemoLifecycleResolution.id.is_(None),
                ExecutionCommand.created_at < now - STALE_ENTRY_AGE,
                # Held commands are excluded in SQL so they cannot starve newer ones.
                ~select(AuditLog.id)
                .where(
                    AuditLog.organization_id == ExecutionCommand.organization_id,
                    AuditLog.resource_type == "manual_demo_test",
                    AuditLog.resource_id == ExecutionCommand.id.cast(String),
                    AuditLog.redacted_metadata["operation"].as_string() == OPERATION,
                    AuditLog.redacted_metadata["outcome"].as_string() == OPERATOR_HOLD,
                )
                .exists(),
            )
            .order_by(ExecutionCommand.created_at)
            .limit(CANDIDATE_SCAN)
        )
    )


def retry_delay(attempts: int) -> timedelta:
    """Exponential backoff after ``attempts`` prior tries: 15m, 30m, 1h ... capped at 6h."""
    if attempts <= 0:
        return timedelta(0)
    delay: timedelta = RETRY_BASE * (2 ** (attempts - 1))
    return min(delay, RETRY_CAP)


def attempt_history(
    session: Session, command_id: UUID, *, organization_id: UUID
) -> tuple[int, datetime | None, bool]:
    """Prior attempts, last attempt time (injected clock), and whether held."""
    rows = list(
        session.scalars(
            select(AuditLog).where(
                AuditLog.organization_id == organization_id,
                AuditLog.resource_type == "manual_demo_test",
                AuditLog.resource_id == str(command_id),
                AuditLog.redacted_metadata["operation"].as_string() == OPERATION,
            )
        )
    )
    attempts, last, held = 0, None, False
    for row in rows:
        meta = row.redacted_metadata
        if meta.get("outcome") == OPERATOR_HOLD:
            held = True
            continue
        attempts += 1
        at = meta.get("attempted_at")
        if isinstance(at, str):
            moment = datetime.fromisoformat(at)
            last = moment if last is None or moment > last else last
    return attempts, last, held


def due_command_ids(
    session: Session, settings: Settings, *, now: datetime
) -> list[tuple[UUID, int]]:
    due: list[tuple[UUID, int]] = []
    for command_id in stale_manual_demo_command_ids(session, settings, now=now):
        attempts, last, held = attempt_history(
            session, command_id, organization_id=UUID(settings.governed_blofin_demo_organization_id)
        )
        if held:
            continue
        if last is not None and now - last < retry_delay(attempts):
            continue
        due.append((command_id, attempts))
        if len(due) >= MAX_PER_PASS:
            break
    return due


def venue_precheck_refusal(provider: GovernedBloFinDemoProvider) -> str | None:
    """Read-only venue check: no position, no pending orders, no pending TP/SL."""
    from app.providers.exchange.manual_demo_lifecycle import (
        observe_account_idle,
        observe_positions,
    )

    try:
        flat = observe_positions(provider)
        idle = observe_account_idle(provider)
    except Exception:
        return "stale_entry_reconciliation_unavailable"
    if not (flat and idle):
        return "stale_entry_venue_mismatch"
    return None


def _audit(
    session: Session,
    *,
    command_id: UUID,
    organization_id: UUID,
    user_id: UUID,
    now: datetime,
    outcome: str,
) -> None:
    AuditService(session, strict_mode=True).record(
        AuditRecordCreate(
            request_id=str(uuid5(command_id, f"{OPERATION}:{outcome}:{now.isoformat()}")),
            trace_id=str(command_id),
            event_type=AuditEventType.DEMO_LIFECYCLE_RECONCILED,
            resource_type="manual_demo_test",
            resource_id=str(command_id),
            organization_id=organization_id,
            user_id=user_id,
            timestamp=now,
            metadata={
                "operation": OPERATION,
                "outcome": outcome,
                "attempted_at": now.isoformat(),
                "paper_only": True,
            },
        )
    )


def reconcile_stale_manual_demo_entries(
    session: Session,
    settings: Settings,
    *,
    clock: Callable[[], datetime],
    provider: GovernedBloFinDemoProvider | None = None,
) -> tuple[str, ...]:
    """Resolve aged manual demo entries the venue proves closed. Never resubmits."""
    if stale_entry_refusal(settings) is not None:
        return ()
    now = clock()
    organization_id = UUID(settings.governed_blofin_demo_organization_id)
    user_id = UUID(settings.governed_blofin_demo_user_id)
    due = due_command_ids(session, settings, now=now)
    session.commit()
    if not due:
        return ()
    membership = session.scalar(
        select(Membership).where(
            Membership.organization_id == organization_id, Membership.user_id == user_id
        )
    )
    if membership is None or membership.role is not MembershipRole.OWNER:
        return ("stale_entry_owner_required",)
    tenant = TenantContext(
        organization_id=organization_id,
        user_id=user_id,
        email="",
        membership_role=MembershipRole.OWNER,
    )
    from app.services.manual_demo_service import ManualDemoService as _Service

    outcomes: list[str] = []
    for command_id, attempts in due:
        refusal = snapshot_flat_refusal(
            session, organization_id=organization_id, user_id=user_id, now=now
        )
        if refusal is None:
            service = _Service(
                session, settings, provider=provider, clock=clock, worker_recovery=True
            )
            refusal = venue_precheck_refusal(service._provider())
        if refusal is None:
            try:
                status = service.resolve(tenant, command_id)
                outcome = (
                    "stale_entry_resolved"
                    if status.recovery_status == "resolved"
                    else "stale_entry_unresolved"
                )
            except AppError as exc:
                session.rollback()
                details = getattr(exc, "details", None) or {}
                if details.get("reason") == "stale_entry_venue_mismatch":
                    outcome = "stale_entry_venue_mismatch"
                else:
                    outcome = "stale_entry_evidence_incomplete"
                logger.info(
                    "stale_manual_demo_entry_refused",
                    command_id=str(command_id),
                    error=type(exc).__name__,
                    detail=str(exc)[:200],
                )
            except Exception:
                session.rollback()
                outcome = "stale_entry_reconciliation_unavailable"
        else:
            outcome = refusal
        _audit(
            session,
            command_id=command_id,
            organization_id=organization_id,
            user_id=user_id,
            now=now,
            outcome=outcome,
        )
        if outcome != "stale_entry_resolved" and attempts + 1 >= MAX_ATTEMPTS:
            _audit(
                session,
                command_id=command_id,
                organization_id=organization_id,
                user_id=user_id,
                now=now,
                outcome=OPERATOR_HOLD,
            )
            outcome = OPERATOR_HOLD
        session.commit()
        logger.info(
            "stale_manual_demo_entry_reconciliation",
            command_id=str(command_id),
            reason_code=outcome,
            paper_only=True,
        )
        outcomes.append(outcome)
    return tuple(outcomes)
