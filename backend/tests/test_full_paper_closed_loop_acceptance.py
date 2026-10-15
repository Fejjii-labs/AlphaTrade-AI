"""One governed PostgreSQL acceptance chain and its fail-closed boundaries.

Run with ALPHATRADE_CLOSED_LOOP_PROOF_PATH to export verified run identities.
No Candidate, assessment, eligibility, authorization, fill or journal is seeded.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select

from app.attention.contracts import AttentionCategory
from app.attention.reader import AttentionQueueService
from app.candidate_alerts.gateway import CandidateAlertGateway
from app.core.errors import NotFoundError, ValidationAppError
from app.daily_review.contracts import ReviewTopic
from app.daily_review.reader import DailyReviewService
from app.daily_review.service import daily_window
from app.db.learning_attribution import LearningAttributionRecordRow
from app.db.models import (
    ApprovalAuthorization,
    CompiledSetupDefinition,
    ConversationMessage,
    ExecutionCommand,
    ExecutionFillFact,
    JournalLifecycleEvent,
    JournalTrade,
)
from app.db.telegram_paper_agent import TelegramPaperNotificationRow
from app.db.telegram_security import TelegramOutboxRow
from app.interactive_agent.contracts import AgentTurnRequest, TurnOperation
from app.interactive_agent.service import InteractiveAgentService
from app.paper_interaction.bridge import project_scan_report
from app.persistence.telegram_paper_agent import PostgresPaperAgentStore
from app.persistence.telegram_postgres import PostgresTelegramSecurityStore
from app.schemas.common import ConversationMessageRole, JournalTradeStatus, RiskAction
from app.schemas.execution_protocol import ClosePaperPlanRequest, ExecutePaperPlanRequest
from app.schemas.risk import KillSwitchMutationRequest, UserRiskSettingsUpdate
from app.schemas.strategy_analytics import StrategyAnalyticsDimension, StrategyAnalyticsFilters
from app.services.audit_service import AuditService
from app.services.canonical_reads import CanonicalReadService
from app.services.canonical_serialization import canonical_sha256
from app.services.execution_service import ExecutionService
from app.services.risk.kill_switch import KillSwitchService
from app.services.risk.settings_service import RiskSettingsService
from app.services.strategy_analytics_service import StrategyAnalyticsService
from app.signal_fusion.enums import ActionEligibilityState, SetupAssessmentState
from app.telegram_paper_agent.contracts import PaperAlertRecipient
from app.telegram_paper_agent.gateway import TelegramPaperAgent
from app.telegram_security.clock import FrozenClock
from app.telegram_security.contracts import OutboxState
from app.telegram_security.protocol import TelegramSecurityProtocol
from app.telegram_security.transport import FakeTelegramTransport
from tests.support.closed_loop_acceptance import ACCOUNT, agent_turn, build_world
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase6_evaluator import make_world
from tests.support.postgres_persistence import requires_postgres
from tests.support.telegram_security import BOT, CHAT, TokenSeq, enroll
from tests.test_watcher_paper_runtime import ORG, ORG_B, USER, USER_B, _world_factory

pytestmark = requires_postgres


@pytest.fixture(autouse=True)
def forbid_network_http(monkeypatch):
    from app.db import base

    class FixtureDatetime(datetime):
        tick = 0

        @classmethod
        def now(cls, tz=None):
            # Preserve normal transcript ordering across writes. A single
            # frozen timestamp makes PostgreSQL's "latest" ordering ambiguous.
            cls.tick += 1
            moment = EVALUATED_AT + timedelta(microseconds=cls.tick)
            return moment if tz is not None else moment.replace(tzinfo=None)

    # ORM timestamp fallbacks share the scenario clock; no authority rows are
    # rewritten to make them appear in the fixed Daily Review/Attention window.
    monkeypatch.setattr(base, "datetime", FixtureDatetime)
    from app.services.risk import daily_risk_accounting, rules

    monkeypatch.setattr(daily_risk_accounting, "datetime", FixtureDatetime)
    monkeypatch.setattr(rules, "datetime", FixtureDatetime)

    def forbidden(*args, **kwargs):
        pytest.fail("Acceptance attempted network HTTP; only fixture/fake transport is allowed.")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


@pytest.fixture
def world(request):
    result = build_world(failure=getattr(request, "param", None))
    yield result
    result.factory.kw["bind"].dispose()


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def propose(world):
    scan = world.scan()
    assert scan.reason_code == SetupAssessmentState.CONFIRMED_SETUP.value, scan
    assert len(scan.candidate_ids) == 1
    assert all(rule.passed for rule in scan.discussion.assessment.rule_results)
    eligibility = world.enable_account_and_eligibility(scan)
    assert eligibility.eligibility.state is ActionEligibilityState.ELIGIBLE
    assert eligibility.paper_actionable and not eligibility.live_executable
    session = world.factory()
    try:
        service = world.agent(session)
        proposal = agent_turn(service, world.prepare_message(scan))
        assert proposal.paper_execution is not None, proposal.reply
        assert proposal.paper_execution.stage == "proposed", proposal.reply
        assert proposal.approval_required
        assert count(session, ApprovalAuthorization) == 0
        assert count(session, ExecutionCommand) == count(session, ExecutionFillFact) == 0
        assert count(session, JournalTrade) == 0
    except BaseException:
        session.close()
        raise
    return scan, eligibility, session, service, proposal


def confirm(service, proposal):
    return agent_turn(
        service,
        proposal.paper_execution.confirmation_message,
        conversation_id=proposal.conversation_id,
    )


def notification_projection(world, scan):
    transport = FakeTelegramTransport()
    protocol = TelegramSecurityProtocol(
        store=PostgresTelegramSecurityStore(world.factory),
        transport=transport,
        clock=FrozenClock(EVALUATED_AT),
        enabled=True,
        token_factory=TokenSeq(),
    )
    _, binding_id = enroll(protocol, organization_id=ORG, user_id=USER)
    agent = TelegramPaperAgent(
        protocol=protocol,
        candidates=CandidateAlertGateway(
            lifecycle=world.runtime.lifecycle,
            protocol=protocol,
            clock=FrozenClock(EVALUATED_AT),
        ),
        clock=FrozenClock(EVALUATED_AT),
        store=PostgresPaperAgentStore(world.factory),
        enabled=True,
    )
    recipient = PaperAlertRecipient(
        organization_id=ORG,
        user_id=USER,
        account_id=ACCOUNT,
        binding_id=binding_id,
        bot_id=BOT,
        chat_id=CHAT,
    )
    projection = project_scan_report(agent, scan, recipient=recipient)
    assert projection is not None
    assert project_scan_report(agent, scan, recipient=recipient).converged
    # Projection is explicitly enabled in this isolated protocol; delivery is
    # never called and all deployment/network activation flags remain false.
    assert transport.attempts == transport.sent == []
    with world.factory() as session:
        assert count(session, TelegramPaperNotificationRow) == 1
        outboxes = list(session.scalars(select(TelegramOutboxRow)))
        assert outboxes and all(row.state == OutboxState.PENDING for row in outboxes)
        return {
            "intent_id": str(projection.intent.intent_id),
            "outbox_ids": [str(row.outbox_id) for row in outboxes],
            "network_requests": 0,
            "transport_attempts": 0,
            "candidate_outbox_id": str(projection.outbox.outbox_id),
            "outbox_states": [row.state for row in outboxes],
        }


def test_complete_governed_paper_closed_loop(world, monkeypatch):
    from app.agents import nodes

    def forbidden(*args, **kwargs):
        pytest.fail("Agent reached model/detector proposal authority during canonical paper flow.")

    monkeypatch.setattr(nodes, "strategy_module_execution", forbidden)
    monkeypatch.setattr(nodes, "trade_proposal_generation", forbidden)
    scan, eligibility, session, service, proposal = propose(world)
    try:
        candidate = scan.discussion.candidate
        assessment = scan.discussion.assessment
        plan = proposal.paper_execution.plan
        # The Agent returns deterministic RiskService and sizing results; it
        # cannot generate any of these numbers with narrative output.
        assert proposal.paper_execution.risk_result.action is not RiskAction.BLOCK
        assert plan.quantity.value == Decimal("0.004")
        assert plan.risk_and_exits.maximum_loss.value == Decimal("0.64")
        executed = confirm(service, proposal)
        assert executed.paper_execution.stage == "executed", executed.reply
        result = executed.paper_execution
        confirmation_row = session.scalars(
            select(ConversationMessage).where(
                ConversationMessage.organization_id == ORG,
                ConversationMessage.user_id == USER,
                ConversationMessage.conversation_id == UUID(proposal.conversation_id),
                ConversationMessage.role == ConversationMessageRole.USER,
                ConversationMessage.request_id == executed.request_id,
            )
        ).one()
        assert confirmation_row.content == proposal.paper_execution.confirmation_message
        assert result.plan == plan and result.candidate_id == candidate.candidate_id
        command = session.get(ExecutionCommand, result.paper_action_id)
        authorization = session.get(ApprovalAuthorization, result.authorization_id)
        fill = session.scalars(select(ExecutionFillFact)).one()
        journal = session.scalars(select(JournalTrade)).one()
        assert command.authorization_id == authorization.id
        assert command.revision_id == authorization.revision_id == plan.revision_id
        assert command.plan_content_hash == authorization.plan_content_hash == plan.content_hash
        assert fill.command_id == command.id and fill.venue_source == "paper_internal"
        assert fill.quantity == journal.size == plan.quantity.value
        assert fill.price == journal.entry_price == Decimal("100080")
        assert journal.execution_lifecycle_id == command.id
        assert journal.candidate_id == candidate.candidate_id
        assert journal.strategy_version_id == candidate.strategy_version_id
        assert journal.status is JournalTradeStatus.OPEN
        replay = confirm(world.agent(session), proposal)
        assert replay.paper_execution is not None, replay.reply
        assert replay.paper_execution.replayed
        assert replay.paper_execution.paper_action_id == command.id
        execution = ExecutionService(
            session, world.settings, AuditService(session), canonical_runtime=world.runtime
        )
        request = ExecutePaperPlanRequest(
            organization_id=ORG,
            user_id=USER,
            account_id=ACCOUNT,
            revision_id=plan.revision_id,
            authorization_id=authorization.id,
            idempotency_key=f"agent-paper-execute:{plan.revision_id}",
        )
        duplicate = execution.execute_paper_plan(request, clock=world.clock.now)
        assert duplicate.replayed and duplicate.command_id == command.id
        duplicate_fill = execution.apply_paper_plan_fill(
            command_id=command.id,
            fill_quantity=plan.quantity.value,
            fill_price=plan.basis_policy.execution_price.value,
            source_identity=fill.source_fill_identity,
            occurred_at=fill.occurred_at,
            venue_source="paper_internal",
        )
        assert duplicate_fill.replayed and duplicate_fill.fill_id == fill.id
        session.commit()
        assert count(session, ExecutionCommand) == count(session, ExecutionFillFact) == 1
        assert count(session, JournalTrade) == count(session, ApprovalAuthorization) == 1

        reads = CanonicalReadService(session, world.runtime)
        assert (
            reads.get_candidate(
                organization_id=ORG, candidate_id=candidate.candidate_id
            ).candidate.evidence_window_hash
            == scan.discussion.window.content_hash
        )
        assert (
            reads.get_candidate_eligibility(
                organization_id=ORG, candidate_id=candidate.candidate_id
            ).evaluation.eligibility.eligibility_id
            == eligibility.eligibility.eligibility_id
        )
        assert (
            reads.get_execution_receipt(
                organization_id=ORG, receipt_id=result.receipt_id
            ).receipt.command_id
            == command.id
        )
        attribution = reads.get_learning_record(
            organization_id=ORG, candidate_id=candidate.candidate_id
        ).record
        assert attribution.journal_trade_id == journal.id
        assert count(session, LearningAttributionRecordRow) == 1
        review = DailyReviewService(session).review(
            organization_id=ORG,
            user_id=USER,
            window=daily_window(EVALUATED_AT.date()),
            generated_at=EVALUATED_AT,
        )
        assert any(
            item.topic is ReviewTopic.PAPER_OPEN
            and any(source.record_id == str(journal.id) for source in item.sources)
            for item in review.facts
        )
        queue = AttentionQueueService(session).queue(
            organization_id=ORG, user_id=USER, now=EVALUATED_AT + timedelta(seconds=1)
        )
        position = next(item for item in queue.items if item.category is AttentionCategory.POSITION)
        assert position.sources[0].record_id == str(journal.id)
        assistant = session.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_id == UUID(proposal.conversation_id),
                ConversationMessage.role == ConversationMessageRole.ASSISTANT,
            )
            .order_by(ConversationMessage.created_at)
        ).all()
        explanation = next(
            row
            for row in assistant
            if row.payload.get("paper_execution", {}).get("stage") == "executed"
            and not row.payload["paper_execution"]["replayed"]
        )
        recorded = explanation.payload["paper_execution"]
        assert recorded["candidate_id"] == str(candidate.candidate_id)
        assert recorded["risk_result"] == result.risk_result.model_dump(mode="json")
        assert recorded["plan"] == plan.model_dump(mode="json")
        assert recorded["journal_trade_id"] == str(journal.id)
        assert str(command.id) in explanation.content and str(journal.id) in explanation.content

        # Strategy Analytics consumes CLOSED canonical journal outcomes. Close
        # the same paper fill through its existing lifecycle authority, using
        # an explicit deterministic exit fixture rather than inventing a PnL.
        close_request = ClosePaperPlanRequest(
            organization_id=ORG,
            user_id=USER,
            account_id=ACCOUNT,
            revision_id=plan.revision_id,
            command_id=command.id,
            exit_price=Decimal("99920"),
            exit_reason="acceptance_target_fixture",
            occurred_at=EVALUATED_AT + timedelta(minutes=1),
            idempotency_key="acceptance-close",
        )
        close = execution.close_canonical_paper_plan(close_request)
        session.commit()
        assert close.journal_trade_id == journal.id and close.net_pnl == Decimal("0.64")
        assert execution.close_canonical_paper_plan(close_request).replayed
        session.commit()
        analytics = StrategyAnalyticsService(session).compute(
            organization_id=ORG,
            user_id=USER,
            filters=StrategyAnalyticsFilters(strategy_version_id=candidate.strategy_version_id),
            group_by=(StrategyAnalyticsDimension.STRATEGY_VERSION,),
        )
        assert analytics.overall.trade_count == 1
        assert analytics.overall.net_pnl_total == Decimal("0.64")
        assert analytics.buckets[0].dimensions[StrategyAnalyticsDimension.STRATEGY_VERSION] == str(
            candidate.strategy_version_id
        )
        closed_review = DailyReviewService(session).review(
            organization_id=ORG,
            user_id=USER,
            window=review.window,
            generated_at=close_request.occurred_at,
        )
        assert any(item.topic is ReviewTopic.PAPER_CLOSE for item in closed_review.facts)
        assert closed_review.daily_pnl[0].recorded_net_pnl == Decimal("0.64")

        class ForbiddenResponder:
            def compose(self, **kwargs):
                pytest.fail("Model was asked to explain authoritative paper facts.")

        reader = InteractiveAgentService(
            session, settings=world.settings, responder=ForbiddenResponder()
        )
        explanation_request = AgentTurnRequest(
            message=f"Explain paper execution command={command.id}",
            conversation_id=UUID(proposal.conversation_id),
        )
        historical = reader.handle_turn(explanation_request, organization_id=ORG, user_id=USER)
        assert historical.operation is TurnOperation.READ
        assert not historical.proposals and not historical.authority_mutated
        assert not historical.execution_attempted
        assert result.risk_result.explanation in historical.reply
        assert JournalTradeStatus.CLOSED.value in historical.reply and "0.64" in historical.reply
        assert {
            str(candidate.candidate_id),
            str(plan.revision_id),
            str(command.id),
            str(fill.id),
            str(journal.id),
            str(explanation.id),
        } <= {source.record_id for source in historical.connections}
        assert count(session, ExecutionCommand) == count(session, ExecutionFillFact) == 1
        assert count(session, JournalTrade) == count(session, ApprovalAuthorization) == 1
        with pytest.raises(NotFoundError):
            reader.handle_turn(
                AgentTurnRequest(message=explanation_request.message),
                organization_id=ORG_B,
                user_id=USER_B,
            )
        with pytest.raises(NotFoundError):
            reads.get_execution_receipt(organization_id=ORG_B, receipt_id=result.receipt_id)
        with pytest.raises(NotFoundError):
            reads.get_learning_record(organization_id=ORG_B, candidate_id=candidate.candidate_id)
        assert (
            StrategyAnalyticsService(session)
            .compute(
                organization_id=ORG_B,
                user_id=USER_B,
                filters=StrategyAnalyticsFilters(),
            )
            .overall.trade_count
            == 0
        )
        assert (
            AttentionQueueService(session)
            .queue(
                organization_id=ORG_B,
                user_id=USER_B,
                now=EVALUATED_AT,
            )
            .items
            == ()
        )
        session.commit()
        attribution = reads.get_learning_record(
            organization_id=ORG, candidate_id=candidate.candidate_id
        ).record
        assert attribution.facts.outcome.status is JournalTradeStatus.CLOSED
        assert attribution.facts.outcome.net_pnl == Decimal("0.64")
        compiled = session.get(CompiledSetupDefinition, candidate.setup_definition_id)
        assert compiled.content_hash == candidate.executable_setup.content_hash
        lifecycle = list(session.scalars(select(JournalLifecycleEvent)))
        assert all(row.execution_lifecycle_id == command.id for row in lifecycle)
        proof = {
            "base_sha": "8673d8f69779ea516ca97456baea7b3064daf089",
            "scenario": "bearish-liquidity-sweep-cvd-at-4h-resistance",
            "fixture_clock": EVALUATED_AT.isoformat(),
            "market_io": "httpx.MockTransport",
            "watcher_lineage_id": str(scan.lineage_id),
            "watcher_request_hash": scan.request_hash,
            "evidence_window_hash": candidate.evidence_window_hash,
            "strategy_version_id": str(candidate.strategy_version_id),
            "compiled_setup_definition_id": str(candidate.setup_definition_id),
            "compiled_setup_content_hash": candidate.executable_setup.content_hash,
            "compiled_setup_definition": compiled.compiled_ast,
            "compiler_version": compiled.compiler_version,
            "assessment": assessment.model_dump(mode="json"),
            "evidence_window": scan.discussion.window.model_dump(mode="json"),
            "assessment_id": str(assessment.assessment_id),
            "assessment_content_hash": assessment.content_hash,
            "candidate_id": str(candidate.candidate_id),
            "candidate_content_hash": candidate.content_hash,
            "eligibility_id": str(eligibility.eligibility.eligibility_id),
            "eligibility_content_hash": eligibility.eligibility.content_hash,
            "eligibility_risk_snapshot_id": str(eligibility.eligibility.risk_snapshot_id),
            "risk_result": result.risk_result.model_dump(mode="json"),
            "risk_result_hash": canonical_sha256(result.risk_result.model_dump(mode="json")),
            "trade_plan_revision_id": str(plan.revision_id),
            "trade_plan_hash": plan.content_hash,
            "trade_plan": plan.model_dump(mode="json"),
            "confirmation": {
                "conversation_id": proposal.conversation_id,
                "user_message_id": str(confirmation_row.id),
                "request_id": executed.request_id,
                "approval_id": str(result.approval_id),
                "authorization_id": str(authorization.id),
                "authorization_hash": authorization.authorization_content_hash,
                "message": result.confirmation_message,
            },
            "execution_command_id": str(command.id),
            "execution_receipt_id": str(result.receipt_id),
            "paper_fill_id": str(fill.id),
            "paper_fill_hash": fill.content_hash,
            "journal_trade_id": str(journal.id),
            "journal_lifecycle_event_ids": [str(row.id) for row in lifecycle],
            "analytics_attribution": attribution.model_dump(mode="json"),
            "strategy_analytics": {
                "strategy_version_id": str(candidate.strategy_version_id),
                "trade_count": analytics.overall.trade_count,
                "net_pnl": str(analytics.overall.net_pnl_total),
            },
            "daily_review_id": str(closed_review.review_id),
            "daily_review_hash": closed_review.content_hash,
            "daily_review": closed_review.model_dump(mode="json"),
            "attention_item_id": str(position.item_id),
            "attention_item": position.model_dump(mode="json"),
            "execution_result_message_id": str(explanation.id),
            "agent_explanation_message_id": str(historical.assistant_message_id),
            "agent_explanation": historical.reply,
            "agent_explanation_sources": [
                item.model_dump(mode="json") for item in historical.connections
            ],
            "quantity": str(fill.quantity),
            "entry": str(fill.price),
            "exit": str(close.exit_price),
            "net_pnl": str(close.net_pnl),
            "safety": {
                "enable_real_trading": False,
                "execution_mode": "paper",
                "exchange_mode": "paper_internal",
                "telegram_network_permitted": False,
            },
        }
    finally:
        session.close()
    proof["notification"] = notification_projection(world, scan)
    proof["proof_hash"] = canonical_sha256(proof)
    if path := os.environ.get("ALPHATRADE_CLOSED_LOOP_PROOF_PATH"):
        Path(path).write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")


@pytest.mark.parametrize(
    "mode,state",
    [("no_setup", SetupAssessmentState.NO_SETUP), ("watch", SetupAssessmentState.WATCH)],
)
def test_non_confirmed_evaluation_produces_no_candidate(world, mode, state):
    # Incomplete warmup and a full window without a swing are evidence variants;
    # the persisted approved policy and normal evaluator determine their state.
    evidence = (
        make_world(bar_15m_count=10, include_snapshot=False)
        if mode == "no_setup"
        else make_world(pattern_bars=False)
    )
    scan = world.scan(evidence_factory=_world_factory(evidence))
    assert scan.reason_code == state.value
    assert scan.candidate_ids == ()
    assert world.runtime.candidate_repository.list_for_organization(ORG)[1] == 0
    with world.factory() as session:
        assert count(session, ExecutionFillFact) == count(session, JournalTrade) == 0


@pytest.mark.parametrize("world", ["stale", "missing"], indirect=True)
def test_stale_or_missing_market_evidence_produces_no_candidate(world):
    scan = world.scan()
    assert scan.candidate_ids == ()
    assert scan.reason_code == "canonical_evidence_unavailable"
    assert world.runtime.candidate_repository.list_for_organization(ORG)[1] == 0
    with world.factory() as session:
        assert count(session, ExecutionFillFact) == count(session, JournalTrade) == 0


@pytest.mark.parametrize(
    "gate", ["risk", "kill_switch", "missing_confirmation", "wrong_confirmation"]
)
def test_confirmation_gates_produce_no_fill(world, gate):
    _, _, session, service, proposal = propose(world)
    try:
        if gate == "risk":
            RiskSettingsService(session, AuditService(session)).update(
                UserRiskSettingsUpdate(default_account_balance=Decimal("100")),
                organization_id=ORG,
                user_id=USER,
            )
            session.commit()
        elif gate == "kill_switch":
            KillSwitchService(session, AuditService(session), world.settings).activate(
                organization_id=ORG,
                actor_user_id=USER,
                payload=KillSwitchMutationRequest(confirm=True, reason="Acceptance kill gate"),
            )
            session.commit()
        if gate == "missing_confirmation":
            response = agent_turn(service, "yes", conversation_id=proposal.conversation_id)
        elif gate == "wrong_confirmation":
            response = agent_turn(
                service,
                proposal.paper_execution.confirmation_message.replace(
                    proposal.paper_execution.plan.content_hash, "0" * 64
                ),
                conversation_id=proposal.conversation_id,
            )
        else:
            response = confirm(service, proposal)
        assert response.paper_execution is None or response.paper_execution.stage == "blocked"
        if gate == "risk":
            assert response.risk_result.action is RiskAction.BLOCK
        assert count(session, ExecutionFillFact) == count(session, JournalTrade) == 0
    finally:
        session.close()


def test_cross_tenant_reads_and_confirmation_fail(world):
    scan, _, session, service, proposal = propose(world)
    try:
        reads = CanonicalReadService(session, world.runtime)
        with pytest.raises(NotFoundError):
            reads.get_candidate(organization_id=ORG_B, candidate_id=scan.candidate_ids[0])
        with pytest.raises(NotFoundError):
            agent_turn(
                service,
                proposal.paper_execution.confirmation_message,
                conversation_id=proposal.conversation_id,
                organization_id=ORG_B,
                user_id=USER_B,
            )
        assert (
            AttentionQueueService(session)
            .queue(organization_id=ORG_B, user_id=USER_B, now=EVALUATED_AT)
            .items
            == ()
        )
        assert count(session, ExecutionFillFact) == count(session, JournalTrade) == 0
    finally:
        session.close()


@pytest.mark.parametrize("capture", ["missing", "mismatched_lineage"])
def test_agent_explanation_refuses_missing_or_mismatched_capture(world, capture):
    _, _, session, service, proposal = propose(world)
    try:
        result = confirm(service, proposal).paper_execution
        message = session.scalars(
            select(ConversationMessage).where(
                ConversationMessage.role == ConversationMessageRole.ASSISTANT,
                ConversationMessage.payload["paper_execution"]["paper_action_id"].as_string()
                == str(result.paper_action_id),
            )
        ).one()
        captured = dict(message.payload["paper_execution"])
        if capture == "missing":
            message.payload = {}
            expected_error = NotFoundError
        else:
            captured["eligibility_id"] = str(UUID(int=99))
            message.payload = dict(message.payload, paper_execution=captured)
            expected_error = ValidationAppError
        session.commit()
        reader = InteractiveAgentService(session, settings=world.settings)
        with pytest.raises(expected_error):
            reader.handle_turn(
                AgentTurnRequest(
                    message=f"Explain paper execution command={result.paper_action_id}"
                ),
                organization_id=ORG,
                user_id=USER,
            )
        assert count(session, ExecutionCommand) == count(session, ExecutionFillFact) == 1
        assert count(session, JournalTrade) == count(session, ApprovalAuthorization) == 1
    finally:
        session.close()


@pytest.mark.parametrize("capture_present", [True, False])
def test_natural_trade_question_resolves_canonical_internal_paper_lineage(world, capture_present):
    _, _, session, service, proposal = propose(world)
    try:
        executed = confirm(service, proposal).paper_execution
        assert executed.stage == "executed"
        if not capture_present:
            message = session.scalars(
                select(ConversationMessage).where(
                    ConversationMessage.payload["paper_execution"]["paper_action_id"].as_string()
                    == str(executed.paper_action_id),
                    ConversationMessage.role == ConversationMessageRole.ASSISTANT,
                )
            ).one()
            message.payload = {}
            session.commit()
        contexts = []

        class Responder:
            def compose(self, **kwargs):
                contexts.append(kwargs["factual_context"])
                return "This was an internal paper trade under recorded plan authorization."

        before = {
            model: count(session, model)
            for model in (
                ExecutionCommand,
                ExecutionFillFact,
                JournalTrade,
                ApprovalAuthorization,
                JournalLifecycleEvent,
            )
        }
        result = InteractiveAgentService(
            session, settings=world.settings, responder=Responder()
        ).handle_turn(
            AgentTurnRequest(
                message=(
                    "Explain my latest BTCUSDT short paper trade: "
                    "strategy, entry, stop, target, authorization and execution venue."
                )
            ),
            organization_id=ORG,
            user_id=USER,
        )
        assert contexts
        facts = contexts[0]
        assert "internal paper simulator (no exchange execution)" in facts
        assert "Why it was allowed:" in facts and "Missing evidence:" in facts
        for record in (
            executed.journal_trade_id,
            executed.plan.revision_id,
            executed.authorization_id,
            executed.receipt_id,
            executed.eligibility_id,
        ):
            assert str(record) in {ref.record_id for ref in result.connections}
        assert (
            "[Captured Risk]" in facts
            if capture_present
            else "detailed captured RiskEngine decision" in facts
        )
        assert result.recorded_evidence == facts
        assert (
            not result.proposals and not result.execution_attempted and not result.authority_mutated
        )
        assert {model: count(session, model) for model in before} == before
    finally:
        session.close()
