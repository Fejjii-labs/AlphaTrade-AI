"""Phase 7 learning attribution: canonical lifecycle → learning facts.

JournalLifecycleProjector remains the JournalTrade authority. Attribution is
record-only. REJECT/SKIP never create executed outcomes.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import JournalProjectionConflictError, ValidationAppError
from app.db.models import JournalLifecycleEvent, JournalTrade
from app.learning_attribution.adapters import (
    lesson_suggestions,
    render_learning_evidence_text,
    rollup_organization,
)
from app.learning_attribution.contracts import (
    NARRATIVE_NOT_FACT_BANNER,
    DecisionActor,
    ExecutionQuality,
    PlannedSetupQuality,
    RiskAdherence,
    TraderBehavior,
)
from app.learning_attribution.errors import (
    CrossTenantAttributionError,
    ExecutedOutcomeForbiddenError,
    NarrativeCannotRewriteFactsError,
)
from app.learning_attribution.identity import attribution_id_for
from app.learning_attribution.memory import InMemoryAttributionStore
from app.learning_attribution.persistence import AGENT_1_ATTRIBUTION_INTEGRATION
from app.schemas.common import JournalLifecycleEventType, JournalTradeStatus, TradeResult
from app.services.journal_lifecycle_lineage import extract_lineage_map
from app.services.journal_lifecycle_projector import JournalLifecycleProjector
from app.signal_fusion.enums import CandidateState, SetupAssessmentState
from tests.support.learning_attribution import (
    ORG_ID,
    OTHER_ACCOUNT,
    OTHER_ORG,
    OTHER_USER,
    USER_ID,
    command_for,
    instrument_payload,
    learning_service,
    lifecycle_event,
    make_lineage,
)
from tests.support.phase6_fusion import CANDIDATE_ID

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src/app/learning_attribution"
FORBIDDEN_SNIPPETS = (
    "app.services.execution",
    "ExecutionService",
    "place_paper_order",
    "execute_paper_plan",
    "from alembic",
    "import alembic",
    "op.add_column",
    "WATCHER_ORCHESTRATION_ENABLED",
    "telegram_interaction_enabled",
)


def _count_trades(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(JournalTrade)) or 0)


def test_package_is_not_an_execution_authority() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for snippet in FORBIDDEN_SNIPPETS:
            assert snippet not in text, f"{path} contains forbidden {snippet}"


def test_reject_does_not_create_journal_trade_or_executed_outcome(
    attribution_sessions: sessionmaker[Session],
) -> None:
    store = InMemoryAttributionStore()
    with attribution_sessions() as session:
        service = learning_service(session, store)
        lineage = make_lineage()
        result = service.apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.REJECT, source_event_id="rej-1"),
                lineage,
            )
        )
        session.commit()
        assert result.journal_trade_id is None
        assert result.executed_trade_outcome is False
        assert result.record.facts.outcome.eligible is False
        assert result.record.facts.trader_behavior.axis is TraderBehavior.REJECTED
        assert result.record.facts.setup_quality.axis is PlannedSetupQuality.CONFIRMED
        assert _count_trades(session) == 0
        assert session.scalar(select(func.count()).select_from(JournalLifecycleEvent)) == 1


def test_skip_does_not_create_journal_trade_or_executed_outcome(
    attribution_sessions: sessionmaker[Session],
) -> None:
    with attribution_sessions() as session:
        result = learning_service(session).apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.SKIP, source_event_id="skip-1"),
                make_lineage(),
            )
        )
        session.commit()
        assert result.executed_trade_outcome is False
        assert result.record.facts.strategy_pattern.skipped is True
        assert result.record.facts.strategy_pattern.executed_outcome is False
        assert _count_trades(session) == 0


def test_one_lifecycle_converges_to_one_journal_trade(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    store = InMemoryAttributionStore()
    with attribution_sessions() as session:
        service = learning_service(session, store)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        plan = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.APPROVED_PLAN,
                    source_event_id="plan-1",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(),
                ),
                lineage,
            )
        )
        fill = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.FILL,
                    source_event_id="fill-1",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(entry_price="64010", size="1"),
                ),
                lineage,
            )
        )
        close = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.CLOSE,
                    source_event_id="close-1",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(
                        exit_price="63000",
                        net_pnl="900",
                        result=TradeResult.WIN.value,
                        realized_vs_available_pct="80",
                    ),
                ),
                lineage,
            )
        )
        session.commit()
        assert plan.journal_trade_id is not None
        assert fill.journal_trade_id == plan.journal_trade_id
        assert close.journal_trade_id == plan.journal_trade_id
        assert _count_trades(session) == 1
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.execution_lifecycle_id == lifecycle_id
        assert trade.status is JournalTradeStatus.CLOSED
        assert close.executed_trade_outcome is True
        assert close.record.facts.setup_quality.axis is PlannedSetupQuality.CONFIRMED
        assert close.record.facts.execution_quality.axis is ExecutionQuality.MATCHED_PLAN
        assert close.record.facts.risk_adherence.axis is RiskAdherence.ADHERED
        assert close.record.facts.trader_behavior.axis is TraderBehavior.EXECUTED
        assert close.record.facts.human_vs_system.decision_actor is (
            DecisionActor.PAPER_SYSTEM_EXECUTION
        )
        events = list(session.scalars(select(JournalLifecycleEvent)))
        assert events
        assert any(
            extract_lineage_map(row.payload).get("candidate_id") == str(CANDIDATE_ID)
            for row in events
        )


def test_duplicate_events_converge(attribution_sessions: sessionmaker[Session]) -> None:
    lifecycle_id = uuid4()
    store = InMemoryAttributionStore()
    with attribution_sessions() as session:
        service = learning_service(session, store)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        event = lifecycle_event(
            JournalLifecycleEventType.APPROVED_PLAN,
            source_event_id="plan-dup",
            execution_lifecycle_id=lifecycle_id,
            payload=instrument_payload(),
        )
        first = service.apply(command_for(event, lineage))
        second = service.apply(command_for(event, lineage))
        session.commit()
        assert second.replayed is True
        assert second.record.attribution_id == first.record.attribution_id
        assert second.facts_hash == first.facts_hash
        assert _count_trades(session) == 1
        assert len(store.get(organization_id=ORG_ID, candidate_id=CANDIDATE_ID).events) == 1  # type: ignore[union-attr]


def test_conflicting_source_identity_fails_closed(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        service = learning_service(session)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.APPROVED_PLAN,
                    source_event_id="plan-conflict",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(planned_entry_price="64000"),
                ),
                lineage,
            )
        )
        session.commit()
        with pytest.raises(JournalProjectionConflictError):
            service.apply(
                command_for(
                    lifecycle_event(
                        JournalLifecycleEventType.APPROVED_PLAN,
                        source_event_id="plan-conflict",
                        execution_lifecycle_id=lifecycle_id,
                        payload=instrument_payload(planned_entry_price="1"),
                    ),
                    lineage,
                )
            )


def test_conflicting_candidate_lineage_fails_closed(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        service = learning_service(session)
        first = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.APPROVED_PLAN,
                    source_event_id="plan-a",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(),
                ),
                first,
            )
        )
        session.commit()
        other = make_lineage(
            candidate_id=uuid4(),
            include_plan=True,
            execution_lifecycle_id=lifecycle_id,
        )
        with pytest.raises(JournalProjectionConflictError, match="lineage"):
            service.apply(
                command_for(
                    lifecycle_event(
                        JournalLifecycleEventType.FILL,
                        source_event_id="fill-b",
                        execution_lifecycle_id=lifecycle_id,
                        payload=instrument_payload(entry_price="64000"),
                    ),
                    other,
                )
            )


def test_cross_tenant_attribution_fails_closed(
    attribution_sessions: sessionmaker[Session],
) -> None:
    with attribution_sessions() as session:
        service = learning_service(session)
        lineage = make_lineage()
        with pytest.raises(CrossTenantAttributionError):
            service.apply(
                command_for(
                    lifecycle_event(
                        JournalLifecycleEventType.REJECT,
                        source_event_id="rej-x",
                        account_id=OTHER_ACCOUNT,
                    ),
                    lineage,
                    organization_id=OTHER_ORG,
                    user_id=OTHER_USER,
                )
            )


def test_projector_lineage_org_mismatch_fails_closed(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        from app.services.audit_service import AuditService

        projector = JournalLifecycleProjector(session, AuditService(session, strict_mode=True))
        with pytest.raises(JournalProjectionConflictError, match="organization"):
            projector.project(
                lifecycle_event(
                    JournalLifecycleEventType.APPROVED_PLAN,
                    source_event_id="plan-org",
                    execution_lifecycle_id=lifecycle_id,
                    payload={
                        **instrument_payload(),
                        "lineage": {"organization_id": str(OTHER_ORG)},
                    },
                ),
                organization_id=ORG_ID,
                user_id=USER_ID,
            )


def test_reject_and_skip_never_enter_executed_analytics(
    attribution_sessions: sessionmaker[Session],
) -> None:
    store = InMemoryAttributionStore()
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        service = learning_service(session, store)
        service.apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.REJECT, source_event_id="rej-stats"),
                make_lineage(candidate_id=uuid4()),
            )
        )
        service.apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.SKIP, source_event_id="skip-stats"),
                make_lineage(candidate_id=uuid4()),
            )
        )
        filled_lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.FILL,
                    source_event_id="fill-stats",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(
                        entry_price="64000",
                        net_pnl="-10",
                        result=TradeResult.LOSS.value,
                    ),
                ),
                filled_lineage,
            )
        )
        session.commit()
        snapshot = rollup_organization(store, organization_id=ORG_ID)
        assert snapshot.human_vs_system.human_reject_or_skip == 2
        assert snapshot.human_vs_system.executed_outcomes == 1
        assert sum(item.rejected_count for item in snapshot.patterns) == 1
        assert sum(item.skipped_count for item in snapshot.patterns) == 1
        assert sum(item.loss_count for item in snapshot.patterns) == 1
        assert sum(item.executed_outcome_count for item in snapshot.patterns) == 1


def test_outcome_does_not_rewrite_setup_quality(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        service = learning_service(session)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        confirmed_hash = lineage.assessment.content_hash
        window_hash = lineage.assessment.evidence_window_hash
        result = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.CLOSE,
                    source_event_id="close-loss",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(
                        entry_price="64000",
                        exit_price="65000",
                        net_pnl="-800",
                        result=TradeResult.LOSS.value,
                    ),
                ),
                lineage,
            )
        )
        session.commit()
        assert lineage.assessment.content_hash == confirmed_hash
        assert lineage.assessment.evidence_window_hash == window_hash
        assert result.record.facts.setup_quality.assessment_content_hash == confirmed_hash
        assert result.record.facts.setup_quality.evidence_window_hash == window_hash
        assert result.record.facts.setup_quality.assessment_state is (
            SetupAssessmentState.CONFIRMED_SETUP
        )
        assert result.record.facts.outcome.result is TradeResult.LOSS
        assert result.record.facts.human_vs_system.setup_quality_axis is (
            PlannedSetupQuality.CONFIRMED
        )
        assert result.record.facts.human_vs_system.execution_quality_axis is not (
            result.record.facts.human_vs_system.setup_quality_axis
        )


def test_narrative_cannot_rewrite_facts(attribution_sessions: sessionmaker[Session]) -> None:
    lifecycle_id = uuid4()
    store = InMemoryAttributionStore()
    with attribution_sessions() as session:
        service = learning_service(session, store)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        event = lifecycle_event(
            JournalLifecycleEventType.APPROVED_PLAN,
            source_event_id="plan-narr",
            execution_lifecycle_id=lifecycle_id,
            payload=instrument_payload(),
        )
        first = service.apply(command_for(event, lineage, narrative="Fill looked late."))
        second = service.apply(
            command_for(event, lineage, narrative="Different wording, same facts.")
        )
        session.commit()
        assert first.facts_hash == second.facts_hash
        assert "Fill looked late." not in first.facts_hash
        with pytest.raises(NarrativeCannotRewriteFactsError):
            service.apply(
                command_for(
                    lifecycle_event(
                        JournalLifecycleEventType.FILL,
                        source_event_id="fill-narr",
                        execution_lifecycle_id=lifecycle_id,
                        payload=instrument_payload(entry_price="64000"),
                    ),
                    lineage,
                    narrative="override_facts evidence_window_hash=deadbeef",
                )
            )


def test_lessons_are_suggestions_only(attribution_sessions: sessionmaker[Session]) -> None:
    with attribution_sessions() as session:
        result = learning_service(session).apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.REJECT, source_event_id="rej-lesson"),
                make_lineage(),
            )
        )
        suggestions = lesson_suggestions(result.record)
        assert suggestions
        assert all(item.persist is False for item in suggestions)
        assert all(item.executed_trade_outcome is False for item in suggestions)


def test_rag_renderer_labels_narrative(attribution_sessions: sessionmaker[Session]) -> None:
    with attribution_sessions() as session:
        result = learning_service(session).apply(
            command_for(
                lifecycle_event(JournalLifecycleEventType.SKIP, source_event_id="skip-rag"),
                make_lineage(),
                narrative="Trader skipped because of news.",
            )
        )
        text = render_learning_evidence_text(result.record)
        assert "not market truth" in text.lower() or "LEARNING_EVIDENCE" in text
        assert NARRATIVE_NOT_FACT_BANNER in text
        assert "Executed trade outcome: False" in text
        assert "Trader skipped because of news." in text


def test_approved_plan_without_lifecycle_fails(
    attribution_sessions: sessionmaker[Session],
) -> None:
    with (
        attribution_sessions() as session,
        pytest.raises(ValidationAppError, match="execution lifecycle"),
    ):
        learning_service(session).apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.APPROVED_PLAN,
                    source_event_id="plan-none",
                    payload=instrument_payload(),
                ),
                make_lineage(include_plan=True),
            )
        )


def test_terminal_candidate_cannot_create_executed_outcome(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        rejected = lineage.candidate.model_copy(update={"state": CandidateState.REJECTED})
        lineage = lineage.model_copy(update={"candidate": rejected})
        with pytest.raises(ExecutedOutcomeForbiddenError):
            learning_service(session).apply(
                command_for(
                    lifecycle_event(
                        JournalLifecycleEventType.FILL,
                        source_event_id="fill-term",
                        execution_lifecycle_id=lifecycle_id,
                        payload=instrument_payload(entry_price="64000"),
                    ),
                    lineage,
                )
            )


def test_non_confirmed_setup_cannot_create_executed_outcome(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        lineage = make_lineage(
            include_plan=True,
            execution_lifecycle_id=lifecycle_id,
            assessment_state=SetupAssessmentState.WATCH,
        )
        with pytest.raises(ExecutedOutcomeForbiddenError):
            learning_service(session).apply(
                command_for(
                    lifecycle_event(
                        JournalLifecycleEventType.APPROVED_PLAN,
                        source_event_id="plan-watch",
                        execution_lifecycle_id=lifecycle_id,
                        payload=instrument_payload(),
                    ),
                    lineage,
                )
            )


def test_attribution_id_is_deterministic() -> None:
    first = attribution_id_for(organization_id=ORG_ID, candidate_id=CANDIDATE_ID)
    second = attribution_id_for(organization_id=ORG_ID, candidate_id=CANDIDATE_ID)
    other = attribution_id_for(organization_id=OTHER_ORG, candidate_id=CANDIDATE_ID)
    assert first == second
    assert first != other


def test_agent_1_persistence_contract_is_explicit() -> None:
    spec = AGENT_1_ATTRIBUTION_INTEGRATION
    assert spec.owner == "agent_1_alembic_orm"
    names = {column.name for column in spec.optional_journal_trade_columns}
    assert "candidate_id" in names
    assert "evidence_window_hash" in names
    assert "reuse" in spec.reuse_now.lower() or "JournalLifecycleProjector" in spec.reuse_now


def test_early_exit_is_execution_quality_not_setup_quality(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        result = learning_service(session).apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.CLOSE,
                    source_event_id="close-early",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(
                        entry_price="64000",
                        exit_price="63800",
                        net_pnl="200",
                        result=TradeResult.WIN.value,
                        realized_vs_available_pct="20",
                    ),
                ),
                lineage,
            )
        )
        session.commit()
        assert result.record.facts.execution_quality.axis is ExecutionQuality.EARLY_EXIT
        assert result.record.facts.risk_adherence.axis is RiskAdherence.ADHERED
        assert result.record.facts.setup_quality.axis is PlannedSetupQuality.CONFIRMED
        suggestions = lesson_suggestions(result.record)
        assert any(item.mistake_type == "early_exit" for item in suggestions)


def test_candidate_confirmed_is_not_an_executed_outcome(
    attribution_sessions: sessionmaker[Session],
) -> None:
    with attribution_sessions() as session:
        result = learning_service(session).apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.CANDIDATE_CONFIRMED,
                    source_event_id="cand-1",
                ),
                make_lineage(),
            )
        )
        session.commit()
        assert result.executed_trade_outcome is False
        assert result.journal_trade_id is None
        assert result.record.facts.trader_behavior.actor is DecisionActor.SYSTEM_SETUP
        assert _count_trades(session) == 0


def test_autonomous_approval_is_excluded_from_human_approval_counts(
    attribution_sessions: sessionmaker[Session],
) -> None:
    from app.learning_attribution.adapters.analytics import rollup_organization
    from app.paper_evaluation.metrics import _human_facts

    lifecycle_id = uuid4()
    store = InMemoryAttributionStore()
    with attribution_sessions() as session:
        service = learning_service(session, store)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        plan = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.APPROVED_PLAN,
                    source_event_id="auto-plan",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(approval_channel="AUTONOMOUS"),
                ),
                lineage,
            )
        )
        assert plan.record.facts.trader_behavior.actor is DecisionActor.AUTONOMOUS_APPROVAL
        assert plan.record.facts.trader_behavior.axis is TraderBehavior.AUTONOMOUS_APPROVAL
        assert plan.record.facts.human_vs_system.decision_actor is (
            DecisionActor.AUTONOMOUS_APPROVAL
        )
        assert plan.record.facts.human_vs_system.trader_behavior_axis is (
            TraderBehavior.AUTONOMOUS_APPROVAL
        )
        assert plan.record.facts.strategy_pattern.plan_approved is False
        assert plan.record.facts.strategy_pattern.autonomous_approval is True
        service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.FILL,
                    source_event_id="auto-fill",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(
                        entry_price="64010",
                        size="1",
                        approval_channel="AUTONOMOUS",
                    ),
                ),
                lineage,
            )
        )
        close = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.CLOSE,
                    source_event_id="auto-close",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(
                        exit_price="63000",
                        net_pnl="900",
                        result=TradeResult.WIN.value,
                        realized_vs_available_pct="80",
                        approval_channel="AUTONOMOUS",
                    ),
                ),
                lineage,
            )
        )
        session.commit()
        pattern = close.record.facts.strategy_pattern
        assert pattern.plan_approved is False
        assert pattern.filled is True
        assert pattern.win is True
        assert close.record.facts.outcome.eligible is True
        assert close.record.facts.human_vs_system.decision_actor is not (
            DecisionActor.HUMAN_APPROVAL
        )
        human = _human_facts((close.record,), SimpleNamespace(confirmed_setup_count=0))
        assert human.human_approvals == 0
        assert human.human_approved_executed_wins == 0
        assert human.human_approved_executed_losses == 0
        assert human.paper_system_executions == 1
    snapshot = rollup_organization(store, organization_id=ORG_ID)
    assert snapshot.human_vs_system.human_approvals == 0
    assert snapshot.human_vs_system.human_approved_executed_wins == 0
    assert snapshot.patterns[0].plan_approved_count == 0
    assert snapshot.patterns[0].win_count == 1


def test_missing_approval_channel_remains_a_human_approval(
    attribution_sessions: sessionmaker[Session],
) -> None:
    lifecycle_id = uuid4()
    with attribution_sessions() as session:
        service = learning_service(session)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        plan = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.APPROVED_PLAN,
                    source_event_id="human-plan",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(),
                ),
                lineage,
            )
        )
        assert plan.record.facts.trader_behavior.actor is DecisionActor.HUMAN_APPROVAL
        assert plan.record.facts.trader_behavior.axis is TraderBehavior.APPROVED_PLAN
        assert plan.record.facts.strategy_pattern.plan_approved is True
        assert plan.record.facts.strategy_pattern.autonomous_approval is False
        fill = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.FILL,
                    source_event_id="human-fill",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(entry_price="64010", size="1"),
                ),
                lineage,
            )
        )
        session.commit()
        assert fill.record.facts.strategy_pattern.plan_approved is True


def test_unstamped_close_cannot_reclassify_an_autonomous_approval(
    attribution_sessions: sessionmaker[Session],
) -> None:
    """A later close that the test does not stamp must not become a human approval.

    The production demo close now copies the authorization channel. This test
    leaves the fill and close payloads without ``approval_channel`` so the
    sticky fold, not a hand-set channel, is what keeps the counts at zero.
    """

    from app.learning_attribution.adapters.analytics import rollup_organization
    from app.paper_evaluation.metrics import _human_facts

    lifecycle_id = uuid4()
    store = InMemoryAttributionStore()
    with attribution_sessions() as session:
        service = learning_service(session, store)
        lineage = make_lineage(include_plan=True, execution_lifecycle_id=lifecycle_id)
        plan = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.APPROVED_PLAN,
                    source_event_id="sticky-plan",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(approval_channel="AUTONOMOUS"),
                ),
                lineage,
            )
        )
        assert plan.record.facts.trader_behavior.axis is TraderBehavior.AUTONOMOUS_APPROVAL
        service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.FILL,
                    source_event_id="sticky-fill",
                    execution_lifecycle_id=lifecycle_id,
                    payload=instrument_payload(entry_price="64010", size="1"),
                ),
                lineage,
            )
        )
        close_payload = instrument_payload(
            exit_price="63000",
            net_pnl="-900",
            result=TradeResult.LOSS.value,
            realized_vs_available_pct="80",
        )
        assert "approval_channel" not in close_payload
        close = service.apply(
            command_for(
                lifecycle_event(
                    JournalLifecycleEventType.CLOSE,
                    source_event_id="sticky-close",
                    execution_lifecycle_id=lifecycle_id,
                    payload=close_payload,
                ),
                lineage,
            )
        )
        session.commit()
        pattern = close.record.facts.strategy_pattern
        assert pattern.autonomous_approval is True
        assert pattern.plan_approved is False
        assert pattern.closed is True
        assert pattern.loss is True
        human = _human_facts((close.record,), SimpleNamespace(confirmed_setup_count=0))
        assert human.human_approvals == 0
        assert human.human_approved_executed_wins == 0
        assert human.human_approved_executed_losses == 0
    snapshot = rollup_organization(store, organization_id=ORG_ID)
    assert snapshot.human_vs_system.human_approvals == 0
    assert snapshot.human_vs_system.human_approved_executed_wins == 0
    assert snapshot.human_vs_system.human_approved_executed_losses == 0
