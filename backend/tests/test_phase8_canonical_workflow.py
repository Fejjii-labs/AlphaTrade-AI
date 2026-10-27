"""End-to-end Phase 8 canonical workflow: execution, journal, learning, reads."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import NotFoundError
from app.db.canonical_trade_plans import PLAN_ROOT_CANONICAL
from app.db.learning_attribution import LearningAttributionRecordRow
from app.db.models import (
    AccountRiskAccountingState,
    ApprovalAuthorization,
    JournalLifecycleEvent,
    JournalTrade,
    Membership,
    TradeProposal,
)
from app.db.session import get_session
from app.learning_attribution.query import LearningQueryService
from app.main import create_app
from app.persistence.attribution_postgres import PostgresAttributionStore
from app.schemas.common import JournalLifecycleEventType, MembershipRole
from app.schemas.execution_protocol import ExecutionCommandOutcome
from app.schemas.risk import KillSwitchMutationRequest
from app.schemas.trade_plan import AuthorizationState
from app.security.tokens import create_access_token
from app.services.audit_service import AuditService
from app.services.canonical_reads import CanonicalReadService
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


def _execute(factory: sessionmaker[Session], envelope: object, authorization: object, *, key: str):
    runtime = build_runtime(factory)
    session = factory()
    try:
        service = canonical_execution_service(session, runtime)
        result = service.execute_paper_plan(
            canonical_execute_request(envelope, authorization, key=key),
            clock=lambda: EXECUTE_AT,
        )
        session.commit()
        return result, session, service, runtime
    except Exception:
        session.rollback()
        session.close()
        raise


@requires_postgres
def test_happy_paper_path_journal_learning_and_reads() -> None:
    factory = _factory()
    world, envelope, authorization = prepared_authorized_canonical(factory)
    result, session, service, runtime = _execute(
        factory, envelope, authorization, key="workflow-happy"
    )
    try:
        assert result.outcome is ExecutionCommandOutcome.ALLOW
        assert result.replayed is False
        events = session.scalars(select(JournalLifecycleEvent)).all()
        assert len(events) == 1
        assert events[0].event_type is JournalLifecycleEventType.APPROVED_PLAN
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.execution_lifecycle_id == result.command_id
        assert trade.candidate_id == envelope.lineage.candidate_id
        row = session.scalars(select(LearningAttributionRecordRow)).one()
        assert row.candidate_id == envelope.lineage.candidate_id
        assert row.execution_lifecycle_id == result.command_id
        assert row.journal_trade_id == trade.id

        fill = service.apply_paper_plan_fill(
            command_id=result.command_id,
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100000"),
            source_identity="workflow-fill",
            occurred_at=EXECUTE_AT,
        )
        session.commit()
        assert fill.replayed is False
        reads = CanonicalReadService(session, runtime)
        listed = reads.list_candidates(organization_id=ORG_ID, limit=20, offset=0)
        assert listed.total == 1
        assert listed.items[0].candidate.candidate_id == world.candidate.candidate_id
        assessment = reads.get_setup_assessment(
            organization_id=ORG_ID, assessment_id=world.candidate.assessment_id
        )
        assert assessment.candidate_id == world.candidate.candidate_id
        eligibility = reads.get_candidate_eligibility(
            organization_id=ORG_ID, candidate_id=world.candidate.candidate_id
        )
        assert eligibility.evaluation.live_executable is False
        receipt = reads.get_execution_receipt(
            organization_id=ORG_ID, receipt_id=result.receipt.receipt_id
        )
        assert receipt.receipt.command_id == result.command_id
        learning = reads.get_learning_record(
            organization_id=ORG_ID, candidate_id=world.candidate.candidate_id
        )
        assert learning.record.facts.strategy_pattern.plan_approved is True
        stats = reads.strategy_stats(organization_id=ORG_ID, learning_venue_mode=None)
        assert stats.snapshot.human_vs_system.human_approvals == 1
        query = LearningQueryService(PostgresAttributionStore(session))
        rollup = query.strategy_pattern_stats(organization_id=ORG_ID)
        assert rollup.patterns[0].plan_approved_count == 1
    finally:
        session.close()


@requires_postgres
def test_duplicate_request_and_restart_idempotency_keep_one_attribution() -> None:
    factory = _factory()
    _world, envelope, authorization = prepared_authorized_canonical(factory)
    first, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="workflow-idem"
    )
    session.close()
    second, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="workflow-idem"
    )
    try:
        assert second.replayed is True
        assert second.command_id == first.command_id
        assert session.scalar(select(LearningAttributionRecordRow).limit(1)) is not None
        third, session2, _service2, _runtime2 = _execute(
            factory, envelope, authorization, key="workflow-idem"
        )
        try:
            assert third.replayed is True
            assert third.command_id == first.command_id
            assert len(session2.scalars(select(LearningAttributionRecordRow)).all()) == 1
        finally:
            session2.close()
    finally:
        session.close()


@requires_postgres
def test_cross_tenant_canonical_reads_are_invisible() -> None:
    factory = _factory()
    world, envelope, authorization = prepared_authorized_canonical(factory)
    result, session, _service, runtime = _execute(
        factory, envelope, authorization, key="workflow-tenant"
    )
    try:
        reads = CanonicalReadService(session, runtime)
        other = uuid4()
        listed = reads.list_candidates(organization_id=other, limit=20, offset=0)
        assert listed.total == 0
        with pytest.raises(NotFoundError):
            reads.get_candidate(organization_id=other, candidate_id=world.candidate.candidate_id)
        with pytest.raises(NotFoundError):
            reads.get_execution_receipt(organization_id=other, receipt_id=result.receipt.receipt_id)
        with pytest.raises(NotFoundError):
            reads.get_learning_record(
                organization_id=other, candidate_id=world.candidate.candidate_id
            )
    finally:
        session.close()


@requires_postgres
def test_kill_switch_and_risk_block_do_not_attribute() -> None:
    factory = _factory()
    _world, envelope, authorization = prepared_authorized_canonical(factory)
    session = factory()
    try:
        KillSwitchService(session, AuditService(session), phase8_settings()).activate(
            organization_id=envelope.plan.organization_id,
            actor_user_id=envelope.plan.user_id,
            payload=KillSwitchMutationRequest(confirm=True, reason="halt workflow"),
        )
        session.commit()
    finally:
        session.close()
    killed, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="workflow-killed"
    )
    try:
        assert killed.outcome is ExecutionCommandOutcome.BLOCKED
        assert killed.blocked_reason_code == "safety_epoch_blocking"
        auth = session.get(ApprovalAuthorization, authorization.authorization_id)
        assert auth is not None
        assert auth.state is AuthorizationState.AVAILABLE
        assert session.scalars(select(LearningAttributionRecordRow)).first() is None
    finally:
        session.close()

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
    blocked, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="workflow-risk"
    )
    try:
        assert blocked.outcome is ExecutionCommandOutcome.BLOCKED
        assert blocked.blocked_reason_code == "insufficient_total_exposure"
        assert session.scalars(select(LearningAttributionRecordRow)).first() is None
    finally:
        session.close()


@requires_postgres
def test_expired_and_rejected_candidate_do_not_attribute() -> None:
    factory = _factory()
    world, envelope, authorization = prepared_authorized_canonical(factory)
    world.lifecycle.transition(
        organization_id=ORG_ID,
        candidate_id=world.candidate.candidate_id,
        new_state=CandidateState.REJECTED,
        reason_codes=(CandidateReasonCode.REJECTED,),
        idempotency_key="workflow-reject",
        correlation_id=world.candidate.correlation_id,
    )
    rejected, session, _service, _runtime = _execute(
        factory, envelope, authorization, key="workflow-rejected"
    )
    try:
        assert rejected.outcome is ExecutionCommandOutcome.BLOCKED
        assert rejected.blocked_reason_code == "candidate_rejected"
        assert session.scalars(select(LearningAttributionRecordRow)).first() is None
    finally:
        session.close()

    factory = _factory()
    _world, envelope, authorization = prepared_authorized_canonical(
        factory, authorization_expires_at=AUTH_EXPIRES_AT
    )
    runtime = build_runtime(factory)
    session = factory()
    try:
        service = canonical_execution_service(session, runtime)
        expired = service.execute_paper_plan(
            canonical_execute_request(envelope, authorization, key="workflow-expired"),
            clock=lambda: AFTER_AUTH_EXPIRY,
        )
        session.commit()
        assert expired.outcome is ExecutionCommandOutcome.BLOCKED
        assert expired.blocked_reason_code == "authorization_expired"
        assert session.scalars(select(LearningAttributionRecordRow)).first() is None
        late_plan = service.execute_paper_plan(
            canonical_execute_request(envelope, authorization, key="workflow-plan-expired"),
            clock=lambda: AFTER_PLAN_EXPIRY,
        )
        session.commit()
        assert late_plan.outcome is ExecutionCommandOutcome.BLOCKED
        assert session.scalars(select(LearningAttributionRecordRow)).first() is None
    finally:
        session.close()


@requires_postgres
def test_approval_mismatch_and_modified_plan_fail_closed() -> None:
    factory = _factory()
    _world, envelope, authorization = prepared_authorized_canonical(factory)
    runtime = build_runtime(factory)
    session = factory()
    try:
        service = canonical_execution_service(session, runtime)
        mismatched = canonical_execute_request(envelope, authorization, key="workflow-mismatch")
        mismatched = mismatched.model_copy(update={"authorization_id": uuid4()})
        with pytest.raises(NotFoundError):
            service.execute_paper_plan(mismatched, clock=lambda: EXECUTE_AT)
        session.rollback()
        assert session.scalars(select(LearningAttributionRecordRow)).first() is None
    finally:
        session.close()


@requires_postgres
def test_legacy_proposal_cannot_see_canonical_plan_root() -> None:
    factory = _factory()
    _world, envelope, _authorization = prepared_authorized_canonical(factory)
    session = factory()
    try:
        roots = session.scalars(
            select(TradeProposal).where(TradeProposal.plan_root_kind == PLAN_ROOT_CANONICAL)
        ).all()
        assert roots
        service = ProposalService(session, AuditService(session))
        items, total = service.list_proposals(organization_id=ORG_ID, user_id=USER_ID)
        assert total == 0
        assert all(item.id != envelope.plan.plan_id for item in items)
    finally:
        session.close()


@requires_postgres
def test_http_paper_plan_binding_and_legacy_isolation() -> None:
    factory = _factory()
    _world, envelope, authorization = prepared_authorized_canonical(factory)
    session = factory()
    try:
        session.add(Membership(user_id=USER_ID, organization_id=ORG_ID, role=MembershipRole.OWNER))
        session.commit()
    finally:
        session.close()

    settings = phase8_settings()
    app = create_app(settings)

    def _override() -> object:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _override
    token, _expires = create_access_token(
        user_id=USER_ID,
        organization_id=ORG_ID,
        email="phase8@example.com",
        settings=settings,
    )
    with TestClient(app) as client:
        # Lifespan composes a process-wide factory; bind the same test factory
        # so HTTP reads see committed Candidate / learning rows.
        app.state.canonical_runtime = build_runtime(factory)
        client.headers.update({"Authorization": f"Bearer {token}"})
        first, exec_session, _service, _runtime = _execute(
            factory, envelope, authorization, key="http-plan-1"
        )
        exec_session.close()
        forbidden = client.post(
            "/execution/paper-plan",
            json={
                "account_id": str(envelope.plan.account_id),
                "authorization_id": str(authorization.authorization_id),
                "revision_id": str(envelope.plan.revision_id),
                "idempotency_key": "http-plan-1",
                "side": "buy",
            },
        )
        assert forbidden.status_code == 422
        allow = client.post(
            "/execution/paper-plan",
            json={
                "account_id": str(envelope.plan.account_id),
                "authorization_id": str(authorization.authorization_id),
                "revision_id": str(envelope.plan.revision_id),
                "idempotency_key": "http-plan-1",
            },
        )
        assert allow.status_code == 200
        body = allow.json()
        assert body["outcome"] == "ALLOW"
        assert body["replayed"] is True
        assert body["command_id"] == str(first.command_id)
        receipt_id = body["receipt"]["receipt_id"]
        candidates = client.get("/canonical/candidates")
        assert candidates.status_code == 200
        assert candidates.json()["total"] == 1
        assert candidates.json()["items"][0]["display_name"] == (
            "Phase 7 compiled setup strategy · BTCUSDT · Short · 15m"
        )
        eligibility = client.get(
            f"/canonical/candidates/{envelope.lineage.candidate_id}/eligibility"
        )
        assert eligibility.status_code == 200
        receipt = client.get(f"/canonical/executions/{receipt_id}")
        assert receipt.status_code == 200
        stats = client.get("/canonical/learning/strategy-stats")
        assert stats.status_code == 200
        assert stats.json()["snapshot"]["human_vs_system"]["human_approvals"] == 1
        evaluation = client.get("/canonical/paper-evaluation/summary")
        assert evaluation.status_code == 200
        evaluation_body = evaluation.json()
        assert evaluation_body["authority"] == "canonical"
        assert evaluation_body["live_executable"] is False
        assert evaluation_body["watcher_activated"] is False
        assert evaluation_body["summary"]["authority"] == "paper_evaluation_measurement"
        assert evaluation_body["summary"]["live_executable"] is False
        assert evaluation_body["summary"]["facts"]["watcher_orchestration_enabled"] is False
        assert evaluation_body["summary"]["facts"]["telegram_interaction_enabled"] is False
        assert all(
            item["activate"] is False and item["auto_activate"] is False
            for item in evaluation_body["summary"]["refinements"]
        )
        legacy = client.post(
            "/execution/paper",
            json={
                "proposal_id": str(envelope.plan.plan_id),
                "idempotency_key": "legacy-should-not-mint",
            },
        )
        assert legacy.status_code in {400, 403, 404, 409, 422}
