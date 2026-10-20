"""Approval request persistence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult

from app.db.models import ApprovalAuthorization, ApprovalRequest
from app.repositories.base import SQLAlchemyRepository
from app.schemas.common import ApprovalStatus
from app.schemas.trade_plan import AuthorizationState, PlanOperation


class ApprovalRepository(SQLAlchemyRepository[ApprovalRequest]):
    model = ApprovalRequest

    def get_by_proposal(self, proposal_id: uuid.UUID) -> ApprovalRequest | None:
        stmt = (
            select(ApprovalRequest)
            .where(ApprovalRequest.proposal_id == proposal_id)
            .order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc())
            .limit(1)
        )
        return self._session.scalar(stmt)

    def get_by_revision(self, revision_id: uuid.UUID) -> ApprovalRequest | None:
        stmt = select(ApprovalRequest).where(ApprovalRequest.plan_revision_id == revision_id)
        return self._session.scalar(stmt)

    def get_scoped(
        self,
        approval_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ApprovalRequest | None:
        stmt = select(ApprovalRequest).where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.organization_id == organization_id,
            ApprovalRequest.user_id == user_id,
        )
        return self._session.scalar(stmt)

    def list_approvals(
        self,
        *,
        organization_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        status: ApprovalStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ApprovalRequest], int]:
        filters = []
        if organization_id is not None:
            filters.append(ApprovalRequest.organization_id == organization_id)
        if user_id is not None:
            filters.append(ApprovalRequest.user_id == user_id)
        if status is not None:
            filters.append(ApprovalRequest.status == status)

        count_stmt = select(func.count()).select_from(ApprovalRequest)
        list_stmt = select(ApprovalRequest).order_by(ApprovalRequest.created_at.desc())
        if filters:
            count_stmt = count_stmt.where(*filters)
            list_stmt = list_stmt.where(*filters)
        total = int(self._session.scalar(count_stmt) or 0)
        return list(self._session.scalars(list_stmt.limit(limit).offset(offset)).all()), total


_REUSABLE_AUTHORIZATION_STATES = (
    AuthorizationState.AVAILABLE,
    AuthorizationState.CONSUMED,
)


class ApprovalAuthorizationRepository(SQLAlchemyRepository[ApprovalAuthorization]):
    model = ApprovalAuthorization

    def get_reusable_for_approval(
        self, approval_request_id: uuid.UUID
    ) -> ApprovalAuthorization | None:
        """Return the AVAILABLE or CONSUMED row. REVOKED and EXPIRED are history."""

        stmt = select(ApprovalAuthorization).where(
            ApprovalAuthorization.approval_request_id == approval_request_id,
            ApprovalAuthorization.state.in_(_REUSABLE_AUTHORIZATION_STATES),
        )
        return self._session.scalar(stmt)

    def get_reusable_by_binding(
        self,
        *,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        exchange_account_id: uuid.UUID | None,
        revision_id: uuid.UUID,
        plan_content_hash: str,
        operation: PlanOperation,
    ) -> ApprovalAuthorization | None:
        """Return the reusable binding. A revoked or expired row is not reused."""

        stmt = select(ApprovalAuthorization).where(
            ApprovalAuthorization.organization_id == organization_id,
            ApprovalAuthorization.account_id == account_id,
            ApprovalAuthorization.exchange_account_scope_key
            == _exchange_account_scope_key(exchange_account_id),
            ApprovalAuthorization.revision_id == revision_id,
            ApprovalAuthorization.plan_content_hash == plan_content_hash,
            ApprovalAuthorization.operation == operation,
            ApprovalAuthorization.state.in_(_REUSABLE_AUTHORIZATION_STATES),
        )
        return self._session.scalar(stmt)

    def get_available_scoped(
        self,
        authorization_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        at: datetime,
    ) -> ApprovalAuthorization | None:
        stmt = select(ApprovalAuthorization).where(
            ApprovalAuthorization.id == authorization_id,
            ApprovalAuthorization.organization_id == organization_id,
            ApprovalAuthorization.user_id == user_id,
            ApprovalAuthorization.account_id == account_id,
            ApprovalAuthorization.state == AuthorizationState.AVAILABLE,
            ApprovalAuthorization.expires_at > at,
        )
        return self._session.scalar(stmt)

    def expire_due(self, authorization_id: uuid.UUID, *, at: datetime) -> bool:
        stmt = (
            update(ApprovalAuthorization)
            .where(
                ApprovalAuthorization.id == authorization_id,
                ApprovalAuthorization.state == AuthorizationState.AVAILABLE,
                ApprovalAuthorization.expires_at <= at,
            )
            .values(state=AuthorizationState.EXPIRED, updated_at=at)
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return bool(result.rowcount)

    def revoke_available(self, authorization_id: uuid.UUID, *, at: datetime) -> bool:
        stmt = (
            update(ApprovalAuthorization)
            .where(
                ApprovalAuthorization.id == authorization_id,
                ApprovalAuthorization.state == AuthorizationState.AVAILABLE,
            )
            .values(state=AuthorizationState.REVOKED, updated_at=at)
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return bool(result.rowcount)

    def revoke_for_superseded_plan(
        self,
        *,
        organization_id: uuid.UUID,
        plan_id: uuid.UUID,
        current_revision_id: uuid.UUID,
        at: datetime,
    ) -> int:
        stmt = (
            update(ApprovalAuthorization)
            .where(
                ApprovalAuthorization.organization_id == organization_id,
                ApprovalAuthorization.plan_id == plan_id,
                ApprovalAuthorization.revision_id != current_revision_id,
                ApprovalAuthorization.state == AuthorizationState.AVAILABLE,
            )
            .values(state=AuthorizationState.REVOKED, updated_at=at)
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return int(result.rowcount or 0)

    def get_for_update(self, authorization_id: uuid.UUID) -> ApprovalAuthorization | None:
        stmt = (
            select(ApprovalAuthorization)
            .where(ApprovalAuthorization.id == authorization_id)
            .with_for_update()
        )
        return self._session.scalar(stmt)

    def compare_and_set_available_to_consumed(
        self,
        *,
        authorization_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        execution_command_id: uuid.UUID,
        consumed_at: datetime,
    ) -> bool:
        stmt = (
            update(ApprovalAuthorization)
            .where(
                ApprovalAuthorization.id == authorization_id,
                ApprovalAuthorization.organization_id == organization_id,
                ApprovalAuthorization.user_id == user_id,
                ApprovalAuthorization.account_id == account_id,
                ApprovalAuthorization.state == AuthorizationState.AVAILABLE,
            )
            .values(
                state=AuthorizationState.CONSUMED,
                consumed_at=consumed_at,
                consumed_by_execution_command_id=execution_command_id,
                updated_at=consumed_at,
            )
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return bool(result.rowcount)


class ApprovalAuthorizationConsumptionPort(Protocol):
    """Claim-transaction consumption boundary implemented by the authorization repository."""

    def compare_and_set_available_to_consumed(
        self,
        *,
        authorization_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        execution_command_id: uuid.UUID,
        consumed_at: datetime,
    ) -> bool:
        """Atomically transition AVAILABLE to CONSUMED in the execution transaction."""
        ...


def exchange_account_scope_key(exchange_account_id: uuid.UUID | None) -> str:
    """Normalize nullable exchange-account identity for deterministic uniqueness."""
    return _exchange_account_scope_key(exchange_account_id)


def _exchange_account_scope_key(exchange_account_id: uuid.UUID | None) -> str:
    return str(exchange_account_id) if exchange_account_id is not None else "NONE"
