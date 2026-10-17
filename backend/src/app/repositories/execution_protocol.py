"""Persistence helpers for the Phase 1 execution-claim protocol."""

from __future__ import annotations

import uuid
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult

from app.db.models import (
    AccountRiskAccountingState,
    AccountSafetyEpoch,
    ExecutionCommand,
    ExecutionFillFact,
    ExecutionIdempotencyBinding,
    ExecutionProjection,
    ExecutionReceipt,
    ExecutionTransition,
    PlanEntryExecutionClaim,
    RiskReservation,
    VenueSubmitEffect,
)
from app.repositories.base import SQLAlchemyRepository
from app.schemas.execution_protocol import (
    ExecutionReceiptState,
    VenueSubmitEffectState,
)


class ExecutionIdempotencyRepository(SQLAlchemyRepository[ExecutionIdempotencyBinding]):
    model = ExecutionIdempotencyBinding

    def get_for_update(
        self,
        *,
        organization_id: uuid.UUID,
        opaque_key: str,
    ) -> ExecutionIdempotencyBinding | None:
        stmt = (
            select(ExecutionIdempotencyBinding)
            .where(
                ExecutionIdempotencyBinding.organization_id == organization_id,
                ExecutionIdempotencyBinding.opaque_key == opaque_key,
            )
            .with_for_update()
        )
        return self._session.scalar(stmt)


class ExecutionCommandRepository(SQLAlchemyRepository[ExecutionCommand]):
    model = ExecutionCommand


class ExecutionReceiptRepository(SQLAlchemyRepository[ExecutionReceipt]):
    model = ExecutionReceipt

    def get_by_command(self, command_id: uuid.UUID) -> ExecutionReceipt | None:
        stmt = select(ExecutionReceipt).where(ExecutionReceipt.command_id == command_id)
        return self._session.scalar(stmt)


class ExecutionTransitionRepository(SQLAlchemyRepository[ExecutionTransition]):
    model = ExecutionTransition

    def list_for_receipt(self, receipt_id: uuid.UUID) -> list[ExecutionTransition]:
        stmt = (
            select(ExecutionTransition)
            .where(ExecutionTransition.receipt_id == receipt_id)
            .order_by(ExecutionTransition.sequence.asc())
        )
        return list(self._session.scalars(stmt).all())

    def next_sequence(self, receipt_id: uuid.UUID) -> int:
        rows = self.list_for_receipt(receipt_id)
        return (rows[-1].sequence + 1) if rows else 1


