"""§24 claim transaction for EXECUTE_PAPER_PLAN.

Transaction boundary: this service flushes protocol rows and never performs
network I/O. The caller owns commit. Lock order after idempotency:

1. account safety epoch
2. account risk accounting
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, TradingPolicyError, ValidationAppError
from app.core.operation_policy import PersistenceKind, assert_write_allowed, get_operation_decision
from app.core.paper_safety import assert_execution_capable_composition_root
from app.db.models import (
    AccountRiskAccountingState,
    ExecutionCommand,
    ExecutionIdempotencyBinding,
    ExecutionProjection,
    ExecutionReceipt,
    JournalLifecycleEvent,
    PlanEntryExecutionClaim,
    RiskReservation,
    VenueSubmitEffect,
)
from app.repositories.approvals import (
    ApprovalAuthorizationRepository,
    exchange_account_scope_key,
)
from app.repositories.execution_protocol import (
    ExecutionCommandRepository,
    ExecutionIdempotencyRepository,
    ExecutionProjectionRepository,
    ExecutionReceiptRepository,
    PlanEntryClaimRepository,
    RiskReservationRepository,
    VenueSubmitEffectRepository,
)
from app.repositories.trade_plans import ExecutionAccountRepository, TradePlanRevisionRepository
from app.schemas.agent import RequestedAction
from app.schemas.approval import ApprovalAuthorization
from app.schemas.canonical_execution import CanonicalExecutionSerializationV1
from app.schemas.common import JournalLifecycleEventType
from app.schemas.execution_protocol import (
    EXECUTION_POLICY_PROTOCOL_VERSION,
    SUBMIT_ENTRY_NAMESPACE,
    ExecutePaperPlanRequest,
    ExecutePaperPlanResult,
    ExecutionCommandOutcome,
    ExecutionProjectionView,
    ExecutionReceiptState,
    ExecutionReceiptView,
    ExecutionReconciliationStatus,
    RiskReservationReleaseState,
    RiskReservationView,
    VenueSubmitEffectState,
    VenueSubmitEffectView,
)
from app.schemas.trade_plan import PlanOperation, TradePlanRevision
from app.services.approval_service import ApprovalService
from app.services.canonical_execution_journal import CANONICAL_EXECUTION_SOURCE_SYSTEM
from app.services.canonical_execution_payload import CanonicalExecutionPayloadSerializerV1
from app.services.demo_account_history import has_demo_entry_history
from app.services.execution_client_order_id import derive_entry_client_order_id
from app.services.execution_exposure import QuoteExposureError, linear_quote_exposure
from app.services.execution_integrity import (
    ensure_db_transaction,
    is_idempotency_unique_violation,
    is_plan_claim_unique_violation,
)
from app.services.execution_transitions import append_transition
from app.services.mappers.trade_plan_mapper import trade_plan_revision_to_schema
from app.services.planned_reward_risk import PlannedRewardRiskError, execution_reward_risk
from app.services.safety_epoch import SafetyEpochService


@dataclass(frozen=True)
class ReservationIntent:
    pending_notional: Decimal
    daily_loss: Decimal
    trade_slots: int
    instrument: str
    exposure_unit: str
    remaining_quantity: Decimal
    quantity_unit: str
    contract_multiplier: Decimal
    contract_type: str


class ClaimRevalidator(Protocol):
    def __call__(
        self,
        *,
        plan: TradePlanRevision,
        authorization: ApprovalAuthorization,
        request: ExecutePaperPlanRequest,
        at: datetime,
    ) -> str | None: ...


@dataclass
class ExecutionClaimHooks:
    after_idempotency: Callable[[], None] | None = None
    after_epoch_lock: Callable[[], None] | None = None
    before_return: Callable[[], None] | None = None
    revalidate: ClaimRevalidator | None = None
    manual_demo_capacity: ClaimRevalidator | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class PaperPlanClaimService:
    """First-writer claim transaction. APPROVE never calls this."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        safety_epochs: SafetyEpochService,
        *,
        clock: Callable[[], datetime] = _now,
        hooks: ExecutionClaimHooks | None = None,
        new_id: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self._session = session
        self._settings = settings
        self._epochs = safety_epochs
        self._clock = clock
        self._hooks = hooks or ExecutionClaimHooks()
        self._new_id = new_id
        self._idempotency = ExecutionIdempotencyRepository(session)
        self._commands = ExecutionCommandRepository(session)
        self._receipts = ExecutionReceiptRepository(session)
        self._projections = ExecutionProjectionRepository(session)
        self._claims = PlanEntryClaimRepository(session)
        self._reservations = RiskReservationRepository(session)
        self._effects = VenueSubmitEffectRepository(session)
        self._authorizations = ApprovalAuthorizationRepository(session)
        self._revisions = TradePlanRevisionRepository(session)
        self._accounts = ExecutionAccountRepository(session)

    def claim(self, request: ExecutePaperPlanRequest) -> ExecutePaperPlanResult:
        self._assert_entry_boundary(request)
        ensure_db_transaction(self._session)
        now = _aware(self._clock())
        plan_row = self._revisions.get_scoped(
            request.revision_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
        )
        if plan_row is None:
            raise NotFoundError("Trade plan revision not found")
        account = self._accounts.get_scoped(
            request.account_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
        )
        if account is None or not account.enabled:
            raise TradingPolicyError(
                "Execution account is missing or disabled.",
                details={"reason": "execution_account_unavailable"},
            )
        plan = trade_plan_revision_to_schema(plan_row)
        auth_preview = self._authorizations.get(request.authorization_id)
        if auth_preview is None:
            raise NotFoundError("Approval authorization not found")
        authorization = ApprovalService._authorization_to_schema(auth_preview)
        serialization = self._derive_payload(plan, authorization)
        payload_hash = serialization.sha256
        scope_key = exchange_account_scope_key(plan.exchange_account_id)
        binding = self._insert_or_lock_idempotency(
            request=request,
            plan=plan,
            payload_hash=payload_hash,
            scope_key=scope_key,
        )
        self._run_hook(self._hooks.after_idempotency)
        if binding.command_id is not None and binding.receipt_id is not None:
            self._assert_binding_matches(
                binding,
                request=request,
                plan=plan,
                payload_hash=payload_hash,
            )
            result = self._load_result(binding, replayed=True)
            self._run_hook(self._hooks.before_return)
            return result
        self._assert_binding_matches(
            binding,
            request=request,
            plan=plan,
            payload_hash=payload_hash,
        )
        locked_plan = self._revisions.get_scoped(
            request.revision_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
            for_update=True,
        )
        auth_row = self._authorizations.get_for_update(request.authorization_id)
        if locked_plan is None or auth_row is None:
            raise NotFoundError("Plan or authorization disappeared during claim.")
        plan = trade_plan_revision_to_schema(locked_plan)
        authorization = ApprovalService._authorization_to_schema(auth_row)
        locked_hash = self._derive_payload(plan, authorization).sha256
        if locked_hash != payload_hash:
            raise ConflictError(
                "Canonical payload changed after idempotency bind.",
                details={"reason": "canonical_payload_changed"},
            )
        command_id = self._new_id()
        blocked_reason = self._revalidate_without_consuming(
            plan=plan,
            authorization=authorization,
            request=request,
            at=now,
        )
        epoch = self._epochs.lock_epoch(
            organization_id=request.organization_id, account_id=request.account_id
        )
        self._run_hook(self._hooks.after_epoch_lock)
        # The epoch serializes competing demo claims across all instruments.
        if (
            blocked_reason is None
            and plan.execution_venue == "BLOFIN_DEMO"
            and has_demo_entry_history(
                self._session,
                organization_id=request.organization_id,
                account_id=request.account_id,
            )
        ):
            blocked_reason = "demo_account_already_claimed"
        intent = conservative_reservation(plan)
        accounting = self._epochs.lock_risk_accounting(
            organization_id=request.organization_id,
            account_id=request.account_id,
            user_id=request.user_id,
            exposure_unit=intent.exposure_unit,
        )
        if blocked_reason is None and plan.execution_venue == "BLOFIN_DEMO":
            from app.experiments.execution_risk import experiment_reservation_refusal

            blocked_reason = experiment_reservation_refusal(self._session, plan, intent, now)
        if blocked_reason is None:
            if plan.schema_version == "ManualDemoTradePlanV1":
                # The ledger keeps reservations/fill facts for every origin. Its
                # simulator limits/PnL/trade slots do not govern connectivity tests.
                # A fresh venue policy hook is mandatory and runs under the epoch.
                if epoch.blocking or self._epochs.organization_kill_active(request.organization_id):
                    blocked_reason = "safety_epoch_blocking"
                elif self._hooks.manual_demo_capacity is None:
                    blocked_reason = "manual_demo_account_evidence_required"
                else:
                    blocked_reason = self._hooks.manual_demo_capacity(
                        plan=plan, authorization=authorization, request=request, at=now
                    )
            else:
                blocked_reason = evaluate_claim_predicate(
                    blocking_epoch=bool(epoch.blocking),
                    kill_active=self._epochs.organization_kill_active(request.organization_id),
                    accounting=accounting,
                    intent=intent,
                )
        if blocked_reason is not None:
            result = self._persist_blocked(
                request=request,
                plan=plan,
                authorization=authorization,
                payload_hash=payload_hash,
                command_id=command_id,
                binding=binding,
                reason=blocked_reason,
                intent=intent,
                now=now,
            )
            self._run_hook(self._hooks.before_return)
            return result
        result = self._persist_allow(
            request=request,
            plan=plan,
            authorization=authorization,
            payload_hash=payload_hash,
            command_id=command_id,
            binding=binding,
            scope_key=scope_key,
            epoch_value=int(epoch.epoch),
            accounting=accounting,
            intent=intent,
            now=now,
        )
        self._run_hook(self._hooks.before_return)
        return result

    def _assert_entry_boundary(self, request: ExecutePaperPlanRequest) -> None:
        assert_execution_capable_composition_root(self._settings)
        assert_write_allowed(PersistenceKind.EXECUTION)
        if self._settings.real_trading_enabled:
            raise TradingPolicyError(
                "Real trading is disabled in this environment.",
                details={"reason": "real_trading_enabled"},
            )
        if not request.idempotency_key or request.authorization_id is None:
            raise ValidationAppError(
                "EXECUTE_PAPER_PLAN requires authorization, revision and idempotency key."
            )
        decision = get_operation_decision()
        if (
            decision is not None
            and decision.requested_action is not RequestedAction.EXECUTE_PAPER_PLAN
        ):
            raise TradingPolicyError(
                "Entry execution requires exact EXECUTE_PAPER_PLAN.",
                details={"requested_action": decision.requested_action.value},
            )
        if request.requested_action != RequestedAction.EXECUTE_PAPER_PLAN.value:
            raise TradingPolicyError(
                "Entry execution requires exact EXECUTE_PAPER_PLAN.",
                details={"requested_action": request.requested_action},
            )

    def _derive_payload(
        self,
        plan: TradePlanRevision,
        authorization: ApprovalAuthorization,
    ) -> CanonicalExecutionSerializationV1:
        try:
            payload = CanonicalExecutionPayloadSerializerV1.build_from_plan(plan, authorization)
            return CanonicalExecutionPayloadSerializerV1.serialize(payload)
        except ValidationAppError as exc:
            raise TradingPolicyError(
                str(exc),
                details={"reason": "canonical_payload_rejected"},
            ) from exc

    def _insert_or_lock_idempotency(
        self,
        *,
        request: ExecutePaperPlanRequest,
        plan: TradePlanRevision,
        payload_hash: str,
        scope_key: str,
    ) -> ExecutionIdempotencyBinding:
        row = ExecutionIdempotencyBinding(
            organization_id=request.organization_id,
            user_id=request.user_id,
            account_id=request.account_id,
            exchange_account_id=plan.exchange_account_id,
            exchange_account_scope_key=scope_key,
            operation_namespace=SUBMIT_ENTRY_NAMESPACE,
            opaque_key=request.idempotency_key,
            canonical_payload_hash=payload_hash,
            plan_id=plan.plan_id,
            revision_id=plan.revision_id,
            authorization_id=request.authorization_id,
        )
        try:
            with self._session.begin_nested():
                self._idempotency.add(row)
                return row
        except IntegrityError as exc:
            if not is_idempotency_unique_violation(exc):
                raise
            if row in self._session:
                self._session.expunge(row)
        locked = self._idempotency.get_for_update(
            organization_id=request.organization_id,
            opaque_key=request.idempotency_key,
        )
        if locked is None:
            raise ConflictError(
                "Idempotency binding could not be locked.",
                details={"reason": "idempotency_lock_failed"},
            )
        return locked

    def _assert_binding_matches(
        self,
        binding: ExecutionIdempotencyBinding,
        *,
        request: ExecutePaperPlanRequest,
        plan: TradePlanRevision,
        payload_hash: str,
    ) -> None:
        if binding.user_id != request.user_id:
            raise ConflictError(
                "Idempotency key is bound to a different principal.",
                details={"reason": "idempotency_principal_conflict"},
            )
        if binding.account_id != request.account_id:
            raise ConflictError(
                "Idempotency key is bound to a different account.",
                details={"reason": "idempotency_account_conflict"},
            )
        if binding.operation_namespace != SUBMIT_ENTRY_NAMESPACE:
            raise ConflictError(
                "Idempotency key is bound to a different operation.",
                details={"reason": "idempotency_operation_conflict"},
            )
        if binding.canonical_payload_hash != payload_hash:
            raise ConflictError(
                "Idempotency key is bound to a different canonical payload.",
                details={"reason": "idempotency_payload_conflict"},
            )
        if binding.revision_id != plan.revision_id:
            raise ConflictError(
                "Idempotency key is bound to a different plan revision.",
                details={"reason": "idempotency_plan_conflict"},
            )

    def _revalidate_without_consuming(
        self,
        *,
        plan: TradePlanRevision,
        authorization: ApprovalAuthorization,
        request: ExecutePaperPlanRequest,
        at: datetime,
    ) -> str | None:
        from app.schemas.trade_plan import AuthorizationState

        if authorization.state is not AuthorizationState.AVAILABLE:
            return "authorization_unavailable"
        if _aware(authorization.expires_at) <= at:
            return "authorization_expired"
        if _aware(plan.valid_from) > at or _aware(plan.valid_until) <= at:
            return "plan_validity_window"
        if plan.account_id != authorization.account_id:
            return "account_mismatch"
        if plan.account_id != request.account_id:
            return "account_mismatch"
        if plan.content_hash != authorization.plan_content_hash:
            return "plan_hash_mismatch"
        try:
            execution_reward_risk(plan)
        except PlannedRewardRiskError as exc:
            return exc.reason
        if self._hooks.revalidate is not None:
            return self._hooks.revalidate(
                plan=plan,
                authorization=authorization,
                request=request,
                at=at,
            )
        return None

    def _persist_blocked(
        self,
        *,
        request: ExecutePaperPlanRequest,
        plan: TradePlanRevision,
        authorization: ApprovalAuthorization,
        payload_hash: str,
        command_id: uuid.UUID,
        binding: ExecutionIdempotencyBinding,
        reason: str,
        intent: ReservationIntent,
        now: datetime,
    ) -> ExecutePaperPlanResult:
        command = ExecutionCommand(
            id=command_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
            account_id=plan.account_id,
            exchange_account_id=plan.exchange_account_id,
            operation=PlanOperation.SUBMIT_ENTRY,
            operation_namespace=SUBMIT_ENTRY_NAMESPACE,
            plan_id=plan.plan_id,
            revision_id=plan.revision_id,
            authorization_id=authorization.authorization_id,
            plan_content_hash=plan.content_hash,
            canonical_payload_hash=payload_hash,
            opaque_idempotency_key=request.idempotency_key,
            correlation_id=request.correlation_id or self._new_id(),
            outcome=ExecutionCommandOutcome.BLOCKED,
            blocked_reason_code=reason,
        )
        self._commands.add(command)
        receipt = ExecutionReceipt(
            command_id=command.id,
            operation=PlanOperation.SUBMIT_ENTRY,
            authorization_id=authorization.authorization_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
            account_id=plan.account_id,
        )
        self._receipts.add(receipt)
        append_transition(
            self._session,
            receipt=receipt,
            prior_state=None,
            new_state=ExecutionReceiptState.BLOCKED,
            source_fact="claim_predicate_block",
            source_identity=reason,
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            actor="execution_service",
        )
        projection = ExecutionProjection(
            receipt_id=receipt.id,
            version=1,
            state=ExecutionReceiptState.BLOCKED,
            filled_quantity=Decimal("0"),
            remaining_quantity=intent.remaining_quantity,
            quantity_unit=intent.quantity_unit,
            weighted_price=None,
            fees=Decimal("0"),
            funding=Decimal("0"),
            position_id=None,
            reconciliation_status=ExecutionReconciliationStatus.NOT_REQUIRED,
            event_watermark=1,
        )
        self._projections.add(projection)
        binding.command_id = command.id
        binding.receipt_id = receipt.id
        binding.outcome = ExecutionCommandOutcome.BLOCKED
        self._session.flush()
        return self._load_result(binding, replayed=False)

    def _persist_allow(
        self,
        *,
        request: ExecutePaperPlanRequest,
        plan: TradePlanRevision,
        authorization: ApprovalAuthorization,
        payload_hash: str,
        command_id: uuid.UUID,
        binding: ExecutionIdempotencyBinding,
        scope_key: str,
        epoch_value: int,
        accounting: AccountRiskAccountingState,
        intent: ReservationIntent,
        now: datetime,
    ) -> ExecutePaperPlanResult:
        command = ExecutionCommand(
            id=command_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
            account_id=request.account_id,
            exchange_account_id=plan.exchange_account_id,
            operation=PlanOperation.SUBMIT_ENTRY,
            operation_namespace=SUBMIT_ENTRY_NAMESPACE,
            plan_id=plan.plan_id,
            revision_id=plan.revision_id,
            authorization_id=authorization.authorization_id,
            plan_content_hash=plan.content_hash,
            canonical_payload_hash=payload_hash,
            opaque_idempotency_key=request.idempotency_key,
            correlation_id=request.correlation_id or self._new_id(),
            outcome=ExecutionCommandOutcome.ALLOW,
            blocked_reason_code=None,
            created_at=now,
            updated_at=now,
        )
        receipt = ExecutionReceipt(
            id=self._new_id(),
            command_id=command.id,
            operation=PlanOperation.SUBMIT_ENTRY,
            authorization_id=authorization.authorization_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
            account_id=request.account_id,
        )
        claim = PlanEntryExecutionClaim(
            organization_id=request.organization_id,
            user_id=request.user_id,
            account_id=request.account_id,
            exchange_account_id=plan.exchange_account_id,
            exchange_account_scope_key=scope_key,
            revision_id=plan.revision_id,
            operation=PlanOperation.SUBMIT_ENTRY,
            command_id=command.id,
            receipt_id=receipt.id,
            canonical_payload_hash=payload_hash,
        )
        try:
            with self._session.begin_nested():
                self._commands.add(command)
                self._receipts.add(receipt)
                self._claims.add(claim)
        except IntegrityError as exc:
            if not is_plan_claim_unique_violation(exc):
                raise
            for obj in (command, receipt, claim):
                if obj in self._session:
                    self._session.expunge(obj)
            return self._persist_blocked(
                request=request,
                plan=plan,
                authorization=authorization,
                payload_hash=payload_hash,
                command_id=self._new_id(),
                binding=binding,
                reason="plan_entry_already_claimed",
                intent=intent,
                now=now,
            )
        consumed = self._authorizations.compare_and_set_available_to_consumed(
            authorization_id=authorization.authorization_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
            account_id=request.account_id,
            execution_command_id=command.id,
            consumed_at=now,
        )
        if not consumed:
            raise ConflictError(
                "Authorization was not AVAILABLE for consumption.",
                details={"reason": "authorization_consume_conflict"},
            )
        charge_reservation(accounting, intent)
        reservation = RiskReservation(
            organization_id=request.organization_id,
            account_id=request.account_id,
            plan_revision_id=plan.revision_id,
            command_id=command.id,
            receipt_id=receipt.id,
            instrument=intent.instrument,
            risk_policy_version=EXECUTION_POLICY_PROTOCOL_VERSION,
            risk_snapshot_version=f"epoch-{epoch_value}",
            pending_order_exposure=intent.pending_notional,
            submitting_or_ambiguous_exposure=intent.pending_notional,
            open_order_notional=intent.pending_notional,
            daily_trade_allocation=intent.trade_slots,
            daily_loss_allocation=intent.daily_loss,
            total_exposure=intent.pending_notional,
            symbol_exposure=intent.pending_notional,
            remaining_reserved_notional=intent.pending_notional,
            converted_trade_slots=0,
            contract_multiplier=intent.contract_multiplier,
            contract_type=intent.contract_type,
            quantity_unit=intent.quantity_unit,
            exposure_unit=intent.exposure_unit,
            safety_epoch=epoch_value,
            release_state=RiskReservationReleaseState.CHARGED,
            release_reason=None,
        )
        self._reservations.add(reservation)
        append_transition(
            self._session,
            receipt=receipt,
            prior_state=None,
            new_state=ExecutionReceiptState.SUBMITTING,
            source_fact="claim_allow",
            source_identity=str(command.id),
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            actor="execution_service",
            quantity=intent.remaining_quantity,
            quantity_unit=intent.quantity_unit,
        )
        self._projections.add(
            ExecutionProjection(
                receipt_id=receipt.id,
                version=1,
                state=ExecutionReceiptState.SUBMITTING,
                filled_quantity=Decimal("0"),
                remaining_quantity=intent.remaining_quantity,
                quantity_unit=intent.quantity_unit,
                weighted_price=None,
                fees=Decimal("0"),
                funding=Decimal("0"),
                position_id=None,
                reconciliation_status=ExecutionReconciliationStatus.NOT_REQUIRED,
                event_watermark=1,
            )
        )
        client_order_id = derive_entry_client_order_id(
            account_id=request.account_id,
            revision_id=plan.revision_id,
            canonical_payload_hash=payload_hash,
        )
        self._effects.add(
            VenueSubmitEffect(
                command_id=command.id,
                receipt_id=receipt.id,
                client_order_id=client_order_id,
                state=VenueSubmitEffectState.CREATED,
                lease_owner=None,
                lease_expires_at=None,
                fencing_token=0,
                attempt=0,
                dispatch_authorized_at=None,
                dispatch_safety_epoch=None,
                dispatch_fencing_token=None,
                dispatch_attempt=None,
                safety_epoch=epoch_value,
                uncertainty=False,
                reconciliation_disposition="NOT_SENT",
            )
        )
        binding.command_id = command.id
        binding.receipt_id = receipt.id
        binding.outcome = ExecutionCommandOutcome.ALLOW
        self._session.flush()
        return self._load_result(binding, replayed=False)

    def _load_result(
        self, binding: ExecutionIdempotencyBinding, *, replayed: bool
    ) -> ExecutePaperPlanResult:
        if binding.command_id is None or binding.receipt_id is None or binding.outcome is None:
            raise ConflictError(
                "Idempotency binding is incomplete.",
                details={"reason": "idempotency_incomplete"},
            )
        command = self._commands.get(binding.command_id)
        receipt = self._receipts.get(binding.receipt_id)
        projection = self._projections.get_by_receipt(binding.receipt_id)
        if command is None or receipt is None or projection is None:
            raise ConflictError(
                "Stable execution identities are missing.",
                details={"reason": "execution_identity_missing"},
            )
        effect = self._effects.get_by_command(command.id)
        reservation = self._reservations.get_by_command(command.id)
        fees: Decimal | None = projection.fees
        funding: Decimal | None = projection.funding
        revision = self._revisions.get(command.revision_id)
        if revision is not None and revision.execution_policy_version == "governed-blofin-demo/v1":
            # The legacy storage has nonnullable zero defaults. Native funding
            # and unknown fees must not be presented as exchange-confirmed zero.
            fees, funding = None, None
            events = (
                select(JournalLifecycleEvent)
                .where(
                    JournalLifecycleEvent.organization_id == command.organization_id,
                    JournalLifecycleEvent.user_id == command.user_id,
                    JournalLifecycleEvent.account_id == command.account_id,
                    JournalLifecycleEvent.execution_lifecycle_id == command.id,
                    JournalLifecycleEvent.source_system == CANONICAL_EXECUTION_SOURCE_SYSTEM,
                    JournalLifecycleEvent.source_aggregate == "execution-command",
                )
                .order_by(JournalLifecycleEvent.created_at.desc(), JournalLifecycleEvent.id.desc())
                .limit(1)
            )
            monetary_event = self._session.scalar(
                events.where(JournalLifecycleEvent.event_type == JournalLifecycleEventType.CLOSE)
            ) or self._session.scalar(
                events.where(JournalLifecycleEvent.event_type == JournalLifecycleEventType.FILL)
            )
            if monetary_event is not None:
                currency_key = (
                    "venue_reported_fee_currency"
                    if monetary_event.event_type == JournalLifecycleEventType.CLOSE
                    else "venue_fill_fee_currency"
                )
                currency = trade_plan_revision_to_schema(
                    revision
                ).instrument_rules.settlement_currency
                amount = monetary_event.payload.get("fees")
                if amount is not None and monetary_event.payload.get(currency_key) == currency:
                    fees = Decimal(str(amount))
        return ExecutePaperPlanResult(
            replayed=replayed,
            outcome=command.outcome,
            command_id=command.id,
            canonical_payload_hash=command.canonical_payload_hash,
            receipt=ExecutionReceiptView(
                receipt_id=receipt.id,
                command_id=command.id,
                operation=command.operation,
                authorization_id=receipt.authorization_id,
                organization_id=receipt.organization_id,
                user_id=receipt.user_id,
                account_id=receipt.account_id,
                created_at=receipt.created_at,
                outcome=command.outcome,
                blocked_reason_code=command.blocked_reason_code,
            ),
            projection=ExecutionProjectionView(
                receipt_id=projection.receipt_id,
                version=projection.version,
                state=projection.state,
                filled_quantity=projection.filled_quantity,
                remaining_quantity=projection.remaining_quantity,
                quantity_unit=projection.quantity_unit,
                weighted_price=projection.weighted_price,
                fees=fees,
                funding=funding,
                position_id=projection.position_id,
                reconciliation_status=projection.reconciliation_status,
                event_watermark=projection.event_watermark,
            ),
            effect=_effect_view(effect),
            reservation=_reservation_view(reservation),
            client_order_id=effect.client_order_id if effect is not None else None,
            blocked_reason_code=command.blocked_reason_code,
        )

    def _run_hook(self, hook: Callable[[], None] | None) -> None:
        if hook is not None:
            hook()


