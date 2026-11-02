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
RETRY_AFTER = timedelta(minutes=15)
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
                # Bound venue reads and audit volume: one attempt per command per window.
                ~select(AuditLog.id)
                .where(
                    AuditLog.organization_id == ExecutionCommand.organization_id,
                    AuditLog.resource_type == "manual_demo_test",
                    AuditLog.resource_id == ExecutionCommand.id.cast(String),
                    AuditLog.redacted_metadata["operation"].as_string() == OPERATION,
                    AuditLog.created_at >= now - RETRY_AFTER,
                )
                .exists(),
            )
            .order_by(ExecutionCommand.created_at)
            .limit(MAX_PER_PASS)
        )
    )


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
            metadata={"operation": OPERATION, "outcome": outcome, "paper_only": True},
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
    from app.services.manual_demo_service import ManualDemoService

    if stale_entry_refusal(settings) is not None:
        return ()
    now = clock()
    organization_id = UUID(settings.governed_blofin_demo_organization_id)
    user_id = UUID(settings.governed_blofin_demo_user_id)
    ids = stale_manual_demo_command_ids(session, settings, now=now)
    session.commit()
    if not ids:
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
    outcomes: list[str] = []
    for command_id in ids:
        refusal = snapshot_flat_refusal(
            session, organization_id=organization_id, user_id=user_id, now=now
        )
        if refusal is None:
            try:
                status = ManualDemoService(
                    session, settings, provider=provider, clock=clock, worker_recovery=True
                ).resolve(tenant, command_id)
                outcome = (
                    "stale_entry_resolved"
                    if status.recovery_status == "resolved"
                    else "stale_entry_unresolved"
                )
            except AppError as exc:
                session.rollback()
                logger.info(
                    "stale_manual_demo_entry_refused",
                    command_id=str(command_id),
                    error=type(exc).__name__,
                    detail=str(exc)[:200],
                )
                outcome = "stale_entry_evidence_incomplete"
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
        session.commit()
        logger.info(
            "stale_manual_demo_entry_reconciliation",
            command_id=str(command_id),
            reason_code=outcome,
            paper_only=True,
        )
        outcomes.append(outcome)
    return tuple(outcomes)
