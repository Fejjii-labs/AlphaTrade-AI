"""Adversarial canonical PAPER execution: lineage, safety, idempotency, journal."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import NotFoundError, TradingPolicyError
from app.db.canonical_trade_plans import PLAN_ROOT_CANONICAL
from app.db.models import (
    AccountRiskAccountingState,
    ApprovalAuthorization,
    ExecutionAccount,
    ExecutionCommand,
    JournalLifecycleEvent,
    JournalTrade,
)
from app.db.models import (
    TradePlanRevision as TradePlanRevisionModel,
)
from app.db.models import (
    TradeProposal as TradeProposalModel,
)
from app.schemas.common import JournalLifecycleEventType
from app.schemas.execution_protocol import ExecutionCommandOutcome
from app.schemas.risk import KillSwitchMutationRequest
from app.schemas.trade_plan import AccountMode, AuthorizationState, ExecutionMode
from app.services.audit_service import AuditService
from app.services.canonical_execution_journal import CANONICAL_EXECUTION_SOURCE_SYSTEM
from app.services.execution_claim import conservative_reservation
from app.services.proposal_service import ProposalService
from app.services.risk.kill_switch import KillSwitchService
from app.signal_fusion.enums import CandidateReasonCode, CandidateState
from tests.support.phase6_fusion import ORG_ID, USER_ID
from tests.support.phase8_runtime import (
    AFTER_AUTH_EXPIRY,
    AFTER_PLAN_EXPIRY,
    AUTH_EXPIRES_AT,
    EXECUTE_AT,
    build_runtime,
    canonical_execute_request,
    canonical_execution_service,
    phase8_settings,
    prepared_authorized_canonical,
)
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres


def _factory() -> sessionmaker[Session]:
    return phase7_plan_session_factory()


def _execute(
    factory: sessionmaker[Session],
    envelope: object,
    authorization: ApprovalAuthorization,
    *,
    key: str,
    clock: object = EXECUTE_AT,
    runtime: object | None = None,
    request: object | None = None,
):
    resolved = runtime or build_runtime(factory)
    session = factory()
    try:
        service = canonical_execution_service(session, resolved)  # type: ignore[arg-type]
        result = service.execute_paper_plan(
            request  # type: ignore[arg-type]
            or canonical_execute_request(envelope, authorization, key=key),  # type: ignore[arg-type]
            clock=lambda: clock,  # type: ignore[arg-type, return-value]
        )
        session.commit()
        return result, session, service, resolved
    except Exception:
        session.rollback()
        session.close()
        raise


@requires_postgres
def test_canonical_paper_execution_allow_and_journal() -> None:
    factory = _factory()
    _world, envelope, authorization = prepared_authorized_canonical(factory)
    result, session, service, _runtime = _execute(
        factory, envelope, authorization, key="canonical-allow-1"
    )
    try:
        assert result.replayed is False
        assert result.outcome is ExecutionCommandOutcome.ALLOW
        assert result.effect is not None
        auth = session.get(ApprovalAuthorization, authorization.authorization_id)
        assert auth is not None
        assert auth.state is AuthorizationState.CONSUMED
        events = session.scalars(
            select(JournalLifecycleEvent).where(
                JournalLifecycleEvent.source_system == CANONICAL_EXECUTION_SOURCE_SYSTEM
            )
        ).all()
        assert len(events) == 1
        assert events[0].event_type is JournalLifecycleEventType.APPROVED_PLAN
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.execution_lifecycle_id == result.command_id
        assert trade.symbol == envelope.plan.execution_instrument
        lineage = events[0].payload["lineage"]
        assert lineage["candidate_id"] == str(envelope.lineage.candidate_id)
        assert lineage["trade_plan_revision_id"] == str(envelope.plan.revision_id)
        fill = service.apply_paper_plan_fill(
            command_id=result.command_id,
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100000"),
            source_identity="canonical-fill-1",
            occurred_at=EXECUTE_AT,
        )
        session.commit()
        assert fill.replayed is False
        fill_events = session.scalars(
            select(JournalLifecycleEvent).where(
                JournalLifecycleEvent.event_type == JournalLifecycleEventType.FILL
            )
        ).all()
        assert len(fill_events) == 1
        assert fill_events[0].source_system == CANONICAL_EXECUTION_SOURCE_SYSTEM
    finally:
        session.close()


@requires_postgres
def test_duplicate_and_restarted_runtime_converge() -> None:
    factory = _factory()
    _world, envelope, authorization = prepared_authorized_canonical(factory)
    first, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="canonical-idem-1"
    )
    session.close()
    second, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="canonical-idem-1"
    )
    try:
        assert second.replayed is True
        assert second.command_id == first.command_id
        assert second.canonical_payload_hash == first.canonical_payload_hash
        assert session.scalar(select(func.count()).select_from(ExecutionCommand)) == 1
        events = session.scalars(select(JournalLifecycleEvent)).all()
        assert len(events) == 1
        restarted = build_runtime(factory)
        third, session2, _service2, _runtime2 = _execute(
            factory,
            envelope,
            authorization,
            key="canonical-idem-1",
            runtime=restarted,
        )
        try:
            assert third.replayed is True
            assert third.command_id == first.command_id
        finally:
            session2.close()
    finally:
        session.close()


@requires_postgres
def test_kill_switch_blocks_without_consuming_authorization() -> None:
    factory = _factory()
    _world, envelope, authorization = prepared_authorized_canonical(factory)
    session = factory()
    try:
        KillSwitchService(session, AuditService(session), phase8_settings()).activate(
            organization_id=envelope.plan.organization_id,
            actor_user_id=envelope.plan.user_id,
            payload=KillSwitchMutationRequest(confirm=True, reason="halt canonical"),
        )
        session.commit()
    finally:
        session.close()
    result, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="canonical-killed"
    )
    try:
        assert result.outcome is ExecutionCommandOutcome.BLOCKED
        assert result.blocked_reason_code == "safety_epoch_blocking"
        auth = session.get(ApprovalAuthorization, authorization.authorization_id)
        assert auth is not None
        assert auth.state is AuthorizationState.AVAILABLE
        assert session.scalar(select(func.count()).select_from(JournalLifecycleEvent)) == 0
    finally:
        session.close()


@requires_postgres
def test_risk_capacity_blocks_canonical_execution() -> None:
    factory = _factory()
    _world, envelope, authorization = prepared_authorized_canonical(factory)
    intent = conservative_reservation(envelope.plan)
    session = factory()
    try:
        row = session.scalar(
            select(AccountRiskAccountingState).where(
                AccountRiskAccountingState.account_id == envelope.plan.account_id
            )
        )
        assert row is not None
        row.max_notional = intent.pending_notional / Decimal("2")
        row.max_symbol_notional = intent.pending_notional
        session.commit()
    finally:
        session.close()
    result, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="canonical-risk"
    )
    try:
        assert result.outcome is ExecutionCommandOutcome.BLOCKED
        assert result.blocked_reason_code == "insufficient_total_exposure"
        auth = session.get(ApprovalAuthorization, authorization.authorization_id)
        assert auth is not None
        assert auth.state is AuthorizationState.AVAILABLE
    finally:
        session.close()


@requires_postgres
def test_rejected_candidate_blocks_execution() -> None:
    factory = _factory()
    world, envelope, authorization = prepared_authorized_canonical(factory)
    world.lifecycle.transition(
        organization_id=ORG_ID,
        candidate_id=world.candidate.candidate_id,
        new_state=CandidateState.REJECTED,
        reason_codes=(CandidateReasonCode.REJECTED,),
        idempotency_key="reject-after-plan",
        correlation_id=world.candidate.correlation_id,
    )
    result, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="canonical-rejected"
    )
    try:
        assert result.outcome is ExecutionCommandOutcome.BLOCKED
        assert result.blocked_reason_code == "candidate_rejected"
        auth = session.get(ApprovalAuthorization, authorization.authorization_id)
        assert auth is not None
        assert auth.state is AuthorizationState.AVAILABLE
    finally:
        session.close()


@requires_postgres
def test_stale_plan_and_expired_authorization_fail_closed() -> None:
    factory = _factory()
    _world, envelope, authorization = prepared_authorized_canonical(factory)
    stale, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="canonical-stale", clock=AFTER_PLAN_EXPIRY
    )
    try:
        assert stale.outcome is ExecutionCommandOutcome.BLOCKED
        assert stale.blocked_reason_code in {"plan_validity_window", "authorization_expired"}
    finally:
        session.close()

    factory = _factory()
    _world, envelope, authorization = prepared_authorized_canonical(
        factory, authorization_expires_at=AUTH_EXPIRES_AT
    )
    expired, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="canonical-auth-expired", clock=AFTER_AUTH_EXPIRY
    )
    try:
        assert expired.outcome is ExecutionCommandOutcome.BLOCKED
        assert expired.blocked_reason_code == "authorization_expired"
    finally:
        session.close()


@requires_postgres
def test_account_and_tenant_mismatch_fail_closed() -> None:
    factory = _factory()
    _world, envelope, authorization = prepared_authorized_canonical(factory)
    session = factory()
    try:
        other = ExecutionAccount(
            organization_id=ORG_ID,
            user_id=USER_ID,
            name="Other canonical paper account",
            execution_mode=ExecutionMode.PAPER,
            account_mode=AccountMode.NET,
        )
        session.add(other)
        session.commit()
        other_id = other.id
    finally:
        session.close()
    request = canonical_execute_request(envelope, authorization, key="canonical-account")
    mismatched = request.model_copy(update={"account_id": other_id})
    result, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="canonical-account", request=mismatched
    )
    try:
        assert result.outcome is ExecutionCommandOutcome.BLOCKED
        assert result.blocked_reason_code == "account_mismatch"
    finally:
        session.close()

    foreign = request.model_copy(update={"organization_id": uuid4(), "idempotency_key": "tenant"})
    session = factory()
    runtime = build_runtime(factory)
    try:
        with pytest.raises(NotFoundError, match="Trade plan revision not found"):
            canonical_execution_service(session, runtime).execute_paper_plan(
                foreign, clock=lambda: EXECUTE_AT
            )
    finally:
        session.close()


@requires_postgres
def test_modified_plan_is_immutable() -> None:
    factory = _factory()
    _world, envelope, _authorization = prepared_authorized_canonical(factory)
    session = factory()
    try:
        row = session.get(TradePlanRevisionModel, envelope.plan.revision_id)
        assert row is not None
        row.content_hash = "ab" * 32
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
    finally:
        session.rollback()
        session.close()


@requires_postgres
def test_proposal_service_cannot_use_canonical_plan_root() -> None:
    factory = _factory()
    _world, envelope, _authorization = prepared_authorized_canonical(factory)
    session = factory()
    try:
        proposal = session.get(TradeProposalModel, envelope.plan.plan_id)
        assert proposal is not None
        assert proposal.plan_root_kind == PLAN_ROOT_CANONICAL
        service = ProposalService(session, AuditService(session))
        with pytest.raises(TradingPolicyError) as exc:
            service.get(envelope.plan.plan_id, organization_id=envelope.plan.organization_id)
        assert exc.value.details["reason"] == "canonical_plan_root_not_proposal_authority"
        items, total = service.list_proposals(organization_id=ORG_ID)
        assert total == 0
        assert items == []
    finally:
        session.close()


@requires_postgres
def test_canonical_runtime_does_not_enable_watcher_or_telegram() -> None:
    runtime = build_runtime(_factory())
    assert runtime.watcher_enabled is False
    assert runtime.telegram_enabled is False
    assert runtime.flags.real_trading_enabled is False