def conservative_reservation(plan: TradePlanRevision) -> ReservationIntent:
    """Conservative claim reservation using immutable plan instrument semantics."""

    quantity = plan.quantity.value
    multiplier = plan.instrument_rules.contract_multiplier
    if plan.limit_price is not None:
        price = plan.limit_price.value
        unit = plan.limit_price.unit
    else:
        price = max(plan.basis_policy.execution_price.value, plan.entry_zone.upper)
        unit = plan.basis_policy.execution_price.unit
    try:
        quote_exposure = linear_quote_exposure(
            quantity=quantity,
            price=price,
            quantity_unit=plan.quantity.unit,
            contract_multiplier=multiplier,
            contract_type=plan.instrument_rules.contract_type.value,
        )
    except QuoteExposureError as exc:
        reason = (
            "inverse_contract_exposure_undefined"
            if exc.reason == "inverse_contract_undefined"
            else exc.reason
        )
        raise TradingPolicyError(str(exc), details={**exc.details, "reason": reason}) from exc
    notional = quote_exposure
    notional += plan.risk_and_exits.fee_allowance.value
    notional += plan.risk_and_exits.slippage_allowance.value
    return ReservationIntent(
        pending_notional=notional,
        daily_loss=plan.risk_and_exits.maximum_loss.value,
        trade_slots=1,
        instrument=plan.execution_instrument,
        exposure_unit=unit,
        remaining_quantity=quantity,
        quantity_unit=plan.quantity.unit,
        contract_multiplier=multiplier,
        contract_type=plan.instrument_rules.contract_type.value,
    )


