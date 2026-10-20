"""Authoritative kill-switch enforcement for execution adapters (AT-014).

Organization state is persisted in PostgreSQL. An optional process-level
``Settings.global_kill_switch_active`` provides an emergency global gate for
ops (no platform-admin role exists in the current multi-tenant model).

Fail closed: if organization state cannot be read from the database, execution
is refused.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, TradingPolicyError, ValidationAppError
from app.db.models import KillSwitchState
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType, AuditResult, AuditSeverity
from app.schemas.risk import KillSwitchMutationRequest, KillSwitchScope, KillSwitchStatus
from app.services.audit_service import AuditService

if TYPE_CHECKING:
    from app.services.safety_epoch import SafetyEpochService


class ExecutionKillSwitch(Protocol):
    """Contract future sandbox/real adapters must honor before side effects."""

    def assert_execution_allowed(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> None:
        """Raise TradingPolicyError when execution must be refused."""
        ...

    def is_execution_blocked(self, *, organization_id: uuid.UUID) -> bool:
        """Return True when execution must be refused (fail closed on errors)."""
        ...


@dataclass(frozen=True)
class KillSwitchEvaluation:
    """Result of an authoritative kill-switch check."""

    blocked: bool
    reason_code: str
    organization_active: bool
    global_active: bool
    status: KillSwitchStatus | None = None


class KillSwitchService:
    """PostgreSQL-backed organization kill switch with fail-closed reads."""

    def __init__(
        self,
        session: Session,
        audit_service: AuditService,
        settings: Settings,
    ) -> None:
        self._session = session
        self._audit = audit_service
        self._settings = settings

    def get_status(self, *, organization_id: uuid.UUID) -> KillSwitchStatus:
        row = self._get_or_create_row(organization_id=organization_id)
        return self._to_status(row)

    def activate(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        payload: KillSwitchMutationRequest,
        actor_type: ActorType = ActorType.USER,
    ) -> KillSwitchStatus:
        self._require_confirm(payload)
        row = self._get_or_create_row(organization_id=organization_id)
        self._check_version(row, payload.expected_version)
        if row.active:
            return self._to_status(row)

        now = datetime.now(UTC)
        reason = payload.reason.strip()
        current_version = int(row.version)
        # A system arm, such as the exit monitor, must not be stored as the
        # trade owner flipping the switch by hand.
        stored_user_id = None if actor_type is ActorType.SYSTEM else actor_user_id
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(KillSwitchState)
                .where(
                    KillSwitchState.organization_id == organization_id,
                    KillSwitchState.version == current_version,
                    KillSwitchState.active.is_(False),
                )
                .values(
                    active=True,
                    reason=reason,
                    activated_by=stored_user_id,
                    activated_at=now,
                    deactivated_by=None,
                    deactivated_at=None,
                    version=current_version + 1,
                )
            ),
        )
        if result.rowcount != 1:
            self._session.expire_all()
            refreshed = self._get_or_create_row(organization_id=organization_id)
            if refreshed.active:
                return self._to_status(refreshed)
            raise ConflictError(
                "Kill switch was modified concurrently; refresh and retry.",
                details={
                    "reason": "version_conflict",
                    "expected_version": str(current_version),
                    "current_version": str(refreshed.version),
                },
            )
        self._session.expire_all()
        row = self._get_or_create_row(organization_id=organization_id)
        self._safety_epochs().advance_for_kill_activation(
            organization_id=organization_id,
            at=now,
        )
        self._audit_mutation(
            organization_id=organization_id,
            actor_user_id=stored_user_id,
            event_type=AuditEventType.KILL_SWITCH_ACTIVATED,
            row=row,
            actor_type=actor_type,
        )
        return self._to_status(row)

    def deactivate(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        payload: KillSwitchMutationRequest,
    ) -> KillSwitchStatus:
        self._require_confirm(payload)
        row = self._get_or_create_row(organization_id=organization_id)
        self._check_version(row, payload.expected_version)
        if not row.active:
            return self._to_status(row)

        now = datetime.now(UTC)
        reason = payload.reason.strip()
        current_version = int(row.version)
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(KillSwitchState)
                .where(
                    KillSwitchState.organization_id == organization_id,
                    KillSwitchState.version == current_version,
                    KillSwitchState.active.is_(True),
                )
                .values(
                    active=False,
                    reason=reason,
                    deactivated_by=actor_user_id,
                    deactivated_at=now,
                    version=current_version + 1,
                )
            ),
        )
        if result.rowcount != 1:
            self._session.expire_all()
            refreshed = self._get_or_create_row(organization_id=organization_id)
            if not refreshed.active:
                return self._to_status(refreshed)
            raise ConflictError(
                "Kill switch was modified concurrently; refresh and retry.",
                details={
                    "reason": "version_conflict",
                    "expected_version": str(current_version),
                    "current_version": str(refreshed.version),
                },
            )
        self._session.expire_all()
        row = self._get_or_create_row(organization_id=organization_id)
        self._safety_epochs().clear_blocking_for_kill_deactivation(
            organization_id=organization_id,
            at=now,
        )
        self._audit_mutation(
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            event_type=AuditEventType.KILL_SWITCH_DEACTIVATED,
            row=row,
        )
        return self._to_status(row)

    def evaluate(self, *, organization_id: uuid.UUID) -> KillSwitchEvaluation:
        """Read authoritative state; fail closed on storage errors."""
        global_active = bool(self._settings.global_kill_switch_active)
        try:
            status = self.get_status(organization_id=organization_id)
        except Exception:
            return KillSwitchEvaluation(
                blocked=True,
                reason_code="kill_switch_unavailable",
                organization_active=False,
                global_active=global_active,
                status=None,
            )

        org_active = bool(status.active)
        if global_active:
            return KillSwitchEvaluation(
                blocked=True,
                reason_code="global_kill_switch_active",
                organization_active=org_active,
                global_active=True,
                status=status,
            )
        if org_active:
            return KillSwitchEvaluation(
                blocked=True,
                reason_code="kill_switch_active",
                organization_active=True,
                global_active=False,
                status=status,
            )
        return KillSwitchEvaluation(
            blocked=False,
            reason_code="ok",
            organization_active=False,
            global_active=False,
            status=status,
        )

    def is_execution_blocked(self, *, organization_id: uuid.UUID) -> bool:
        return self.evaluate(organization_id=organization_id).blocked

    def assert_execution_allowed(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> None:
        evaluation = self.evaluate(organization_id=organization_id)
        if not evaluation.blocked:
            return

        if evaluation.reason_code == "kill_switch_unavailable":
            self._audit_trigger(
                organization_id=organization_id,
                user_id=user_id,
                reason=evaluation.reason_code,
            )
            raise TradingPolicyError(
                "Kill switch state is unavailable; execution refused.",
                details={"reason": evaluation.reason_code},
            )

        self._audit_trigger(
            organization_id=organization_id,
            user_id=user_id,
            reason=evaluation.reason_code,
        )
        raise TradingPolicyError(
            "Kill switch is active; new execution is blocked.",
            details={"reason": evaluation.reason_code},
        )

    def _get_or_create_row(self, *, organization_id: uuid.UUID) -> KillSwitchState:
        row = self._session.scalar(
            select(KillSwitchState).where(KillSwitchState.organization_id == organization_id)
        )
        if row is not None:
            return row

        nested = self._session.begin_nested()
        try:
            row = KillSwitchState(
                organization_id=organization_id,
                active=False,
                version=1,
            )
            self._session.add(row)
            self._session.flush()
            nested.commit()
            return row
        except IntegrityError:
            nested.rollback()
            existing = self._session.scalar(
                select(KillSwitchState).where(KillSwitchState.organization_id == organization_id)
            )
            if existing is None:
                raise
            return existing

    def _to_status(self, row: KillSwitchState) -> KillSwitchStatus:
        global_active = bool(self._settings.global_kill_switch_active)
        return KillSwitchStatus(
            organization_id=row.organization_id,
            active=bool(row.active),
            reason=row.reason,
            activated_by=row.activated_by,
            activated_at=row.activated_at,
            deactivated_by=row.deactivated_by,
            deactivated_at=row.deactivated_at,
            version=int(row.version),
            scope=KillSwitchScope.ORGANIZATION,
            global_active=global_active,
            execution_blocked=bool(row.active) or global_active,
        )

    @staticmethod
    def _require_confirm(payload: KillSwitchMutationRequest) -> None:
        if not payload.confirm:
            raise ValidationAppError(
                "Kill switch changes require confirm=true.",
                details={"reason": "confirmation_required"},
            )
        if not payload.reason.strip():
            raise ValidationAppError(
                "A reason is required for kill switch changes.",
                details={"reason": "reason_required"},
            )

    @staticmethod
    def _check_version(row: KillSwitchState, expected: int | None) -> None:
        if expected is None:
            return
        if int(row.version) != int(expected):
            raise ConflictError(
                "Kill switch was modified concurrently; refresh and retry.",
                details={
                    "reason": "version_conflict",
                    "expected_version": str(expected),
                    "current_version": str(row.version),
                },
            )

    def _audit_mutation(
        self,
        *,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID | None,
        event_type: AuditEventType,
        row: KillSwitchState,
        actor_type: ActorType = ActorType.USER,
    ) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id="kill-switch",
                trace_id="kill-switch",
                event_type=event_type,
                resource_type="kill_switch",
                resource_id=str(row.id),
                organization_id=organization_id,
                user_id=actor_user_id,
                actor_type=actor_type,
                result=AuditResult.SUCCESS,
                severity=AuditSeverity.CRITICAL if row.active else AuditSeverity.HIGH,
                metadata={
                    "active": str(row.active),
                    "version": str(row.version),
                    "scope": KillSwitchScope.ORGANIZATION,
                    "reason": (row.reason or "")[:200],
                },
            )
        )

    def _safety_epochs(self) -> SafetyEpochService:
        from app.services.risk.settings_service import RiskSettingsService
        from app.services.safety_epoch import SafetyEpochService

        return SafetyEpochService(
            self._session,
            self._settings,
            RiskSettingsService(self._session, self._audit),
        )

    def _audit_trigger(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        reason: str,
    ) -> None:
        # Block path raises before the route commits — keep the trigger durable.
        self._audit.record_durable_isolated(
            AuditRecordCreate(
                request_id="kill-switch",
                trace_id="kill-switch",
                event_type=AuditEventType.KILL_SWITCH_TRIGGERED,
                resource_type="kill_switch",
                resource_id=str(organization_id),
                organization_id=organization_id,
                user_id=user_id,
                actor_type=ActorType.SYSTEM,
                result=AuditResult.BLOCKED,
                severity=AuditSeverity.CRITICAL,
                metadata={"reason": reason, "scope": KillSwitchScope.ORGANIZATION},
            )
        )
