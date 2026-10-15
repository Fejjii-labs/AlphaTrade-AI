"""Conversation-to-canonical PAPER path, using actual PostgreSQL authorities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agents.paper_intent import parse_paper_confirmation, parse_paper_intent
from app.core.config import ExchangeMode
from app.db.models import (
    AccountRiskAccountingState,
    ApprovalAuthorization,
    Conversation,
    ExecutionCommand,
    ExecutionFillFact,
    JournalTrade,
    TradePlanRevision,
)
from app.runtime.canonical import build_production_canonical_runtime
from app.schemas.position_sizing import PaperPositionSizingRequest
from app.services.agent_service import AgentInvokeContext, build_agent_service
from app.services.position_sizing_service import PositionSizingService
from app.signal_fusion.enums import CandidateReasonCode, CandidateState
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase6_fusion import ACCOUNT_ID, ORG_ID, USER_ID
from tests.support.phase7_postgres import postgres_plan_world
from tests.support.phase8_runtime import phase8_settings
from tests.support.postgres_persistence import ReusablePhase7Database, requires_postgres


@dataclass
class MutableClock:
    moment: datetime = EVALUATED_AT

    def now(self) -> datetime:
        return self.moment


class CanonicalQuotes:
    """Only external market reads are stubbed; no execution authority is replaced."""

    def __init__(self, clock):
        self.clock = clock
        self.price = "100000"
        self.live = True

    def read(self, *, organization_id, symbol):
        return SimpleNamespace(
            source=SimpleNamespace(
                venue="binance",
                instrument_id=f"binance:usdm_futures:perpetual:{symbol}",
                provider_symbol=symbol,
                market_type="perpetual",
                is_live=self.live,
                is_mock=False,
            ),
            current_price=SimpleNamespace(
                price=self.price,
                usable_as_current_market_price=True,
                is_live=self.live,
                is_mock=False,
                fallback_used=False,
                source_time=self.clock.now() - timedelta(seconds=1),
                freshness=SimpleNamespace(valid_until=self.clock.now() + timedelta(seconds=9)),
            ),
        )


@pytest.fixture(scope="module")
def paper_database():
    database = ReusablePhase7Database()
    yield database
    database.dispose()


@pytest.fixture
def paper_world(paper_database):
    factory = paper_database.session_factory()
    world = postgres_plan_world(factory)
    clock = MutableClock()
    settings = phase8_settings().model_copy(
        update={
            "exchange_mode": ExchangeMode.PAPER_INTERNAL,
            "narrative_llm_enabled": False,
        }
    )
    runtime = build_production_canonical_runtime(factory, settings=settings, clock=clock)
    quotes = CanonicalQuotes(clock)
    session = factory()
    service = build_agent_service(
        settings=settings, session=session, canonical_runtime=runtime, canonical_evidence=quotes
    )
    yield SimpleNamespace(
        session=session,
        factory=factory,
        service=service,
        world=world,
        runtime=runtime,
        clock=clock,
        quotes=quotes,
        settings=settings,
    )
    session.close()


def details(w, **updates):
    data = {
        "candidate_id": str(w.world.candidate.candidate_id),
        "account_id": str(ACCOUNT_ID),
        "symbol": "BTCUSDT",
        "venue": "binance",
        "market": "PERPETUAL",
        "timeframe": "15m",
        "direction": "short",
        "entry": "100000",
        "stop": "101000",
        "targets": ["99000", "98000"],
    }
    data.update(updates)
    return data


def run(w, message, conversation=None, user=USER_ID, org=ORG_ID):
    return w.service.run(
        message,
        AgentInvokeContext(
            request_id=str(uuid4()),
            organization_id=org,
            user_id=user,
            conversation_id=UUID(conversation) if conversation else None,
        ),
    )


def prepare(w, **updates):
    return run(w, "Prepare paper trade " + json.dumps(details(w, **updates)))


def count(w, model):
    return w.session.scalar(select(func.count()).select_from(model))


@requires_postgres
def test_successful_conversation_paper_flow(paper_world, monkeypatch):
    w = paper_world
    # The graph must bypass strategy/detector proposal generation for paper actions.
    from app.agents import nodes

    def forbidden(*args, **kwargs):
        pytest.fail("Agent paper action reached a detector or synthetic proposal.")

    monkeypatch.setattr(nodes, "strategy_module_execution", forbidden)
    monkeypatch.setattr(nodes, "trade_proposal_generation", forbidden)
    # Recompile with the forbidden node boundaries.
    from app.services.agent_service import AgentService

    w.service = AgentService(w.service.runtime)
    proposal = prepare(w)
    assert proposal.approval_required is True, proposal.reply
    assert proposal.paper_execution.stage == "proposed"
    assert count(w, ExecutionCommand) == count(w, ExecutionFillFact) == count(w, JournalTrade) == 0
    assert count(w, ApprovalAuthorization) == 0
    plan = proposal.paper_execution.plan
    assert plan.quantity.value == Decimal("0.005")
    assert plan.quantity.value % plan.instrument_rules.lot_size == 0
    assert sum(t.quantity_fraction for t in plan.risk_and_exits.targets) == 1
    assert plan.risk_and_exits.maximum_loss.value == Decimal("5")
    confirmation = run(w, proposal.paper_execution.confirmation_message, proposal.conversation_id)
    assert confirmation.paper_execution.stage == "executed", confirmation.reply
    result = confirmation.paper_execution
    assert result.candidate_id == w.world.candidate.candidate_id
    assert result.plan.plan_id == plan.plan_id
    assert result.plan.revision_id == plan.revision_id
    assert result.paper_action_id and result.receipt_id and result.journal_trade_id
    assert count(w, ExecutionCommand) == count(w, ExecutionFillFact) == count(w, JournalTrade) == 1
    journal = w.session.get(JournalTrade, result.journal_trade_id)
    assert journal.candidate_id == result.candidate_id
    assert journal.execution_lifecycle_id == result.paper_action_id
    assert journal.size == plan.quantity.value
    assert journal.status.value == "open"
    assert not w.settings.enable_real_trading


@requires_postgres
def test_duplicate_confirmation_and_restart_converge(paper_world):
    w = paper_world
    proposal = prepare(w)
    first = run(w, proposal.paper_execution.confirmation_message, proposal.conversation_id)
    assert first.paper_execution.stage == "executed", first.reply
    # Restart Agent orchestration and advance beyond proposal TTL: durable replay may
    # return old identities but must never issue a fresh action or allocation.
    w.clock.moment += timedelta(minutes=1)
    w.service = build_agent_service(
        settings=w.settings,
        session=w.session,
        canonical_runtime=w.runtime,
        canonical_evidence=w.quotes,
    )
    replay = run(w, proposal.paper_execution.confirmation_message, proposal.conversation_id)
    assert replay.paper_execution is not None, replay.reply
    assert replay.paper_execution.replayed is True, replay.reply
    assert replay.paper_execution.paper_action_id == first.paper_execution.paper_action_id
    assert replay.paper_execution.journal_trade_id == first.paper_execution.journal_trade_id
    assert count(w, ExecutionCommand) == count(w, ExecutionFillFact) == count(w, JournalTrade) == 1
    assert count(w, ApprovalAuthorization) == 1


@requires_postgres
@pytest.mark.parametrize("change", ["expiry", "hash", "price", "candidate", "risk", "new_request"])
def test_stale_confirmation_fails_closed(paper_world, change):
    w = paper_world
    proposal = prepare(w)
    assert proposal.paper_execution is not None, proposal.reply
    message = proposal.paper_execution.confirmation_message
    if change == "expiry":
        w.clock.moment += timedelta(seconds=15)
    elif change == "hash":
        message = message.replace(proposal.paper_execution.plan.content_hash, "0" * 64)
    elif change == "price":
        w.quotes.price = "100100"
    elif change == "candidate":
        with w.runtime.bind_session(w.session):
            w.runtime.lifecycle.transition(
                organization_id=ORG_ID,
                candidate_id=w.world.candidate.candidate_id,
                new_state=CandidateState.INVALIDATED,
                reason_codes=(CandidateReasonCode.INVALIDATED,),
                idempotency_key="invalidate-before-confirm",
                correlation_id=uuid4(),
            )
        w.session.commit()
    elif change == "risk":
        from app.db.models import UserRiskSettings

        w.session.add(
            UserRiskSettings(
                organization_id=ORG_ID, user_id=USER_ID, max_risk_per_trade_percent=Decimal("0.001")
            )
        )
        w.session.commit()
    else:
        run(w, "Prepare paper trade {}", proposal.conversation_id)
    refused = run(w, message, proposal.conversation_id)
    assert refused.approval_status == "blocked", refused.reply
    assert count(w, ExecutionFillFact) == count(w, JournalTrade) == 0
    # Candidate lineage invalidation is also checked by the canonical claim.
    if change != "candidate":
        assert count(w, ApprovalAuthorization) == 0


@requires_postgres
@pytest.mark.parametrize("at_confirmation", [False, True])
def test_risk_block_is_final(paper_world, at_confirmation):
    w = paper_world
    proposal = prepare(w) if at_confirmation else None
    from app.services.audit_service import AuditService
    from app.services.risk.settings_service import RiskSettingsService

    daily = RiskSettingsService(w.session, AuditService(w.session)).ensure_daily_risk_state(
        organization_id=ORG_ID, user_id=USER_ID, day=datetime.now().date()
    )
    daily.locked = True
    w.session.commit()
    response = (
        run(w, proposal.paper_execution.confirmation_message, proposal.conversation_id)
        if proposal
        else prepare(w)
    )
    assert response.approval_status == "blocked", response.reply
    assert "Risk BLOCK is final" in response.reply
    assert (
        count(w, ExecutionFillFact)
        == count(w, JournalTrade)
        == count(w, ApprovalAuthorization)
        == 0
    )


@requires_postgres
def test_gateway_rechecks_locked_risk_capacity(paper_world):
    w = paper_world
    proposal = prepare(w)
    w.session.add(
        AccountRiskAccountingState(
            organization_id=ORG_ID,
            account_id=ACCOUNT_ID,
            max_notional=Decimal("1"),
            max_daily_loss=Decimal("100"),
            max_trade_slots=20,
            max_symbol_notional=Decimal("1"),
            exposure_unit="USDT",
        )
    )
    w.session.commit()
    response = run(w, proposal.paper_execution.confirmation_message, proposal.conversation_id)
    assert response.paper_execution.stage == "blocked", response.reply
    assert response.paper_execution.reason_code == "insufficient_total_exposure"
    assert count(w, ExecutionFillFact) == count(w, JournalTrade) == 0


@requires_postgres
@pytest.mark.parametrize(
    "update",
    [
        {"stop": "100000"},
        {"stop": "99000"},
        {"entry": "NaN"},
        {"targets": []},
        {"targets": ["101000"]},
        {"targets": ["98000", "99000"]},
        {"entry": 100000.0},
        {"position_size": "100"},
        {"venue": "bybit"},
        {"market": "SPOT"},
        {"entry": "100000.01"},
        {"mode": "live"},
    ],
)
def test_invalid_details_and_live_refusal(paper_world, update):
    w = paper_world
    response = prepare(w, **update)
    assert response.approval_status == "blocked", response.reply
    assert (
        count(w, TradePlanRevision)
        == count(w, ApprovalAuthorization)
        == count(w, ExecutionCommand)
        == 0
    )


@requires_postgres
@pytest.mark.parametrize("identity", ["organization", "user", "conversation"])
def test_confirmation_tenant_and_conversation_isolation(paper_world, identity):
    w = paper_world
    proposal = prepare(w)
    conversation = Conversation(organization_id=ORG_ID, user_id=USER_ID, title="Different chat")
    w.session.add(conversation)
    w.session.commit()
    if identity == "conversation":
        response = run(w, proposal.paper_execution.confirmation_message, str(conversation.id))
        assert response.approval_status == "blocked"
    else:
        from app.core.errors import NotFoundError

        with pytest.raises(NotFoundError):
            run(
                w,
                proposal.paper_execution.confirmation_message,
                proposal.conversation_id,
                org=uuid4() if identity == "organization" else ORG_ID,
                user=uuid4() if identity == "user" else USER_ID,
            )
    assert count(w, ExecutionCommand) == count(w, ApprovalAuthorization) == 0


@pytest.mark.parametrize(
    "message",
    [
        "yes",
        "do it",
        "Confirm paper execution",
        f"Confirm paper execution revision={uuid4()} hash={'a' * 64} if risk looks good",
    ],
)
def test_ambiguous_confirmation_is_not_authority(message):
    with pytest.raises(ValueError):
        parse_paper_confirmation(message)


def test_sizing_preserves_costs_and_rounds_down():
    result = PositionSizingService().calculate_paper(
        PaperPositionSizingRequest(
            approved_risk_amount=Decimal("100"),
            maximum_notional=Decimal("10000"),
            entry=Decimal("100"),
            stop=Decimal("97"),
            lot_size=Decimal("0.1"),
            minimum_quantity=Decimal("0.1"),
            minimum_notional=Decimal("5"),
            fee_allowance=Decimal("2"),
            funding_allowance=Decimal("1"),
            slippage_allowance=Decimal("1"),
        )
    )
    assert result.quantity == Decimal("32")
    assert result.maximum_loss == Decimal("100")
    assert result.quantity % Decimal("0.1") == 0


def test_missing_required_market_and_trade_fields():
    with pytest.raises(ValidationError):
        parse_paper_intent('Prepare paper trade {"symbol": "BTCUSDT"}')


@requires_postgres
def test_concurrent_confirmations_create_one_action(paper_world):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    w = paper_world
    proposal = prepare(w)
    barrier = Barrier(2)

    def confirm_once():
        with w.factory() as session:
            service = build_agent_service(
                settings=w.settings,
                session=session,
                canonical_runtime=w.runtime,
                canonical_evidence=w.quotes,
            )
            barrier.wait(timeout=10)
            return service.run(
                proposal.paper_execution.confirmation_message,
                AgentInvokeContext(
                    request_id=str(uuid4()),
                    organization_id=ORG_ID,
                    user_id=USER_ID,
                    conversation_id=UUID(proposal.conversation_id),
                ),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(confirm_once) for _ in range(2)]
        results = [f.result(timeout=30) for f in futures]
    assert all(r.paper_execution is not None for r in results), [r.reply for r in results]
    assert results[0].paper_execution.paper_action_id == results[1].paper_execution.paper_action_id
    assert sorted(r.paper_execution.replayed for r in results) == [False, True]
    w.session.expire_all()
    assert count(w, ExecutionCommand) == count(w, ExecutionFillFact) == count(w, JournalTrade) == 1


@requires_postgres
def test_explicit_labelled_trade_details(paper_world):
    w = paper_world
    tokens = " ".join(
        f"{key}={','.join(value) if isinstance(value, list) else value}"
        for key, value in details(w).items()
    )
    response = run(w, "Prepare paper trade " + tokens)
    assert response.paper_execution.stage == "proposed", response.reply
    assert count(w, ExecutionCommand) == 0


@requires_postgres
def test_unpresented_revision_cannot_confirm(paper_world):
    w = paper_world
    response = run(w, f"Confirm paper execution revision={uuid4()} hash={'a' * 64}")
    assert response.approval_status == "blocked"
    assert count(w, ApprovalAuthorization) == count(w, ExecutionCommand) == 0


@requires_postgres
def test_wrong_account_cannot_create_plan(paper_world):
    response = prepare(paper_world, account_id=str(uuid4()))
    assert response.approval_status == "blocked"
    assert count(paper_world, TradePlanRevision) == 0


@requires_postgres
def test_rejected_approval_cannot_execute(paper_world):
    from app.db.models import ApprovalRequest
    from app.schemas.common import ApprovalStatus

    w = paper_world
    proposal = prepare(w)
    approval = w.session.get(ApprovalRequest, proposal.paper_execution.approval_id)
    approval.status = ApprovalStatus.REJECTED
    w.session.commit()
    response = run(w, proposal.paper_execution.confirmation_message, proposal.conversation_id)
    assert response.approval_status == "blocked", response.reply
    assert count(w, ApprovalAuthorization) == count(w, ExecutionCommand) == 0


@requires_postgres
def test_execution_quota_is_checked_without_minting_authorization(paper_world):
    from app.db.models import OrganizationQuota

    w = paper_world
    proposal = prepare(w)
    w.session.add(OrganizationQuota(organization_id=ORG_ID, limit_paper_execution=0))
    w.session.commit()
    response = run(w, proposal.paper_execution.confirmation_message, proposal.conversation_id)
    assert response.approval_status == "blocked", response.reply
    assert count(w, ApprovalAuthorization) == count(w, ExecutionCommand) == 0


@requires_postgres
def test_degraded_market_cannot_mint_plan(paper_world):
    w = paper_world
    w.quotes.live = False
    response = prepare(w)
    assert response.approval_status == "blocked"
    assert count(w, TradePlanRevision) == count(w, ExecutionCommand) == 0


@pytest.fixture
def interactive_world(paper_world):
    from app.db.models import Membership
    from app.schemas.common import MembershipRole

    w = paper_world
    w.session.add(Membership(organization_id=ORG_ID, user_id=USER_ID, role=MembershipRole.OWNER))
    w.session.commit()
    return w


def interactive_service(w, session=None):
    from app.interactive_agent.service import InteractiveAgentService
    from app.services.agent_paper_execution import AgentPaperExecutionService

    session = session or w.session
    return InteractiveAgentService(
        session,
        settings=w.settings,
        paper_execution=AgentPaperExecutionService(session, w.settings, w.runtime, w.quotes),
    )


def interactive_prepare(w, *, text=False):
    from app.interactive_agent.actions import ActionRequest
    from app.interactive_agent.contracts import AgentTurnRequest

    result = interactive_service(w).handle_turn(
        AgentTurnRequest(
            message="Prepare paper trade " + json.dumps(details(w))
            if text
            else "Prepare this paper execution",
            action=None
            if text
            else ActionRequest(
                name="paper_trade.prepare_execution", arguments={"trade": details(w)}
            ),
        ),
        organization_id=ORG_ID,
        user_id=USER_ID,
    )
    w.session.commit()
    return result.proposals[0]


def interactive_confirm(w, proposal, *, session=None, **updates):
    from app.interactive_agent.contracts import ProposalDecisionRequest

    body = {
        "conversation_id": proposal.conversation_id,
        "expected_content_hash": proposal.content_hash,
        "statement": "I confirm",
    }
    body.update(updates)
    return interactive_service(w, session).confirm(
        proposal.proposal_id,
        ProposalDecisionRequest(**body),
        organization_id=ORG_ID,
        user_id=USER_ID,
    )


@requires_postgres
@pytest.mark.parametrize("text", [False, True])
def test_interactive_sealed_proposal_executes_and_replays(interactive_world, text):
    from app.interactive_agent.contracts import ProposalLifecycle

    w = interactive_world
    proposal = interactive_prepare(w, text=text)
    assert proposal.status is ProposalLifecycle.PROPOSED
    assert proposal.applied is False
    assert proposal.authority_mutated is True
    assert count(w, ExecutionCommand) == count(w, ApprovalAuthorization) == 0
    preview = proposal.payload["paper_execution"]
    assert preview["pretrade"]["risk_reward_ratios"] == ["1", "2"]
    assert preview["plan"]["quantity"]["value"] == "0.005"
    result = interactive_confirm(w, proposal)
    w.session.commit()
    assert result.status is ProposalLifecycle.APPLIED
    assert result.resulting_record_id == UUID(result.application_result["paper_action_id"])
    assert result.application_result["candidate_id"] == str(w.world.candidate.candidate_id)
    assert result.application_result["plan"]["revision_id"] == preview["plan"]["revision_id"]
    assert result.application_result["authorization_id"]
    assert result.application_result["receipt_id"]
    assert result.application_result["journal_trade_id"]
    assert result.content_hash == proposal.content_hash
    w.clock.moment += timedelta(minutes=5)
    duplicate = interactive_confirm(w, proposal)
    w.session.commit()
    assert duplicate.application_result == result.application_result
    assert count(w, ExecutionCommand) == count(w, ExecutionFillFact) == count(w, JournalTrade) == 1


@requires_postgres
@pytest.mark.parametrize("change", ["hash", "expiry", "price", "risk", "reject", "ambiguous"])
def test_interactive_confirmation_fails_closed(interactive_world, change):
    from app.core.errors import AppError
    from app.interactive_agent.contracts import ProposalDecisionRequest

    w = interactive_world
    proposal = interactive_prepare(w)
    overrides = {}
    if change == "hash":
        overrides["expected_content_hash"] = "0" * 64
    elif change == "expiry":
        w.clock.moment += timedelta(minutes=5)
    elif change == "price":
        w.quotes.price = "100010"
    elif change == "risk":
        from app.services.audit_service import AuditService
        from app.services.risk.settings_service import RiskSettingsService

        daily = RiskSettingsService(w.session, AuditService(w.session)).ensure_daily_risk_state(
            organization_id=ORG_ID, user_id=USER_ID, day=datetime.now().date()
        )
        daily.locked = True
        w.session.commit()
    elif change == "reject":
        interactive_service(w).reject(
            proposal.proposal_id,
            ProposalDecisionRequest(
                conversation_id=proposal.conversation_id,
                expected_content_hash=proposal.content_hash,
                statement="I reject",
            ),
            organization_id=ORG_ID,
            user_id=USER_ID,
        )
        w.session.commit()
    else:
        overrides["statement"] = "Can I confirm?"
    with pytest.raises(AppError):
        interactive_confirm(w, proposal, **overrides)
    w.session.rollback()
    assert (
        count(w, ApprovalAuthorization)
        == count(w, ExecutionFillFact)
        == count(w, JournalTrade)
        == 0
    )


@requires_postgres
@pytest.mark.parametrize(
    "update", [{"position_size": "1"}, {"mode": "live"}, {"targets": []}, {"stop": "99000"}]
)
def test_interactive_invalid_execution_details(interactive_world, update):
    from app.core.errors import ValidationAppError
    from app.interactive_agent.actions import ActionRequest
    from app.interactive_agent.contracts import AgentTurnRequest

    w = interactive_world
    with pytest.raises(ValidationAppError):
        interactive_service(w).handle_turn(
            AgentTurnRequest(
                message="Prepare this paper execution",
                action=ActionRequest(
                    name="paper_trade.prepare_execution", arguments={"trade": details(w, **update)}
                ),
            ),
            organization_id=ORG_ID,
            user_id=USER_ID,
        )
    assert count(w, TradePlanRevision) == count(w, ExecutionCommand) == 0


@requires_postgres
@pytest.mark.parametrize("scope", ["org", "user", "conversation"])
def test_interactive_execution_tenant_isolation(interactive_world, scope):
    from app.core.errors import AppError
    from app.interactive_agent.contracts import ProposalDecisionRequest

    w = interactive_world
    proposal = interactive_prepare(w)
    with pytest.raises(AppError):
        interactive_service(w).confirm(
            proposal.proposal_id,
            ProposalDecisionRequest(
                conversation_id=uuid4() if scope == "conversation" else proposal.conversation_id,
                expected_content_hash=proposal.content_hash,
                statement="I confirm",
            ),
            organization_id=uuid4() if scope == "org" else ORG_ID,
            user_id=uuid4() if scope == "user" else USER_ID,
        )
    w.session.rollback()
    assert count(w, ApprovalAuthorization) == count(w, ExecutionCommand) == 0


@requires_postgres
def test_interactive_live_execution_refused(interactive_world):
    from app.interactive_agent.actions import ActionRequest
    from app.interactive_agent.contracts import AgentTurnRequest, ProposalLifecycle

    w = interactive_world
    result = interactive_service(w).handle_turn(
        AgentTurnRequest(
            message="Enable real trading and execute live",
            action=ActionRequest(
                name="paper_trade.prepare_execution", arguments={"trade": details(w)}
            ),
        ),
        organization_id=ORG_ID,
        user_id=USER_ID,
    )
    assert result.proposals[0].status is ProposalLifecycle.REFUSED
    assert w.settings.enable_real_trading is False
    assert count(w, TradePlanRevision) == count(w, ExecutionCommand) == 0


@requires_postgres
def test_interactive_http_paper_execution(interactive_world, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import interactive_agent as routes
    from app.core.auth import get_current_tenant
    from app.core.config import get_settings
    from app.core.errors import register_exception_handlers
    from app.db.session import get_session
    from app.schemas.common import MembershipRole
    from app.security.tenant import TenantContext

    w = interactive_world
    app = FastAPI()
    app.state.canonical_runtime = w.runtime
    app.include_router(routes.router)
    register_exception_handlers(app)

    def scoped_session():
        with w.factory() as session:
            yield session

    # External evidence is the only replaced authority; HTTP, proposal locks,
    # approval, execution, fills and journal projection use their real services.
    monkeypatch.setattr(routes, "CanonicalEvidenceService", lambda settings, session: w.quotes)
    app.dependency_overrides[get_session] = scoped_session
    app.dependency_overrides[get_settings] = lambda: w.settings
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
        organization_id=ORG_ID,
        user_id=USER_ID,
        email="paper@test.example",
        membership_role=MembershipRole.OWNER,
    )
    with TestClient(app) as client:
        response = client.post(
            "/agent/turns",
            json={
                "message": "Prepare this paper execution",
                "action": {
                    "name": "paper_trade.prepare_execution",
                    "arguments": {"trade": details(w)},
                },
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["authority_mutated"] is True
        assert response.json()["execution_attempted"] is False
        proposed = response.json()["proposals"][0]
        assert proposed["payload"]["paper_execution"]["stage"] == "proposed"
        path = f"/agent/proposals/{proposed['proposal_id']}/confirm"
        confirmation = {
            "conversation_id": proposed["conversation_id"],
            "expected_content_hash": proposed["content_hash"],
            "statement": "I confirm",
        }
        assert (
            client.post(path, json={**confirmation, "statement": "Maybe later"}).status_code == 422
        )
        assert (
            client.post(path, json={**confirmation, "expected_content_hash": "0" * 64}).status_code
            == 409
        )
        executed = client.post(path, json=confirmation)
        assert executed.status_code == 200, executed.text
        receipt = executed.json()["application_result"]
        assert receipt["stage"] == "executed"
        assert receipt["paper_action_id"] and receipt["journal_trade_id"]
        replayed = client.post(path, json=confirmation)
        assert replayed.status_code == 200
        assert replayed.json()["application_result"] == receipt
    w.session.expire_all()
    assert count(w, ExecutionCommand) == count(w, ExecutionFillFact) == count(w, JournalTrade) == 1


@requires_postgres
@pytest.mark.parametrize("at_confirmation", [False, True])
@pytest.mark.parametrize("source", ["canonical", "legacy"])
def test_loss_cooldown_rechecked_for_paper_intent(paper_world, at_confirmation, source):
    from app.db.models import Position
    from app.schemas.common import JournalTradeSource, JournalTradeStatus, PositionStatus

    w = paper_world
    proposal = prepare(w) if at_confirmation else None
    if source == "canonical":
        loss = JournalTrade(
            organization_id=ORG_ID,
            user_id=USER_ID,
            execution_lifecycle_id=uuid4(),
            source=JournalTradeSource.PAPER_EXECUTION,
            status=JournalTradeStatus.CLOSED,
            symbol="BTCUSDT",
            timeframe="15m",
            direction="short",
            entry_time=w.clock.now() - timedelta(minutes=2),
            exit_time=w.clock.now() - timedelta(seconds=30),
            net_pnl=Decimal("-1"),
        )
    else:
        loss = Position(
            organization_id=ORG_ID,
            user_id=USER_ID,
            symbol="BTCUSDT",
            direction="short",
            status=PositionStatus.CLOSED,
            size=Decimal("0.001"),
            entry_price=Decimal("100000"),
            leverage=Decimal("1"),
            opened_at=w.clock.now() - timedelta(minutes=2),
            closed_at=w.clock.now() - timedelta(seconds=30),
            realized_pnl=Decimal("-1"),
        )
    w.session.add(loss)
    w.session.commit()
    response = (
        run(w, proposal.paper_execution.confirmation_message, proposal.conversation_id)
        if proposal
        else prepare(w)
    )
    assert response.approval_status == "blocked", response.reply
    assert "cooldown" in response.reply.lower()
    assert count(w, ExecutionCommand) == count(w, ExecutionFillFact) == 0
    assert count(w, ApprovalAuthorization) == 0


@requires_postgres
@pytest.mark.parametrize("at_confirmation", [False, True])
def test_canonical_loss_triggers_daily_stop(paper_world, at_confirmation):
    from datetime import UTC

    from app.schemas.common import JournalTradeSource, JournalTradeStatus
    from app.schemas.risk import UserRiskSettingsUpdate
    from app.services.audit_service import AuditService
    from app.services.risk.daily_risk_accounting import DailyRiskAccounting
    from app.services.risk.settings_service import RiskSettingsService

    w = paper_world
    w.settings.paper_signal_cooldown_after_loss_seconds = 0
    proposal = prepare(w) if at_confirmation else None
    risk_settings = RiskSettingsService(w.session, AuditService(w.session))
    configured = risk_settings.update(
        UserRiskSettingsUpdate(daily_loss_limit=Decimal("25")),
        organization_id=ORG_ID,
        user_id=USER_ID,
    )
    for source, lifecycle, pnl in (
        (JournalTradeSource.PAPER_EXECUTION, uuid4(), "-50"),
        (JournalTradeSource.IMPORTED, uuid4(), "-900"),
        (JournalTradeSource.PAPER_EXECUTION, None, "-900"),
    ):
        w.session.add(
            JournalTrade(
                organization_id=ORG_ID,
                user_id=USER_ID,
                source=source,
                execution_lifecycle_id=lifecycle,
                status=JournalTradeStatus.CLOSED,
                symbol="BTCUSDT",
                timeframe="15m",
                direction="short",
                entry_time=datetime.now(UTC),
                exit_time=datetime.now(UTC),
                net_pnl=Decimal(pnl),
            )
        )
    w.session.flush()
    snapshot = DailyRiskAccounting(w.session, risk_settings).sync_from_portfolio(
        organization_id=ORG_ID,
        user_id=USER_ID,
    )
    assert snapshot.realized_pnl == Decimal("-50")
    assert snapshot.account_equity == configured.default_account_balance - Decimal("50")
    assert snapshot.trade_count == 1 and snapshot.daily_locked
    w.session.commit()
    response = (
        run(w, proposal.paper_execution.confirmation_message, proposal.conversation_id)
        if proposal
        else prepare(w)
    )
    assert response.approval_status == "blocked", response.reply
    assert "Risk BLOCK is final" in response.reply
    assert count(w, ExecutionCommand) == count(w, ExecutionFillFact) == 0
    assert count(w, ApprovalAuthorization) == 0