def evaluate_claim_predicate(
    *,
    blocking_epoch: bool,
    kill_active: bool,
    accounting: AccountRiskAccountingState,
    intent: ReservationIntent,
) -> str | None:
    if blocking_epoch or kill_active:
        return "safety_epoch_blocking"
    if accounting.daily_locked:
        return "daily_loss_locked"
    remaining_notional = (
        accounting.max_notional - accounting.reserved_notional - accounting.actual_notional
    )
    if intent.pending_notional > remaining_notional:
        return "insufficient_total_exposure"
    remaining_loss = (
        accounting.max_daily_loss - accounting.reserved_daily_loss - accounting.actual_daily_loss
    )
    if intent.daily_loss > remaining_loss:
        return "insufficient_daily_loss_allocation"
    remaining_slots = (
        int(accounting.max_trade_slots)
        - int(accounting.reserved_trade_slots)
        - int(accounting.actual_trade_count)
    )
    if remaining_slots < intent.trade_slots:
        return "insufficient_daily_trade_allocation"
    symbol_reserved = Decimal(str(accounting.symbol_reserved.get(intent.instrument, "0")))
    symbol_actual = Decimal(str(accounting.symbol_actual.get(intent.instrument, "0")))
    remaining_symbol = accounting.max_symbol_notional - symbol_reserved - symbol_actual
    if intent.pending_notional > remaining_symbol:
        return "insufficient_symbol_exposure"
    return None