class ExecutionProjectionRepository(SQLAlchemyRepository[ExecutionProjection]):
    model = ExecutionProjection

    def get_by_receipt(self, receipt_id: uuid.UUID) -> ExecutionProjection | None:
        stmt = select(ExecutionProjection).where(ExecutionProjection.receipt_id == receipt_id)
        return self._session.scalar(stmt)

    def compare_and_set_version(
        self,
        *,
        receipt_id: uuid.UUID,
        expected_version: int,
        values: dict[str, object],
    ) -> bool:
        stmt = (
            update(ExecutionProjection)
            .where(
                ExecutionProjection.receipt_id == receipt_id,
                ExecutionProjection.version == expected_version,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        result = cast(CursorResult[Any], self._session.execute(stmt))
        return bool(result.rowcount)


class PlanEntryClaimRepository(SQLAlchemyRepository[PlanEntryExecutionClaim]):
    model = PlanEntryExecutionClaim

    def get_for_revision(
        self,
        *,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
        exchange_account_scope_key: str,
        revision_id: uuid.UUID,
    ) -> PlanEntryExecutionClaim | None:
        stmt = select(PlanEntryExecutionClaim).where(
            PlanEntryExecutionClaim.organization_id == organization_id,
            PlanEntryExecutionClaim.account_id == account_id,
            PlanEntryExecutionClaim.exchange_account_scope_key == exchange_account_scope_key,
            PlanEntryExecutionClaim.revision_id == revision_id,
        )
        return self._session.scalar(stmt)


class AccountSafetyEpochRepository(SQLAlchemyRepository[AccountSafetyEpoch]):
    model = AccountSafetyEpoch

    def get_for_update(
        self,
        *,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
    ) -> AccountSafetyEpoch | None:
        stmt = (
            select(AccountSafetyEpoch)
            .where(
                AccountSafetyEpoch.organization_id == organization_id,
                AccountSafetyEpoch.account_id == account_id,
            )
            .with_for_update()
        )
        return self._session.scalar(stmt)

    def list_for_organization_for_update(
        self, organization_id: uuid.UUID
    ) -> list[AccountSafetyEpoch]:
        stmt = (
            select(AccountSafetyEpoch)
            .where(AccountSafetyEpoch.organization_id == organization_id)
            .order_by(AccountSafetyEpoch.account_id.asc())
            .with_for_update()
        )
        return list(self._session.scalars(stmt).all())


class AccountRiskAccountingRepository(SQLAlchemyRepository[AccountRiskAccountingState]):
    model = AccountRiskAccountingState

    def get_for_update(
        self,
        *,
        organization_id: uuid.UUID,
        account_id: uuid.UUID,
    ) -> AccountRiskAccountingState | None:
        stmt = (
            select(AccountRiskAccountingState)
            .where(
                AccountRiskAccountingState.organization_id == organization_id,
                AccountRiskAccountingState.account_id == account_id,
            )
            .with_for_update()
        )
        return self._session.scalar(stmt)


class RiskReservationRepository(SQLAlchemyRepository[RiskReservation]):
    model = RiskReservation

    def get_by_command(self, command_id: uuid.UUID) -> RiskReservation | None:
        stmt = select(RiskReservation).where(RiskReservation.command_id == command_id)
        return self._session.scalar(stmt)

    def get_by_command_for_update(self, command_id: uuid.UUID) -> RiskReservation | None:
        stmt = (
            select(RiskReservation)
            .where(RiskReservation.command_id == command_id)
            .with_for_update()
        )
        return self._session.scalar(stmt)


class ExecutionFillFactRepository(SQLAlchemyRepository[ExecutionFillFact]):
    model = ExecutionFillFact

    def get_by_source(
        self, *, receipt_id: uuid.UUID, source_fill_identity: str
    ) -> ExecutionFillFact | None:
        stmt = select(ExecutionFillFact).where(
            ExecutionFillFact.receipt_id == receipt_id,
            ExecutionFillFact.source_fill_identity == source_fill_identity,
        )
        return self._session.scalar(stmt)

    def list_for_receipt(self, receipt_id: uuid.UUID) -> list[ExecutionFillFact]:
        stmt = (
            select(ExecutionFillFact)
            .where(ExecutionFillFact.receipt_id == receipt_id)
            .order_by(ExecutionFillFact.occurred_at.asc(), ExecutionFillFact.id.asc())
        )
        return list(self._session.scalars(stmt).all())


class VenueSubmitEffectRepository(SQLAlchemyRepository[VenueSubmitEffect]):
    model = VenueSubmitEffect

    def get_by_command(self, command_id: uuid.UUID) -> VenueSubmitEffect | None:
        stmt = select(VenueSubmitEffect).where(VenueSubmitEffect.command_id == command_id)
        return self._session.scalar(stmt)

    def get_by_command_for_update(self, command_id: uuid.UUID) -> VenueSubmitEffect | None:
        # The lock serializes competing workers, but the Session may already
        # hold an older effect. Refresh state before granting a lease or send.
        stmt = (
            select(VenueSubmitEffect)
            .where(VenueSubmitEffect.command_id == command_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self._session.scalar(stmt)

    def get_for_update(self, effect_id: uuid.UUID) -> VenueSubmitEffect | None:
        stmt = select(VenueSubmitEffect).where(VenueSubmitEffect.id == effect_id).with_for_update()
        return self._session.scalar(stmt)


def receipt_state_name(state: ExecutionReceiptState | str | None) -> str | None:
    if state is None:
        return None
    return state.value if isinstance(state, ExecutionReceiptState) else str(state)


def effect_is_leased(effect: VenueSubmitEffect, *, owner: str, fencing_token: int) -> bool:
    return (
        effect.state is VenueSubmitEffectState.LEASED
        and effect.lease_owner == owner
        and int(effect.fencing_token) == fencing_token
    )
