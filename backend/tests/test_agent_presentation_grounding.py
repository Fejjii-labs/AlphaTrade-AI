"""Model prose and evidence remain separate from canonical strategy authority."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.db.models import ConversationMessage
from app.interactive_agent.action_registry import resolve_action, route_action
from app.interactive_agent.contracts import AgentTurnRequest
from app.interactive_agent.conversation import ModelConversationalResponder, compose_visible_reply
from app.interactive_agent.paper_execution_explanation import _demo_explanation
from app.interactive_agent.retrieval import retrieve_strategies
from app.interactive_agent.service import InteractiveAgentService
from app.schemas.common import StrategyChangeSource, StrategyLifecycleState, TradeDirection
from app.schemas.nested_continuation import NestedContinuationSpec
from app.schemas.trade_plan import EntrySide
from app.services.compiled_setup_service import CompiledSetupService
from app.services.strategy_versioning import StrategyVersioningService
from app.strategy_brain.agent import read_brain
from app.strategy_brain.service import create_template, overview
from tests.test_interactive_agent_foundation import ORG_A, ORG_B, USER_A, USER_A2, USER_B
from tests.test_interactive_agent_foundation import agent_db as agent_db
from tests.test_sfp_detector import spec


@pytest.mark.parametrize("prefix", ["", "command="])
def test_execution_explanation_routes_both_explicit_id_forms_to_durable_read(prefix):
    command_id = uuid4()
    action = route_action(AgentTurnRequest(message=f"Explain paper execution {prefix}{command_id}"))
    assert action is not None
    tool, arguments = resolve_action(action)
    assert tool.name == "paper_trade.explain_execution"
    assert tool.behavior == "read"
    assert arguments.command_id == command_id


@pytest.mark.parametrize("length", [4000, 8000, 15000])
def test_long_evidence_preserves_useful_prose_and_late_warnings(length):
    explanation = "Conclusion: wait. " + "A material blocker remains. " * 45 + "Next: review risk."
    evidence = "Evidence reference " * (length // 19) + "\nCanonical perpetual evidence is stale."
    reply = compose_visible_reply(explanation, evidence)
    assert reply.startswith(explanation)
    assert len(reply) <= 4000
    assert "Stored evidence is stale" in reply.split("Recorded facts")[0]
    assert "Evidence excerpt" in reply


def test_model_context_keeps_both_families_after_a_long_user_message(agent_db, monkeypatch):
    factory, settings = agent_db
    captured = []

    class Router:
        def complete(self, request, messages):
            captured.extend(messages)
            return SimpleNamespace(
                content="Both families need confirmed setup evidence.",
                unavailable=False,
                mutation_allowed=False,
                fallback_used=False,
                resolved_model="simulated-model",
                decision=SimpleNamespace(selected_model="simulated-model"),
                input_tokens=0,
                output_tokens=0,
                total_latency_ms=0.0,
                total_cost=Decimal("0"),
                cost_source=SimpleNamespace(value="unavailable"),
            )

    monkeypatch.setattr(
        "app.interactive_agent.conversation.resolve_providers",
        lambda _: SimpleNamespace(llm=object()),
    )
    monkeypatch.setattr(
        "app.interactive_agent.conversation.ModelRouter.from_settings",
        lambda *args, **kwargs: Router(),
    )
    facts = "Nested stored rules. " * 350 + "SFP stored rules: selected lifecycle=approved."
    with factory() as session:
        reply = ModelConversationalResponder(session, settings).compose(
            organization_id=ORG_A,
            user_id=USER_A,
            conversation_id=ORG_A,
            message="Long comparison question. " * 300,
            factual_context=facts,
        )
    assert reply.startswith("Both families")
    assert captured[1].content.endswith(facts)
    assert "Research validation" in captured[0].content


def _approved(session, *, sfp=False, short=False, organization_id=ORG_A, user_id=USER_A):
    authored = (
        spec(bearish=short)
        if sfp
        else NestedContinuationSpec(
            symbol="BTCUSDT", direction=TradeDirection.SHORT if short else TradeDirection.LONG
        )
    )
    template = create_template(
        session, organization_id=organization_id, user_id=user_id, spec=authored
    )
    compiler = CompiledSetupService(session)
    compiler.compile_version(
        template["version_id"], organization_id=organization_id, user_id=user_id
    )
    compiler.approve_version(
        template["version_id"],
        organization_id=organization_id,
        user_id=user_id,
        confirm_message="I confirm",
    )
    return template


def test_comparison_grounding_has_both_families_scopes_rules_and_distinct_states(agent_db):
    factory, settings = agent_db
    captured = []

    class Responder:
        def compose(self, **kwargs):
            captured.append(kwargs["factual_context"])
            return "Both families are approved for evaluation. Execution gates still apply."

    with factory() as session:
        for sfp in (False, True):
            for short in (False, True):
                _approved(session, sfp=sfp, short=short)
        session.commit()
        result = InteractiveAgentService(
            session, settings=settings, responder=Responder()
        ).handle_turn(
            AgentTurnRequest(message="Compare my approved Nested and SFP strategies on BTCUSDT"),
            organization_id=ORG_A,
            user_id=USER_A,
        )
        facts = captured[0]
        assert "NESTED stored evidence" in facts and "SFP stored evidence" in facts
        for scope in ("BTCUSDT long 15m", "BTCUSDT short 15m"):
            assert facts.count(scope) >= 2
        assert facts.count("lifecycle=approved") >= 4
        assert "research_validation=draft" in facts
        assert "closed_break_of_impulse_extreme" in facts
        assert "closed_break_of_reclaim_extreme" in facts
        assert "No SFP execution plan" in facts
        assert "Current market conditions are unknown" in facts
        assert "not execution eligibility" in facts
        assert result.recorded_evidence == facts
        assert len(result.reply) <= 4000
        assistant = session.get(ConversationMessage, result.assistant_message_id)
        assert assistant.payload["interactive_agent"]["recorded_evidence"] == facts
        assert not result.authority_mutated and not result.execution_attempted
        assert not result.proposals


def test_approval_uses_latest_event_of_selected_version(agent_db):
    factory, _ = agent_db
    with factory() as session:
        template = _approved(session)
        versioning = StrategyVersioningService(session)
        strategy = versioning.require_strategy(template["strategy_id"], organization_id=ORG_A)
        parent = versioning.selected_version(strategy)
        versioning.fork_semantic_update(
            strategy,
            parent=parent,
            card={**parent.card, "entry_conditions": ["A newly authored entry rule"]},
            structured_rules=None,
            lesson_source_metadata=None,
            actor_user_id=USER_A,
            source=StrategyChangeSource.CARD_UPDATE,
            reason="New draft",
        )
        hits, _ = retrieve_strategies(
            session,
            organization_id=ORG_A,
            user_id=USER_A,
            query="Nested",
            list_all=True,
            strategy_id=None,
        )
        assert hits[0].selected_version_id != template["version_id"]
        assert hits[0].lifecycle_status == "draft"
        assert "lifecycle=draft" in hits[0].summary
        assert overview(session, organization_id=ORG_A)["strategies"][0]["approved_by"] is None
        selected = versioning.selected_version(strategy)
        versioning.append_lifecycle(
            organization_id=ORG_A,
            strategy_id=strategy.id,
            strategy_version_id=selected.id,
            new_state=StrategyLifecycleState.APPROVED,
            actor_user_id=USER_A,
            reason="test approval",
        )
        versioning.append_lifecycle(
            organization_id=ORG_A,
            strategy_id=strategy.id,
            strategy_version_id=selected.id,
            new_state=StrategyLifecycleState.PAUSED,
            actor_user_id=USER_A,
            reason="test pause",
        )
        hits, _ = retrieve_strategies(
            session,
            organization_id=ORG_A,
            user_id=USER_A,
            query="Nested",
            list_all=True,
            strategy_id=None,
        )
        assert hits[0].lifecycle_status == "paused"
        assert overview(session, organization_id=ORG_A)["strategies"][0]["approved_by"] is None


def test_grounded_definitions_respect_tenant_and_library_owner(agent_db):
    factory, _ = agent_db
    with factory() as session:
        own = _approved(session)
        colleague = _approved(session, sfp=True, user_id=USER_A2)
        foreign = _approved(session, organization_id=ORG_B, user_id=USER_B)
        text, refs, _ = read_brain(
            session,
            organization_id=ORG_A,
            user_id=USER_A,
            message="Compare Nested and SFP definitions",
            symbol="BTCUSDT",
        )
        assert str(own["version_id"]) in text
        assert str(colleague["version_id"]) not in text
        assert str(foreign["version_id"]) not in text
        assert {UUID(ref.record_id) for ref in refs} == {own["strategy_id"]}


def test_library_retrieval_does_not_let_nested_variants_crowd_out_sfp(agent_db):
    factory, _ = agent_db
    with factory() as session:
        _approved(session, sfp=True)
        _approved(session)
        _approved(session, short=True)
        hits, _ = retrieve_strategies(
            session,
            organization_id=ORG_A,
            user_id=USER_A,
            query="Compare Nested and SFP",
            list_all=True,
            strategy_id=None,
            limit=2,
        )
        assert {hit.setup_type for hit in hits} == {"nested_continuation", "sfp"}


@pytest.mark.parametrize("filled,protection", [(False, None), (True, False), (True, True)])
def test_demo_explanation_separates_authorization_fill_and_recorded_protection(filled, protection):
    revision, command_id = uuid4(), uuid4()
    command = SimpleNamespace(
        id=command_id,
        revision_id=revision,
        plan_content_hash="a" * 64,
        organization_id=ORG_A,
        user_id=USER_A,
        account_id=uuid4(),
        authorization_id=uuid4(),
    )
    plan = SimpleNamespace(
        revision_id=revision,
        content_hash="a" * 64,
        strategy_version_id=uuid4(),
        side=EntrySide.BUY,
        execution_instrument="BTC-USDT",
        quantity=SimpleNamespace(value=Decimal("3"), unit="CONTRACTS"),
        risk_and_exits=SimpleNamespace(
            stop=SimpleNamespace(value=Decimal("90")),
            maximum_loss=SimpleNamespace(value=Decimal("30")),
        ),
    )
    fill_id, event_id, candidate_id = uuid4(), uuid4(), uuid4()
    fill = SimpleNamespace(
        id=fill_id,
        quantity=Decimal("3"),
        price=Decimal("100.5"),
        occurred_at=datetime(2026, 10, 5, tzinfo=UTC),
        source_fill_identity="venue-order:venue-trade",
    )
    event = SimpleNamespace(
        id=event_id, payload={"demo_protection": "verified" if protection else "missing"}
    )
    journal = (
        SimpleNamespace(
            id=uuid4(),
            status=SimpleNamespace(value="OPEN"),
            fees=Decimal("0.02"),
            net_pnl=None,
            candidate_id=candidate_id,
            trade_plan_revision_id=revision,
            account_id=command.account_id,
        )
        if filled
        else None
    )

    class Store:
        def __init__(self):
            self.results = iter(([fill] if filled else [], [event] if filled else []))
            self.records = iter(
                [
                    SimpleNamespace(
                        id=uuid4(),
                        reconciliation_disposition="DEMO_PROTECTED"
                        if protection
                        else "DEMO_PROTECTION_MISSING",
                    )
                    if filled
                    else None,
                    SimpleNamespace(
                        id=uuid4(),
                        learning_venue_mode="paper_exchange_demo",
                        filled=True,
                        closed=False,
                        journal_trade_id=journal.id,
                    )
                    if filled
                    else None,
                    None,  # No recorded demo exit lifecycle resolution in this entry fixture.
                ]
            )

        def scalars(self, statement):
            return next(self.results)

        def scalar(self, statement):
            return next(self.records)

    result = _demo_explanation(
        Store(),
        command=command,
        plan=plan,
        receipt=SimpleNamespace(receipt_id=uuid4(), outcome=SimpleNamespace(value="ALLOW")),
        projection=SimpleNamespace(state=SimpleNamespace(value="ACKNOWLEDGED")),
        journal=journal,
        candidate_id=candidate_id,
        eligibility_id=uuid4(),
    )
    assert result.source_message_id is None
    assert "internal paper" not in result.reply
    assert "Authorization is not a fill" in result.recorded_evidence
    assert "contact the venue" in result.recorded_evidence
    if filled:
        assert "actual exchange fill evidence" in result.reply
        assert "venue-order:venue-trade" in result.recorded_evidence
        assert "closed=False" in result.recorded_evidence
        assert str(fill_id) in {ref.record_id for ref in result.connections}
        assert (
            "verified at reconciliation" in result.reply
            if protection
            else "missing at reconciliation" in result.reply
        )
    else:
        assert "no recorded exchange fill evidence" in result.reply
        assert "Journal unavailable" in result.recorded_evidence