def charge_reservation(accounting: AccountRiskAccountingState, intent: ReservationIntent) -> None:
    accounting.reserved_notional += intent.pending_notional
    accounting.reserved_daily_loss += intent.daily_loss
    accounting.reserved_trade_slots += intent.trade_slots
    reserved = dict(accounting.symbol_reserved or {})
    current = Decimal(str(reserved.get(intent.instrument, "0")))
    reserved[intent.instrument] = str(current + intent.pending_notional)
    accounting.symbol_reserved = reserved
    accounting.version = int(accounting.version) + 1


def _effect_view(effect: VenueSubmitEffect | None) -> VenueSubmitEffectView | None:
    if effect is None:
        return None
    return VenueSubmitEffectView(
        effect_id=effect.id,
        command_id=effect.command_id,
        receipt_id=effect.receipt_id,
        client_order_id=effect.client_order_id,
        state=effect.state,
        fencing_token=effect.fencing_token,
        attempt=effect.attempt,
        lease_owner=effect.lease_owner,
        safety_epoch=int(effect.safety_epoch),
        dispatch_safety_epoch=(
            int(effect.dispatch_safety_epoch) if effect.dispatch_safety_epoch is not None else None
        ),
        uncertainty=effect.uncertainty,
        reconciliation_disposition=effect.reconciliation_disposition,
    )


def _reservation_view(row: RiskReservation | None) -> RiskReservationView | None:
    if row is None:
        return None
    return RiskReservationView(
        reservation_id=row.id,
        command_id=row.command_id,
        receipt_id=row.receipt_id,
        pending_order_exposure=row.pending_order_exposure,
        remaining_reserved_notional=row.remaining_reserved_notional,
        daily_loss_allocation=row.daily_loss_allocation,
        daily_trade_allocation=row.daily_trade_allocation,
        safety_epoch=int(row.safety_epoch),
        release_state=row.release_state,
        release_reason=row.release_reason,
        exposure_unit=row.exposure_unit,
    )
