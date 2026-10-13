"""Phase 1 slices 7-9: idempotency, claim, reservation, epoch, receipt, effect."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, PersistencePolicyError, TradingPolicyError
from app.core.operation_policy import operation_scope
from app.db.models import (
    AccountRiskAccountingState,
    ApprovalAuthorization,
    ApprovalRequest,
    ExecutionCommand,
    ExecutionFillFact,
    ExecutionIdempotencyBinding,
    ExecutionProjection,
    ExecutionReceipt,
    ExecutionTransition,
    Order,
    PlanEntryExecutionClaim,
    Position,
    RiskReservation,
    VenueSubmitEffect,
)
from app.providers.execution.fake_venue import FakeVenueBehavior, FakeVenueSubmitProvider
from app.schemas.agent import Intent, IntentDecision, OperationClass, RequestedAction
from app.schemas.common import ApprovalStatus, OrderSide, OrderType, RiskSeverity
from app.schemas.execution import PaperOrderRequest
from app.schemas.execution_protocol import (
    ExecutionCommandOutcome,
    ExecutionReceiptState,
    RiskReservationReleaseState,
    VenueSubmitEffectState,
)
from app.schemas.risk import KillSwitchMutationRequest
from app.schemas.trade_plan import AuthorizationState
from app.services.audit_service import AuditService
from app.services.execution_claim import conservative_reservation
from app.services.execution_client_order_id import derive_entry_client_order_id
from app.services.execution_service import (
    LEGACY_DEMO_MIRROR_REASON,
    LEGACY_PAPER_EXECUTION_REASON,
)
from app.services.execution_transitions import rebuild_projection_from_transitions
from app.services.risk.kill_switch import KillSwitchService
from tests.support.phase1_plan_fixtures import (
    EXECUTE_AT,
    approve_plan,
    execute_request,
    execution_service,
    persist_plan,
    plan_request,
    prepared_authorized_plan,
    seed_support,
    sqlite_session,
)


@pytest.fixture
def session() -> Iterator[Session]:
    yield from sqlite_session()


def _approve_decision(organization_id: object) -> IntentDecision:
    return IntentDecision(
        intent=Intent.APPROVE,
        operation_class=OperationClass.APPROVAL,
        organization_id=organization_id,  # type: ignore[arg-type]
        requested_action=RequestedAction.APPROVE,
    )


def test_execute_paper_plan_creates_stable_identities(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    result = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="entry-key-1"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    assert result.replayed is False
    assert result.outcome is ExecutionCommandOutcome.ALLOW
    # Internal simulation keeps its defined known-zero accounting contract.
    assert result.projection.fees == result.projection.funding == Decimal("0")
    assert result.effect is not None
    assert result.reservation is not None
    assert result.projection.state is ExecutionReceiptState.SUBMITTING
    assert result.client_order_id == derive_entry_client_order_id(
        account_id=plan.account_id,
        revision_id=plan.revision_id,
        canonical_payload_hash=result.canonical_payload_hash,
    )
    assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 1
    assert session.scalar(select(func.count()).select_from(PlanEntryExecutionClaim)) == 1
    assert session.scalar(select(func.count()).select_from(VenueSubmitEffect)) == 1
    auth = session.get(ApprovalAuthorization, authorization.authorization_id)
    assert auth is not None
    assert auth.state is AuthorizationState.CONSUMED
    assert auth.consumed_by_execution_command_id == result.command_id


def test_approve_does_not_create_execution_command(session: Session) -> None:
    ids = seed_support(session)
    plan = persist_plan(session, ids)
    approve_plan(session, ids, plan)
    session.commit()
    assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 0
    assert session.scalar(select(func.count()).select_from(PlanEntryExecutionClaim)) == 0
    assert session.scalar(select(func.count()).select_from(VenueSubmitEffect)) == 0


def test_approve_intent_cannot_execute(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    org_id = ids["organization"].id
    with operation_scope(_approve_decision(org_id)), pytest.raises(TradingPolicyError):
        service.execute_paper_plan(
            execute_request(ids, plan, authorization, key="blocked-by-approve"),
            clock=lambda: EXECUTE_AT,
        )
    auth = session.get(ApprovalAuthorization, authorization.authorization_id)
    assert auth is not None
    assert auth.state is AuthorizationState.AVAILABLE


def test_replay_same_key_and_payload_returns_stable_receipt(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    first = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="replay-key"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    second = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="replay-key"),
        clock=lambda: EXECUTE_AT,
    )
    assert second.replayed is True
    assert second.command_id == first.command_id
    assert second.receipt.receipt_id == first.receipt.receipt_id
    assert second.client_order_id == first.client_order_id
    assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 1
    assert session.scalar(select(func.count()).select_from(ApprovalAuthorization)) == 1


def test_same_key_different_payload_conflicts(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="payload-key"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    other = persist_plan(
        session, ids, plan_request(ids, quantity={"value": "3", "unit": "CONTRACTS"})
    )
    other_auth = approve_plan(session, ids, other)
    with pytest.raises(ConflictError) as exc:
        service.execute_paper_plan(
            execute_request(ids, other, other_auth, key="payload-key"),
            clock=lambda: EXECUTE_AT,
        )
    assert exc.value.details.get("reason") == "idempotency_payload_conflict"


def test_same_key_different_principal_is_rejected(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="principal-key"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    with pytest.raises((ConflictError, TradingPolicyError, NotFoundError)):
        service.execute_paper_plan(
            execute_request(
                ids,
                plan,
                authorization,
                key="principal-key",
                user_id=ids["other_user"].id,
            ),
            clock=lambda: EXECUTE_AT,
        )


def test_same_key_different_account_conflicts(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="account-key"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    with pytest.raises(ConflictError) as exc:
        service.execute_paper_plan(
            execute_request(
                ids,
                plan,
                authorization,
                key="account-key",
                account_id=ids["account_two"].id,
            ),
            clock=lambda: EXECUTE_AT,
        )
    assert exc.value.details.get("reason") == "idempotency_account_conflict"


def test_different_keys_same_plan_do_not_duplicate_claim(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    first = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="plan-key-a"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    second = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="plan-key-b"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    assert first.outcome is ExecutionCommandOutcome.ALLOW
    assert second.outcome is ExecutionCommandOutcome.BLOCKED
    assert second.blocked_reason_code in {
        "plan_entry_already_claimed",
        "authorization_unavailable",
    }
    assert session.scalar(select(func.count()).select_from(PlanEntryExecutionClaim)) == 1
    assert session.scalar(select(func.count()).select_from(VenueSubmitEffect)) == 1
    auth = session.get(ApprovalAuthorization, authorization.authorization_id)
    assert auth is not None
    assert auth.consumed_by_execution_command_id == first.command_id


def test_kill_switch_before_claim_blocks_without_consumption(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    kill = KillSwitchService(session, AuditService(session), execution_service(session)._settings)
    kill.activate(
        organization_id=ids["organization"].id,
        actor_user_id=ids["user"].id,
        payload=KillSwitchMutationRequest(confirm=True, reason="halt entries"),
    )
    session.commit()
    result = execution_service(session).execute_paper_plan(
        execute_request(ids, plan, authorization, key="killed-before"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    assert result.outcome is ExecutionCommandOutcome.BLOCKED
    assert result.blocked_reason_code == "safety_epoch_blocking"
    assert result.effect is None
    assert result.reservation is None
    auth = session.get(ApprovalAuthorization, authorization.authorization_id)
    assert auth is not None
    assert auth.state is AuthorizationState.AVAILABLE
    replay = execution_service(session).execute_paper_plan(
        execute_request(ids, plan, authorization, key="killed-before"),
        clock=lambda: EXECUTE_AT,
    )
    assert replay.replayed is True
    assert replay.outcome is ExecutionCommandOutcome.BLOCKED


def test_serializable_capacity_blocks_second_command(session: Session) -> None:
    # Demo history has a separate, earlier account lock; isolate exposure capacity.
    ids, plan, authorization = prepared_authorized_plan(session, execution_venue="PAPER_INTERNAL")
    intent = conservative_reservation(plan)
    session.add(
        AccountRiskAccountingState(
            organization_id=ids["organization"].id,
            account_id=plan.account_id,
            reserved_notional=Decimal("0"),
            reserved_daily_loss=Decimal("0"),
            reserved_trade_slots=0,
            actual_notional=Decimal("0"),
            actual_daily_loss=Decimal("0"),
            actual_trade_count=0,
            symbol_reserved={},
            symbol_actual={},
            max_notional=intent.pending_notional,
            max_daily_loss=Decimal("1000"),
            max_trade_slots=20,
            max_symbol_notional=intent.pending_notional,
            daily_locked=False,
            exposure_unit=intent.exposure_unit,
            version=1,
        )
    )
    other = persist_plan(
        session,
        ids,
        plan_request(
            ids, execution_venue="PAPER_INTERNAL", quantity={"value": "3", "unit": "CONTRACTS"}
        ),
    )
    other_auth = approve_plan(session, ids, other)
    session.commit()
    service = execution_service(session)
    first = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="cap-a"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    second = service.execute_paper_plan(
        execute_request(ids, other, other_auth, key="cap-b"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    assert first.outcome is ExecutionCommandOutcome.ALLOW
    assert second.outcome is ExecutionCommandOutcome.BLOCKED
    assert second.blocked_reason_code == "insufficient_total_exposure"
    assert session.scalar(select(func.count()).select_from(RiskReservation)) == 1
    assert session.scalar(select(func.count()).select_from(PlanEntryExecutionClaim)) == 1
    assert session.scalar(select(func.count()).select_from(VenueSubmitEffect)) == 1
    accounting = session.scalar(select(AccountRiskAccountingState))
    assert accounting is not None
    assert accounting.reserved_notional == intent.pending_notional
    assert accounting.reserved_trade_slots == 1
    other_auth_row = session.get(ApprovalAuthorization, other_auth.authorization_id)
    assert other_auth_row is not None
    assert other_auth_row.state is AuthorizationState.AVAILABLE


def test_dispatch_barriers_and_fake_ack(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="dispatch-key"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    leased = service.lease_paper_plan_effect(command_id=claimed.command_id, owner="worker-1")
    session.commit()
    assert leased.state is VenueSubmitEffectState.LEASED
    authorized = service.authorize_paper_plan_dispatch(
        command_id=claimed.command_id,
        owner="worker-1",
        fencing_token=int(leased.fencing_token),
    )
    session.commit()
    assert authorized.state is VenueSubmitEffectState.DISPATCH_AUTHORIZED
    provider = FakeVenueSubmitProvider(behavior=FakeVenueBehavior.ACCEPT)
    sent = service.attempt_fake_paper_plan_send(
        command_id=claimed.command_id,
        owner="worker-1",
        fencing_token=int(leased.fencing_token),
        provider=provider,
    )
    session.commit()
    assert sent.reconciliation_disposition == "ACCEPTED"
    assert provider.submit_count == 1
    projection = session.scalar(
        select(ExecutionProjection).where(
            ExecutionProjection.receipt_id == claimed.receipt.receipt_id
        )
    )
    assert projection is not None
    assert projection.state is ExecutionReceiptState.ACKNOWLEDGED


def test_kill_after_claim_before_lease_blocks_unsent(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="lease-kill"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    KillSwitchService(session, AuditService(session), service._settings).activate(
        organization_id=ids["organization"].id,
        actor_user_id=ids["user"].id,
        payload=KillSwitchMutationRequest(confirm=True, reason="stop before lease"),
    )
    session.commit()
    provider = FakeVenueSubmitProvider()
    effect = service.lease_paper_plan_effect(command_id=claimed.command_id, owner="worker-1")
    session.commit()
    assert effect.state is VenueSubmitEffectState.PROVEN_UNSENT
    with pytest.raises(ConflictError):
        service.attempt_fake_paper_plan_send(
            command_id=claimed.command_id,
            owner="worker-1",
            fencing_token=1,
            provider=provider,
        )
    assert provider.submit_count == 0
    reservation = session.scalar(
        select(RiskReservation).where(RiskReservation.command_id == claimed.command_id)
    )
    assert reservation is not None
    assert reservation.release_state is RiskReservationReleaseState.RELEASED
    auth = session.get(ApprovalAuthorization, authorization.authorization_id)
    assert auth is not None
    assert auth.state is AuthorizationState.CONSUMED


def test_kill_after_lease_before_dispatch_authorization(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="auth-kill"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    leased = service.lease_paper_plan_effect(command_id=claimed.command_id, owner="worker-1")
    session.commit()
    KillSwitchService(session, AuditService(session), service._settings).activate(
        organization_id=ids["organization"].id,
        actor_user_id=ids["user"].id,
        payload=KillSwitchMutationRequest(confirm=True, reason="stop before POST"),
    )
    session.commit()
    authorized = service.authorize_paper_plan_dispatch(
        command_id=claimed.command_id,
        owner="worker-1",
        fencing_token=int(leased.fencing_token),
    )
    session.commit()
    assert authorized.state is VenueSubmitEffectState.PROVEN_UNSENT
    projection = session.scalar(
        select(ExecutionProjection).where(
            ExecutionProjection.receipt_id == claimed.receipt.receipt_id
        )
    )
    assert projection is not None
    assert projection.state is ExecutionReceiptState.BLOCKED_BEFORE_DISPATCH


def test_crash_before_claim_commit_leaves_key_unused(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    session.commit()
    service = execution_service(session)
    service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="crash-before-commit"),
        clock=lambda: EXECUTE_AT,
    )
    session.rollback()
    assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 0
    assert session.scalar(select(func.count()).select_from(ExecutionIdempotencyBinding)) == 0
    auth = session.get(ApprovalAuthorization, authorization.authorization_id)
    assert auth is not None
    assert auth.state is AuthorizationState.AVAILABLE


def test_crash_after_dispatch_authorization_does_not_blind_resubmit(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="crash-after-auth"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    leased = service.lease_paper_plan_effect(command_id=claimed.command_id, owner="worker-1")
    session.commit()
    service.authorize_paper_plan_dispatch(
        command_id=claimed.command_id,
        owner="worker-1",
        fencing_token=int(leased.fencing_token),
    )
    session.commit()
    provider = FakeVenueSubmitProvider()
    recovered = service.recover_paper_plan_effect(command_id=claimed.command_id, owner="worker-1")
    session.commit()
    assert recovered.uncertainty is True
    assert recovered.state is VenueSubmitEffectState.SEND_AMBIGUOUS
    assert provider.submit_count == 0
    reservation = session.scalar(
        select(RiskReservation).where(RiskReservation.command_id == claimed.command_id)
    )
    assert reservation is not None
    assert reservation.release_state is RiskReservationReleaseState.CHARGED


def test_ambiguous_send_preserves_reservation(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="ambiguous"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    leased = service.lease_paper_plan_effect(command_id=claimed.command_id, owner="worker-1")
    session.commit()
    service.authorize_paper_plan_dispatch(
        command_id=claimed.command_id,
        owner=leased.lease_owner or "worker-1",
        fencing_token=int(leased.fencing_token),
    )
    session.commit()
    provider = FakeVenueSubmitProvider(behavior=FakeVenueBehavior.AMBIGUOUS)
    sent = service.attempt_fake_paper_plan_send(
        command_id=claimed.command_id,
        owner="worker-1",
        fencing_token=int(leased.fencing_token),
        provider=provider,
    )
    session.commit()
    assert sent.uncertainty is True
    reservation = session.scalar(
        select(RiskReservation).where(RiskReservation.command_id == claimed.command_id)
    )
    assert reservation is not None
    assert reservation.release_state is RiskReservationReleaseState.CHARGED


def test_unique_fill_converts_reservation_and_appends_transition(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="fill-key"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    service.apply_paper_plan_fill(
        command_id=claimed.command_id,
        fill_quantity=Decimal("1"),
        fill_price=Decimal("100.10"),
        source_identity="fill-1",
        occurred_at=EXECUTE_AT,
    )
    session.commit()
    projection = session.scalar(
        select(ExecutionProjection).where(
            ExecutionProjection.receipt_id == claimed.receipt.receipt_id
        )
    )
    assert projection is not None
    assert projection.state is ExecutionReceiptState.PARTIALLY_FILLED
    assert projection.filled_quantity == Decimal("1")
    assert projection.weighted_price == Decimal("100.10")
    receipt = session.get(ExecutionReceipt, claimed.receipt.receipt_id)
    assert receipt is not None
    transitions = list(
        session.scalars(
            select(ExecutionTransition)
            .where(ExecutionTransition.receipt_id == claimed.receipt.receipt_id)
            .order_by(ExecutionTransition.sequence)
        )
    )
    rebuilt = rebuild_projection_from_transitions(
        receipt,
        transitions,
        initial_remaining=Decimal("2"),
        quantity_unit="CONTRACTS",
    )
    assert rebuilt["state"] is ExecutionReceiptState.PARTIALLY_FILLED
    assert rebuilt["filled_quantity"] == Decimal("1")
    assert rebuilt["weighted_price"] == Decimal("100.10")
    reservation = session.scalar(
        select(RiskReservation).where(RiskReservation.command_id == claimed.command_id)
    )
    assert reservation is not None
    assert reservation.release_state is RiskReservationReleaseState.PARTIALLY_RELEASED
    assert reservation.remaining_reserved_notional < reservation.pending_order_exposure
    accounting = session.scalar(
        select(AccountRiskAccountingState).where(
            AccountRiskAccountingState.account_id == claimed.receipt.account_id
        )
    )
    assert accounting is not None
    assert accounting.actual_notional > 0
    assert Decimal(str((accounting.symbol_actual or {}).get(reservation.instrument, "0"))) > 0
    facts = list(
        session.scalars(select(ExecutionFillFact).where(ExecutionFillFact.receipt_id == receipt.id))
    )
    assert len(facts) == 1


def test_readonly_cannot_claim(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    decision = IntentDecision(
        intent=Intent.MARKET_ANALYSIS,
        operation_class=OperationClass.READ_ONLY,
        organization_id=ids["organization"].id,
        requested_action=RequestedAction.NONE,
    )
    with operation_scope(decision), pytest.raises(PersistencePolicyError):
        service.execute_paper_plan(
            execute_request(ids, plan, authorization, key="readonly-key"),
            clock=lambda: EXECUTE_AT,
        )


def test_stale_worker_cannot_dispatch_after_lease_loss(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="fence-key"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    first = service.lease_paper_plan_effect(
        command_id=claimed.command_id, owner="worker-old", lease_seconds=0
    )
    first_token = int(first.fencing_token)
    session.commit()
    second = service.lease_paper_plan_effect(command_id=claimed.command_id, owner="worker-new")
    session.commit()
    with pytest.raises(ConflictError):
        service.authorize_paper_plan_dispatch(
            command_id=claimed.command_id,
            owner="worker-old",
            fencing_token=first_token,
        )
    authorized = service.authorize_paper_plan_dispatch(
        command_id=claimed.command_id,
        owner="worker-new",
        fencing_token=int(second.fencing_token),
    )
    assert authorized.state is VenueSubmitEffectState.DISPATCH_AUTHORIZED
    assert int(second.fencing_token) > first_token


def test_uncommitted_barrier3_cannot_invoke_provider(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="uncommitted-b3"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    leased = service.lease_paper_plan_effect(command_id=claimed.command_id, owner="worker-1")
    session.commit()
    effect = session.scalar(
        select(VenueSubmitEffect).where(VenueSubmitEffect.command_id == claimed.command_id)
    )
    assert effect is not None
    effect.state = VenueSubmitEffectState.DISPATCH_AUTHORIZED
    effect.dispatch_fencing_token = int(leased.fencing_token)
    effect.lease_owner = "worker-1"
    session.flush()
    provider = FakeVenueSubmitProvider()
    with pytest.raises(ConflictError) as exc:
        service.attempt_fake_paper_plan_send(
            command_id=claimed.command_id,
            owner="worker-1",
            fencing_token=int(leased.fencing_token),
            provider=provider,
        )
    assert exc.value.details["reason"] in {
        "send_blocked_open_database_transaction",
        "send_before_dispatch_authorization",
    }
    assert provider.submit_count == 0


def test_provider_send_has_no_active_database_transaction(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="idle-send"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    leased = service.lease_paper_plan_effect(command_id=claimed.command_id, owner="worker-1")
    session.commit()
    authorized = service.authorize_paper_plan_dispatch(
        command_id=claimed.command_id,
        owner="worker-1",
        fencing_token=int(leased.fencing_token),
    )
    assert authorized.state is VenueSubmitEffectState.DISPATCH_AUTHORIZED
    command_id = claimed.command_id
    receipt_id = claimed.receipt.receipt_id
    effect_id = authorized.id
    client_order_id = authorized.client_order_id
    seen: dict[str, bool | None] = {"in_txn": None}

    def _capture() -> None:
        seen["in_txn"] = session.in_transaction()

    provider = FakeVenueSubmitProvider(behavior=FakeVenueBehavior.ACCEPT, on_submit=_capture)
    sent = service.attempt_fake_paper_plan_send(
        command_id=claimed.command_id,
        owner="worker-1",
        fencing_token=int(leased.fencing_token),
        provider=provider,
    )
    session.commit()
    assert seen["in_txn"] is False
    assert sent.command_id == command_id
    assert sent.id == effect_id
    assert sent.client_order_id == client_order_id
    receipt = session.get(ExecutionReceipt, receipt_id)
    assert receipt is not None
    assert receipt.id == receipt_id
    assert receipt.command_id == command_id


def test_stale_fencing_token_cannot_submit(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="stale-send"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    first = service.lease_paper_plan_effect(
        command_id=claimed.command_id, owner="worker-old", lease_seconds=0
    )
    first_token = int(first.fencing_token)
    session.commit()
    second = service.lease_paper_plan_effect(command_id=claimed.command_id, owner="worker-new")
    session.commit()
    service.authorize_paper_plan_dispatch(
        command_id=claimed.command_id,
        owner="worker-new",
        fencing_token=int(second.fencing_token),
    )
    provider = FakeVenueSubmitProvider()
    with pytest.raises(ConflictError) as exc:
        service.attempt_fake_paper_plan_send(
            command_id=claimed.command_id,
            owner="worker-old",
            fencing_token=first_token,
            provider=provider,
        )
    assert exc.value.details["reason"] == "send_fence_mismatch"
    assert provider.submit_count == 0


def test_kill_before_barrier3_produces_zero_sends(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="kill-zero-send"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    leased = service.lease_paper_plan_effect(command_id=claimed.command_id, owner="worker-1")
    session.commit()
    KillSwitchService(session, AuditService(session), service._settings).activate(
        organization_id=ids["organization"].id,
        actor_user_id=ids["user"].id,
        payload=KillSwitchMutationRequest(confirm=True, reason="block barrier 3"),
    )
    session.commit()
    blocked = service.authorize_paper_plan_dispatch(
        command_id=claimed.command_id,
        owner="worker-1",
        fencing_token=int(leased.fencing_token),
    )
    assert blocked.state is VenueSubmitEffectState.PROVEN_UNSENT
    provider = FakeVenueSubmitProvider()
    with pytest.raises(ConflictError):
        service.attempt_fake_paper_plan_send(
            command_id=claimed.command_id,
            owner="worker-1",
            fencing_token=int(leased.fencing_token),
            provider=provider,
        )
    assert provider.submit_count == 0


def test_multiple_fills_weighted_price_and_replay(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="multi-fill"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    occurred = EXECUTE_AT
    first = service.apply_paper_plan_fill(
        command_id=claimed.command_id,
        fill_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        source_identity="fill-a",
        occurred_at=occurred,
    )
    session.commit()
    second = service.apply_paper_plan_fill(
        command_id=claimed.command_id,
        fill_quantity=Decimal("1"),
        fill_price=Decimal("110"),
        source_identity="fill-b",
        occurred_at=occurred,
    )
    session.commit()
    assert first.replayed is False
    assert second.replayed is False
    assert second.weighted_price == Decimal("105")
    projection = session.scalar(
        select(ExecutionProjection).where(
            ExecutionProjection.receipt_id == claimed.receipt.receipt_id
        )
    )
    assert projection is not None
    version_before = int(projection.version)
    filled_before = projection.filled_quantity
    remaining_before = projection.remaining_quantity
    weighted_before = projection.weighted_price
    reservation = session.scalar(
        select(RiskReservation).where(RiskReservation.command_id == claimed.command_id)
    )
    assert reservation is not None
    remaining_reserved_before = reservation.remaining_reserved_notional
    accounting = session.scalar(
        select(AccountRiskAccountingState).where(
            AccountRiskAccountingState.account_id == claimed.receipt.account_id
        )
    )
    assert accounting is not None
    actual_before = accounting.actual_notional
    reserved_before = accounting.reserved_notional
    replay = service.apply_paper_plan_fill(
        command_id=claimed.command_id,
        fill_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        source_identity="fill-a",
        occurred_at=occurred,
    )
    session.commit()
    assert replay.replayed is True
    session.refresh(projection)
    session.refresh(reservation)
    session.refresh(accounting)
    assert int(projection.version) == version_before
    assert projection.filled_quantity == filled_before
    assert projection.remaining_quantity == remaining_before
    assert projection.weighted_price == weighted_before
    assert reservation.remaining_reserved_notional == remaining_reserved_before
    assert accounting.actual_notional == actual_before
    assert accounting.reserved_notional == reserved_before
    assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 2


def test_conflicting_fill_identity_fails_closed(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="conflict-fill"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    service.apply_paper_plan_fill(
        command_id=claimed.command_id,
        fill_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        source_identity="fill-same",
        occurred_at=EXECUTE_AT,
    )
    session.commit()
    with pytest.raises(ConflictError) as exc:
        service.apply_paper_plan_fill(
            command_id=claimed.command_id,
            fill_quantity=Decimal("1"),
            fill_price=Decimal("999"),
            source_identity="fill-same",
            occurred_at=EXECUTE_AT,
        )
    assert exc.value.details["reason"] == "conflicting_fill_identity"


def _legacy_paper_request(
    ids: dict[str, object],
    *,
    approval_id: object,
    key: str = "legacy-bypass",
) -> PaperOrderRequest:
    return PaperOrderRequest(
        proposal_id=ids["proposal_id"],  # type: ignore[arg-type]
        approval_id=approval_id,  # type: ignore[arg-type]
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        size=Decimal("0.005"),
        idempotency_key=key,
    )


def test_legacy_place_paper_order_cannot_create_order_or_position(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    with pytest.raises(TradingPolicyError) as exc:
        service.place_paper_order(
            _legacy_paper_request(ids, approval_id=authorization.authorization_id)
        )
    assert exc.value.details["reason"] == LEGACY_PAPER_EXECUTION_REASON
    session.commit()
    assert session.scalar(select(func.count()).select_from(Order)) == 0
    assert session.scalar(select(func.count()).select_from(Position)) == 0
    assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 0
    assert session.scalar(select(func.count()).select_from(PlanEntryExecutionClaim)) == 0
    del plan


def test_legacy_path_cannot_call_demo_provider(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    provider = FakeVenueSubmitProvider()
    service = execution_service(session)
    service._exchange_execution = provider  # type: ignore[assignment]
    with pytest.raises(TradingPolicyError) as exc:
        service.place_paper_order(
            _legacy_paper_request(ids, approval_id=authorization.authorization_id, key="no-demo-1")
        )
    assert exc.value.details["reason"] == LEGACY_PAPER_EXECUTION_REASON
    assert provider.submit_count == 0
    assert service._demo_routing_enabled() is False
    with pytest.raises(TradingPolicyError) as mirror_exc:
        service._mirror_to_demo_venue(
            request=_legacy_paper_request(
                ids, approval_id=authorization.authorization_id, key="mirror-x"
            ),
            order=None,  # type: ignore[arg-type]
            bound=None,  # type: ignore[arg-type]
        )
    assert mirror_exc.value.details["reason"] == LEGACY_DEMO_MIRROR_REASON
    del plan


def test_legacy_approval_request_cannot_substitute_for_authorization(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    legacy = ApprovalRequest(
        proposal_id=ids["proposal_id"],
        plan_revision_id=None,
        organization_id=ids["organization"].id,
        user_id=ids["user"].id,
        status=ApprovalStatus.APPROVED,
        risk_level=RiskSeverity.MEDIUM,
        confidence=0.7,
    )
    session.add(legacy)
    session.flush()
    service = execution_service(session)
    with pytest.raises((NotFoundError, TradingPolicyError, ConflictError)):
        service.execute_paper_plan(
            execute_request(ids, plan, authorization, key="legacy-auth-swap").model_copy(
                update={"authorization_id": legacy.id}
            ),
            clock=lambda: EXECUTE_AT,
        )
    with pytest.raises(TradingPolicyError) as exc:
        service.place_paper_order(
            _legacy_paper_request(ids, approval_id=legacy.id, key="legacy-ar")
        )
    assert exc.value.details["reason"] == LEGACY_PAPER_EXECUTION_REASON
    assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 0
    assert session.scalar(select(func.count()).select_from(PlanEntryExecutionClaim)) == 0


def test_only_execute_paper_plan_reaches_claim_boundary(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    with pytest.raises(TradingPolicyError):
        service.place_paper_order(
            _legacy_paper_request(ids, approval_id=authorization.authorization_id, key="not-claim")
        )
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="real-claim"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    assert claimed.replayed is False
    assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 1
    assert session.scalar(select(func.count()).select_from(PlanEntryExecutionClaim)) == 1


def test_global_kill_switch_after_restart_blocks_lease_send(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    first = execution_service(session)
    claimed = first.execute_paper_plan(
        execute_request(ids, plan, authorization, key="gks-lease"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    restarted = execution_service(session, global_kill_switch_active=True)
    blocked = restarted.lease_paper_plan_effect(command_id=claimed.command_id, owner="worker-1")
    session.commit()
    assert blocked.state is VenueSubmitEffectState.PROVEN_UNSENT
    provider = FakeVenueSubmitProvider()
    with pytest.raises(ConflictError):
        restarted.attempt_fake_paper_plan_send(
            command_id=claimed.command_id,
            owner="worker-1",
            fencing_token=int(blocked.fencing_token),
            provider=provider,
        )
    assert provider.submit_count == 0
    reservation = session.scalar(
        select(RiskReservation).where(RiskReservation.command_id == claimed.command_id)
    )
    assert reservation is not None
    assert reservation.release_state is RiskReservationReleaseState.RELEASED


def test_global_kill_switch_after_lease_blocks_barrier3(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    first = execution_service(session)
    claimed = first.execute_paper_plan(
        execute_request(ids, plan, authorization, key="gks-b3"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    leased = first.lease_paper_plan_effect(command_id=claimed.command_id, owner="worker-1")
    session.commit()
    token = int(leased.fencing_token)
    restarted = execution_service(session, global_kill_switch_active=True)
    blocked = restarted.authorize_paper_plan_dispatch(
        command_id=claimed.command_id,
        owner="worker-1",
        fencing_token=token,
    )
    assert blocked.state is VenueSubmitEffectState.PROVEN_UNSENT
    provider = FakeVenueSubmitProvider()
    with pytest.raises(ConflictError):
        restarted.attempt_fake_paper_plan_send(
            command_id=claimed.command_id,
            owner="worker-1",
            fencing_token=token,
            provider=provider,
        )
    assert provider.submit_count == 0


def test_linear_contract_multiplier_fill_exposure(session: Session) -> None:
    rules = {
        "contract_multiplier": "10",
        "contract_type": "LINEAR",
        "base_currency": "BTC",
        "quote_currency": "USDT",
        "settlement_currency": "USDT",
        "tick_size": "0.10",
        "lot_size": "1",
        "minimum_quantity": "1",
        "minimum_notional": "5",
        "rules_version": "blofin-rules-2026-09-15",
    }
    ids, plan, authorization = prepared_authorized_plan(session, instrument_rules=rules)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="mult-fill"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    reservation = session.scalar(
        select(RiskReservation).where(RiskReservation.command_id == claimed.command_id)
    )
    accounting = session.scalar(
        select(AccountRiskAccountingState).where(
            AccountRiskAccountingState.account_id == claimed.receipt.account_id
        )
    )
    assert reservation is not None
    assert accounting is not None
    assert reservation.contract_multiplier == Decimal("10")
    reserved_before = reservation.remaining_reserved_notional
    expected_reserved = (
        Decimal("2") * Decimal("100.10") * Decimal("10") + Decimal("1") + Decimal("0.75")
    )
    assert reserved_before == expected_reserved
    assert reserved_before == accounting.reserved_notional
    fill = service.apply_paper_plan_fill(
        command_id=claimed.command_id,
        fill_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        source_identity="mult-fill-1",
        occurred_at=EXECUTE_AT,
    )
    session.commit()
    session.refresh(reservation)
    session.refresh(accounting)
    fill_notional = Decimal("1000")
    assert fill.weighted_price == Decimal("100")
    assert reservation.remaining_reserved_notional == reserved_before - fill_notional
    assert accounting.actual_notional == fill_notional
    assert accounting.reserved_notional == reservation.remaining_reserved_notional
    assert Decimal(str((accounting.symbol_actual or {}).get(reservation.instrument, "0"))) == (
        fill_notional
    )
    assert Decimal(str((accounting.symbol_reserved or {}).get(reservation.instrument, "0"))) == (
        reservation.remaining_reserved_notional
    )


def test_linear_base_quantity_ignores_contract_multiplier(session: Session) -> None:
    rules = {
        "contract_multiplier": "10",
        "contract_type": "LINEAR",
        "base_currency": "BTC",
        "quote_currency": "USDT",
        "settlement_currency": "USDT",
        "tick_size": "0.10",
        "lot_size": "1",
        "minimum_quantity": "1",
        "minimum_notional": "5",
        "rules_version": "blofin-rules-2026-09-15",
    }
    ids, plan, authorization = prepared_authorized_plan(
        session,
        quantity={"value": "2.000", "unit": "BASE"},
        quantity_unit="BASE",
        instrument_rules=rules,
    )
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="base-mult-fill"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    reservation = session.scalar(
        select(RiskReservation).where(RiskReservation.command_id == claimed.command_id)
    )
    accounting = session.scalar(
        select(AccountRiskAccountingState).where(
            AccountRiskAccountingState.account_id == claimed.receipt.account_id
        )
    )
    assert reservation is not None
    assert accounting is not None
    assert reservation.quantity_unit == "BASE"
    assert reservation.contract_multiplier == Decimal("10")
    quote_exposure = Decimal("2") * Decimal("100.10")
    expected_reserved = quote_exposure + Decimal("1") + Decimal("0.75")
    assert quote_exposure == Decimal("200.20")
    assert expected_reserved == Decimal("201.95")
    assert expected_reserved != Decimal("2") * Decimal("100.10") * Decimal("10") + Decimal(
        "1"
    ) + Decimal("0.75")
    assert reservation.remaining_reserved_notional == expected_reserved
    assert accounting.reserved_notional == expected_reserved
    assert Decimal(str((accounting.symbol_reserved or {}).get(reservation.instrument, "0"))) == (
        expected_reserved
    )
    fill = service.apply_paper_plan_fill(
        command_id=claimed.command_id,
        fill_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        source_identity="base-fill-1",
        occurred_at=EXECUTE_AT,
    )
    session.commit()
    session.refresh(reservation)
    session.refresh(accounting)
    fill_notional = Decimal("1") * Decimal("100")
    assert fill.replayed is False
    assert fill_notional == Decimal("100")
    assert fill_notional != Decimal("1") * Decimal("100") * Decimal("10")
    assert reservation.remaining_reserved_notional == expected_reserved - fill_notional
    assert accounting.actual_notional == fill_notional
    assert accounting.reserved_notional == reservation.remaining_reserved_notional
    assert Decimal(str((accounting.symbol_actual or {}).get(reservation.instrument, "0"))) == (
        fill_notional
    )
    assert Decimal(str((accounting.symbol_reserved or {}).get(reservation.instrument, "0"))) == (
        reservation.remaining_reserved_notional
    )
    remaining_before_replay = reservation.remaining_reserved_notional
    actual_before_replay = accounting.actual_notional
    symbol_reserved_before = Decimal(
        str((accounting.symbol_reserved or {}).get(reservation.instrument, "0"))
    )
    symbol_actual_before = Decimal(
        str((accounting.symbol_actual or {}).get(reservation.instrument, "0"))
    )
    replay = service.apply_paper_plan_fill(
        command_id=claimed.command_id,
        fill_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        source_identity="base-fill-1",
        occurred_at=EXECUTE_AT,
    )
    session.commit()
    session.refresh(reservation)
    session.refresh(accounting)
    assert replay.replayed is True
    assert reservation.remaining_reserved_notional == remaining_before_replay
    assert accounting.actual_notional == actual_before_replay
    assert Decimal(str((accounting.symbol_reserved or {}).get(reservation.instrument, "0"))) == (
        symbol_reserved_before
    )
    assert Decimal(str((accounting.symbol_actual or {}).get(reservation.instrument, "0"))) == (
        symbol_actual_before
    )


def _inverse_rules() -> dict[str, str]:
    return {
        "contract_multiplier": "1",
        "contract_type": "INVERSE",
        "base_currency": "BTC",
        "quote_currency": "USD",
        "settlement_currency": "BTC",
        "tick_size": "0.10",
        "lot_size": "1",
        "minimum_quantity": "1",
        "minimum_notional": "5",
        "rules_version": "blofin-rules-2026-09-15",
    }


def test_inverse_contract_fails_closed_at_authorization(session: Session) -> None:
    ids = seed_support(session)
    plan = persist_plan(session, ids, plan_request(ids, instrument_rules=_inverse_rules()))
    with pytest.raises(TradingPolicyError, match="linear instruments only") as exc:
        approve_plan(session, ids, plan)
    assert exc.value.details["reason"] == "planned_reward_risk_invalid"
    session.flush()
    assert session.scalar(select(func.count()).select_from(ApprovalAuthorization)) == 0
    assert session.scalars(select(ApprovalRequest)).one().status is ApprovalStatus.PENDING
    for model in (
        ExecutionCommand,
        PlanEntryExecutionClaim,
        RiskReservation,
        VenueSubmitEffect,
        ExecutionFillFact,
        Order,
        Position,
    ):
        assert session.scalar(select(func.count()).select_from(model)) == 0


def test_inverse_contract_fails_closed_at_claim(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reproduce a historical authorization issued before the linear gross1R gate.
    # Restore current policy before execution; no immutable plan or auth is altered.
    with monkeypatch.context() as historical:
        historical.setattr(
            "app.services.planned_reward_risk.planned_reward_risk", lambda _terms: None
        )
        ids, plan, authorization = prepared_authorized_plan(
            session, instrument_rules=_inverse_rules()
        )
    service = execution_service(session)
    with pytest.raises(TradingPolicyError) as exc:
        service.execute_paper_plan(
            execute_request(ids, plan, authorization, key="inverse-block"),
            clock=lambda: EXECUTE_AT,
        )
    assert exc.value.details["reason"] == "inverse_contract_exposure_undefined"
    assert session.get(ApprovalAuthorization, authorization.authorization_id).state is (
        AuthorizationState.AVAILABLE
    )
    for model in (
        ExecutionCommand,
        PlanEntryExecutionClaim,
        RiskReservation,
        VenueSubmitEffect,
        ExecutionFillFact,
        Order,
        Position,
    ):
        assert session.scalar(select(func.count()).select_from(model)) == 0


def test_missing_and_empty_source_fill_identity_rejected(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="fill-id-req"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    with pytest.raises(ConflictError) as missing:
        service.apply_paper_plan_fill(
            command_id=claimed.command_id,
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100"),
            source_identity=None,  # type: ignore[arg-type]
            occurred_at=EXECUTE_AT,
        )
    assert missing.value.details["reason"] == "missing_source_fill_identity"
    with pytest.raises(ConflictError) as empty:
        service.apply_paper_plan_fill(
            command_id=claimed.command_id,
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100"),
            source_identity="",
            occurred_at=EXECUTE_AT,
        )
    assert empty.value.details["reason"] == "missing_source_fill_identity"
    with pytest.raises(ConflictError) as blank:
        service.apply_paper_plan_fill(
            command_id=claimed.command_id,
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100"),
            source_identity="   ",
            occurred_at=EXECUTE_AT,
        )
    assert blank.value.details["reason"] == "missing_source_fill_identity"
    assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 0


def test_missing_authoritative_occurred_at_rejected(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="fill-time-req"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    with pytest.raises(ConflictError) as exc:
        service.apply_paper_plan_fill(
            command_id=claimed.command_id,
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100"),
            source_identity="timed-fill",
            occurred_at=None,  # type: ignore[arg-type]
        )
    assert exc.value.details["reason"] == "missing_authoritative_occurred_at"
    assert session.scalar(select(func.count()).select_from(ExecutionFillFact)) == 0


def test_identical_fill_replay_and_conflicting_replay(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    claimed = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="fill-replay-id"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    first = service.apply_paper_plan_fill(
        command_id=claimed.command_id,
        fill_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        source_identity="venue-fill-stable",
        occurred_at=EXECUTE_AT,
    )
    session.commit()
    replay = service.apply_paper_plan_fill(
        command_id=claimed.command_id,
        fill_quantity=Decimal("1"),
        fill_price=Decimal("100"),
        source_identity="venue-fill-stable",
        occurred_at=EXECUTE_AT,
    )
    session.commit()
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.filled_quantity == first.filled_quantity
    with pytest.raises(ConflictError) as exc:
        service.apply_paper_plan_fill(
            command_id=claimed.command_id,
            fill_quantity=Decimal("1"),
            fill_price=Decimal("101"),
            source_identity="venue-fill-stable",
            occurred_at=EXECUTE_AT,
        )
    assert exc.value.details["reason"] == "conflicting_fill_identity"
