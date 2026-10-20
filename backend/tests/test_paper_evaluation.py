"""Continuous paper evaluation: measurement only, not a trading authority."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.learning_attribution.contracts import NARRATIVE_NOT_FACT_BANNER, RiskAdherence
from app.learning_attribution.memory import InMemoryAttributionStore
from app.paper_evaluation.contracts import (
    DataQualityClass,
    PaperEvaluationObservation,
    PaperEvaluationStage,
)
from app.paper_evaluation.errors import RefinementActivationForbiddenError
from app.paper_evaluation.hashing import hashed_observation
from app.paper_evaluation.memory import InMemoryPaperEvaluationStore
from app.paper_evaluation.metrics import PaperEvaluationInput, rollup_facts
from app.paper_evaluation.query import PaperEvaluationQueryService
from app.paper_evaluation.recorder import PaperEvaluationRecorder
from app.paper_evaluation.refinement import refinement_suggestions, refuse_activation
from app.persistence.paper_evaluation_postgres import PostgresPaperEvaluationStore
from app.schemas.journal_statistics import SampleConfidence
from app.services.canonical_reads import CanonicalReadService
from app.signal_fusion.enums import EligibilityReasonCode, SetupAssessmentState
from app.watcher.contracts import EvaluationStatus
from app.watcher.fusion_evaluation import (
    InMemoryWatcherScanEvidence,
    build_fusion_evaluation_service,
)
from tests.support.learning_attribution import OTHER_ORG
from tests.support.paper_evaluation import (
    GENERATED_AT,
    HASH,
    OTHER_STRATEGY_VERSION_ID,
    blocked_evaluation,
    closed_loss,
    closed_win,
    journal_measurement,
    make_assessment_observation,
    make_attribution_record,
    make_eval_command,
    make_eval_outcome,
    make_observation,
    make_scan,
)
from tests.support.phase6_fusion import CANDIDATE_ID, ORG_ID, STRATEGY_VERSION_ID
from tests.support.phase7_trade_plan import make_world

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src/app/paper_evaluation"
PERSISTENCE_MODULE = (
    Path(__file__).resolve().parents[1] / "src/app/persistence/paper_evaluation_postgres.py"
)
FORBIDDEN_SNIPPETS = (
    "app.services.execution",
    "ExecutionService",
    "place_paper_order",
    "execute_paper_plan",
    "WATCHER_ORCHESTRATION_ENABLED",
    "from alembic",
    "import alembic",
    "ENABLE_REAL_TRADING=True",
    "telegram_interaction_enabled=True",
)


def test_package_is_not_a_trading_or_activation_authority() -> None:
    for path in (*PACKAGE_ROOT.rglob("*.py"), PERSISTENCE_MODULE):
        text = path.read_text(encoding="utf-8")
        for snippet in FORBIDDEN_SNIPPETS:
            assert snippet not in text, f"{path} contains forbidden {snippet}"


def test_narrative_is_excluded_from_observation_hash() -> None:
    first = make_observation(narrative_explanation="Wording A")
    second = hashed_observation(
        first.model_copy(update={"narrative_explanation": "Wording B", "content_hash": "0" * 64})
    )
    assert first.content_hash == second.content_hash
    assert "Wording A" not in first.content_hash


def test_generated_at_is_excluded_from_facts_hash() -> None:
    later = GENERATED_AT + timedelta(hours=1)
    first = rollup_facts(
        PaperEvaluationInput(organization_id=ORG_ID, generated_at=GENERATED_AT, observations=())
    )
    second = rollup_facts(
        PaperEvaluationInput(organization_id=ORG_ID, generated_at=later, observations=())
    )
    assert first.content_hash == second.content_hash
    assert first.generated_at != second.generated_at


def test_live_executable_cannot_be_true() -> None:
    with pytest.raises(ValidationError):
        PaperEvaluationObservation.model_validate(
            {
                "observation_id": uuid4(),
                "organization_id": ORG_ID,
                "stage": PaperEvaluationStage.WATCHER_SCAN,
                "source_system": "watcher_fusion",
                "source_event_id": "x",
                "source_event_version": 1,
                "occurred_at": GENERATED_AT,
                "data_quality": DataQualityClass.FRESH,
                "content_hash": HASH,
                "live_executable": True,
            }
        )


def test_watcher_performance_and_data_quality_metrics() -> None:
    observations = (
        make_scan(status="succeeded", data_quality=DataQualityClass.FRESH),
        make_scan(
            status="failed",
            reason_code="provider_outage",
            data_quality=DataQualityClass.UNAVAILABLE,
        ),
        make_scan(
            status="blocked",
            reason_code="stale_evidence",
            data_quality=DataQualityClass.STALE,
        ),
        make_scan(status="skipped", replayed=True, data_quality=DataQualityClass.REPLAY),
        make_assessment_observation(state=SetupAssessmentState.CONFIRMED_SETUP),
        make_assessment_observation(state=SetupAssessmentState.WATCH),
        make_assessment_observation(state=SetupAssessmentState.NO_SETUP),
        make_assessment_observation(state=SetupAssessmentState.PARTIAL_MATCH),
        make_assessment_observation(state=SetupAssessmentState.INVALIDATED),
        make_assessment_observation(state=SetupAssessmentState.EXPIRED),
        make_observation(
            stage=PaperEvaluationStage.CANDIDATE,
            source_system="watcher_candidate",
            candidate_id=CANDIDATE_ID,
        ),
    )
    facts = rollup_facts(
        PaperEvaluationInput(
            organization_id=ORG_ID, generated_at=GENERATED_AT, observations=observations
        )
    )
    assert facts.live_executable is False
    assert facts.watcher_orchestration_enabled is False
    assert facts.telegram_interaction_enabled is False
    assert facts.watcher.scan_count == 4
    assert facts.watcher.succeeded_count == 1
    assert facts.watcher.failed_count == 1
    assert facts.watcher.blocked_count == 1
    assert facts.watcher.skipped_count == 1
    assert facts.watcher.replay_count == 1
    assert facts.watcher.stale_evidence_count == 1
    assert facts.watcher.provider_outage_count == 1
    assert facts.watcher.confirmed_setup_count == 1
    assert facts.watcher.watch_count == 1
    assert facts.watcher.no_setup_count == 1
    assert facts.watcher.partial_match_count == 1
    assert facts.watcher.invalidated_count == 1
    assert facts.watcher.expired_count == 1
    assert facts.watcher.candidates_published == 1
    assert facts.data_quality.fresh_count == 1
    assert facts.data_quality.stale_count == 1
    assert facts.data_quality.unavailable_count == 1
    assert facts.data_quality.replay_count == 1
    assert facts.data_quality.stale_or_unavailable_rate == Decimal("0.5000")
    assert any(item.code == "watcher_not_activated" for item in facts.warnings)
    assert any(item.code == "degraded_evidence" for item in facts.warnings)


def test_setup_conversion_false_signals_and_human_vs_system() -> None:
    world = make_world()
    rejected = make_attribution_record(confirmed=True, rejected=True)
    skipped = make_attribution_record(confirmed=True, skipped=True)
    approved = make_attribution_record(confirmed=True, plan_approved=True)
    win = closed_win(pnl="12")
    loss = closed_loss(pnl="-6")
    facts = rollup_facts(
        PaperEvaluationInput(
            organization_id=ORG_ID,
            generated_at=GENERATED_AT,
            observations=(
                make_scan(),
                make_assessment_observation(state=SetupAssessmentState.CONFIRMED_SETUP),
            ),
            attributions=(rejected, skipped, approved, win, loss),
            eligibility=(world.evaluation,),
        )
    )
    assert facts.conversion.scans == 1
    assert facts.conversion.confirmed_setups >= 1
    assert facts.conversion.candidates == 5
    assert facts.conversion.eligible == 1
    assert facts.conversion.approved == 3
    assert facts.conversion.rejected == 1
    assert facts.conversion.skipped == 1
    assert facts.conversion.filled == 2
    assert facts.conversion.closed == 2
    assert facts.conversion.scan_to_confirmed_rate is not None
    assert facts.false_signals.confirmed_setups >= 1
    assert facts.false_signals.executed_outcomes == 2
    assert facts.false_signals.confirmed_wins == 1
    assert facts.false_signals.confirmed_losses == 1
    assert facts.false_signals.false_signal_rate == Decimal("0.5000")
    assert facts.human_vs_system.human_reject_or_skip == 2
    assert facts.human_vs_system.human_approvals == 3
    assert facts.human_vs_system.paper_system_executions == 2
    assert facts.human_vs_system.executed_outcomes == 2
    assert facts.human_vs_system.human_approved_executed_wins == 1
    assert facts.human_vs_system.human_approved_executed_losses == 1
    assert facts.missed_opportunities.rejected_confirmed == 1
    assert facts.missed_opportunities.skipped_confirmed == 1
    assert facts.missed_opportunities.counterfactual_pnl is None
    assert "Counterfactual PnL is not invented" in facts.missed_opportunities.warning


def test_win_rate_expectancy_drawdown_mfe_mae_and_rule_adherence() -> None:
    first_id = uuid4()
    second_id = uuid4()
    third_id = uuid4()
    win = closed_win(pnl="10", journal_trade_id=first_id, candidate_id=uuid4())
    loss = closed_loss(
        pnl="-20",
        journal_trade_id=second_id,
        candidate_id=uuid4(),
        risk_adherence=RiskAdherence.STOP_VIOLATION,
    )
    recovery = closed_win(pnl="5", journal_trade_id=third_id, candidate_id=uuid4())
    journal = {
        first_id: journal_measurement(first_id, net_pnl="10", mfe="30", mae="4", capture="40"),
        second_id: journal_measurement(second_id, net_pnl="-20", mfe="2", mae="22", capture="10"),
        third_id: journal_measurement(third_id, net_pnl="5", mfe="8", mae="1", capture="70"),
    }
    facts = rollup_facts(
        PaperEvaluationInput(
            organization_id=ORG_ID,
            generated_at=GENERATED_AT,
            attributions=(win, loss, recovery),
            journal=journal,
        )
    )
    overall = facts.strategy_overall
    assert overall.win_count == 2
    assert overall.loss_count == 1
    assert overall.win_rate == Decimal("0.6667")
    assert overall.expectancy == Decimal("-1.67")
    assert overall.net_pnl_total == Decimal("-5.00")
    assert overall.max_drawdown == Decimal("20.00")
    assert overall.mfe_sample_count == 3
    assert overall.average_mfe == Decimal("13.33")
    assert overall.mae_sample_count == 3
    assert overall.average_mae == Decimal("9.00")
    assert overall.average_capture_pct == Decimal("40.00")
    assert overall.confidence is SampleConfidence.INSUFFICIENT
    assert facts.rule_adherence.assessed_count == 3
    assert facts.rule_adherence.risk_adhered_count == 2
    assert facts.rule_adherence.stop_violation_count == 1
    assert facts.rule_adherence.adherence_rate == Decimal("0.6667")


def test_blocked_trades_and_eligible_not_approved() -> None:
    world = make_world()
    blocked = blocked_evaluation(
        world.evaluation,
        reason=EligibilityReasonCode.BLOCKED_DATA_QUALITY,
        candidate_id=uuid4(),
    )
    facts = rollup_facts(
        PaperEvaluationInput(
            organization_id=ORG_ID,
            generated_at=GENERATED_AT,
            eligibility=(world.evaluation, blocked),
        )
    )
    assert facts.blocked.blocked_count == 1
    assert facts.blocked.by_reason[0][0] == "blocked_data_quality"
    assert facts.missed_opportunities.eligible_not_approved == 1
    assert facts.missed_opportunities.blocked_after_confirmation == 1
    assert facts.missed_opportunities.counterfactual_pnl is None


def test_autonomous_unfilled_approval_is_not_a_missed_opportunity() -> None:
    world = make_world()
    candidate_id = world.evaluation.eligibility.candidate_id
    record = make_attribution_record(
        organization_id=ORG_ID,
        candidate_id=candidate_id,
        plan_approved=False,
        filled=False,
    )
    autonomous = record.model_copy(
        update={
            "facts": record.facts.model_copy(
                update={
                    "strategy_pattern": record.facts.strategy_pattern.model_copy(
                        update={"autonomous_approval": True, "plan_approved": False}
                    )
                }
            )
        }
    )
    facts = rollup_facts(
        PaperEvaluationInput(
            organization_id=ORG_ID,
            generated_at=GENERATED_AT,
            attributions=(autonomous,),
            eligibility=(world.evaluation,),
        )
    )
    assert facts.missed_opportunities.eligible_not_approved == 0
    assert facts.conversion.approved == 0
    assert facts.human_vs_system.human_approvals == 0


def test_strategy_version_comparison_and_refinement_cannot_activate() -> None:
    strong = tuple(
        closed_win(pnl="12", strategy_version_id=STRATEGY_VERSION_ID, candidate_id=uuid4())
        for _ in range(5)
    )
    weak = tuple(
        closed_loss(pnl="-9", strategy_version_id=OTHER_STRATEGY_VERSION_ID, candidate_id=uuid4())
        for _ in range(5)
    )
    facts = rollup_facts(
        PaperEvaluationInput(
            organization_id=ORG_ID,
            generated_at=GENERATED_AT,
            attributions=strong + weak,
        )
    )
    assert len(facts.strategy_versions) == 2
    by_version = {item.strategy_version_id: item for item in facts.strategy_versions}
    assert by_version[STRATEGY_VERSION_ID].expectancy == Decimal("12.00")
    assert by_version[OTHER_STRATEGY_VERSION_ID].expectancy == Decimal("-9.00")
    suggestions = refinement_suggestions(facts, narrative="Tighten the trigger.")
    assert suggestions
    assert all(item.activate is False and item.auto_activate is False for item in suggestions)
    assert any(item.category == "strategy_version_comparison" for item in suggestions)
    labeled = suggestions[0].narrative_explanation or ""
    assert labeled.startswith(NARRATIVE_NOT_FACT_BANNER[:18])
    with pytest.raises(RefinementActivationForbiddenError):
        refuse_activation(suggestions[0])


def test_tenant_isolation_and_query_service_merge() -> None:
    store = InMemoryPaperEvaluationStore()
    attributions = InMemoryAttributionStore()
    recorder = PaperEvaluationRecorder(store)
    recorder.record(make_scan(organization_id=ORG_ID, source_event_id="org-a"))
    recorder.record(make_scan(organization_id=OTHER_ORG, source_event_id="org-b"))
    attributions.put(closed_win(candidate_id=uuid4()))
    attributions.put(make_attribution_record(organization_id=OTHER_ORG, candidate_id=uuid4()))
    query = PaperEvaluationQueryService(store, attribution_store=attributions)
    summary = query.summary(organization_id=ORG_ID, generated_at=GENERATED_AT)
    assert summary.authority == "paper_evaluation_measurement"
    assert summary.live_executable is False
    assert summary.facts.watcher.scan_count == 1
    assert summary.facts.conversion.candidates == 1
    assert summary.narrative is None
    other = query.summary(organization_id=OTHER_ORG, generated_at=GENERATED_AT)
    assert other.facts.watcher.scan_count == 1
    assert other.facts.conversion.candidates == 1
    assert other.facts.organization_id == OTHER_ORG


def test_duplicate_watcher_identity_converges_and_persist_adds_candidate() -> None:
    store = InMemoryPaperEvaluationStore()
    recorder = PaperEvaluationRecorder(store)
    command = make_eval_command()
    assessed = make_eval_outcome(command, candidate_ids=())
    recorder.record_watcher_outcome(command, assessed, occurred_at=GENERATED_AT)
    persisted = make_eval_outcome(command, candidate_ids=(CANDIDATE_ID,))
    recorder.record_watcher_outcome(
        command, persisted, occurred_at=GENERATED_AT + timedelta(seconds=3)
    )
    rows = store.list_for_organization(ORG_ID)
    scans = [item for item in rows if item.stage is PaperEvaluationStage.WATCHER_SCAN]
    candidates = [item for item in rows if item.stage is PaperEvaluationStage.CANDIDATE]
    assert len(scans) == 1
    assert len(candidates) == 1
    assert candidates[0].candidate_id == CANDIDATE_ID


def test_observer_cannot_change_evaluation_outcome() -> None:
    class Boom:
        def observe_evaluation(self, command: object, outcome: object) -> None:
            raise RuntimeError("measurement must not change setup truth")

    command = make_eval_command()
    service = build_fusion_evaluation_service(
        evidence=InMemoryWatcherScanEvidence(),
        outcome_observer=Boom(),
    )
    outcome = service.evaluate(command)
    assert outcome.status is EvaluationStatus.FAILED
    assert outcome.reason_code == "missing_canonical_evidence"
    assert outcome.candidate_ids == ()


def test_recorder_copies_stale_and_invalidated_watcher_outcomes() -> None:
    store = InMemoryPaperEvaluationStore()
    recorder = PaperEvaluationRecorder(store)
    stale = make_eval_command()
    recorder.record_watcher_outcome(
        stale,
        make_eval_outcome(stale, status=EvaluationStatus.FAILED, reason_code="stale_evidence"),
    )
    invalidated = make_eval_command()
    recorder.record_watcher_outcome(
        invalidated,
        make_eval_outcome(
            invalidated,
            status=EvaluationStatus.SUCCEEDED,
            reason_code=SetupAssessmentState.INVALIDATED.value,
        ),
    )
    facts = rollup_facts(
        PaperEvaluationInput(
            organization_id=ORG_ID,
            generated_at=GENERATED_AT,
            observations=store.list_for_organization(ORG_ID),
        )
    )
    assert facts.watcher.stale_evidence_count == 1
    assert facts.watcher.invalidated_count == 1
    assert facts.false_signals.confirmed_invalidated_before_fill == 1
    assert facts.data_quality.stale_count == 1


def test_component_reason_codes_preserve_stale_and_outage_measurement() -> None:
    store = InMemoryPaperEvaluationStore()
    recorder = PaperEvaluationRecorder(store)
    for reason in ("canonical_open_interest_stale", "canonical_ohlcv_15m_regional_failure"):
        command = make_eval_command()
        recorder.record_watcher_outcome(
            command,
            make_eval_outcome(command, status=EvaluationStatus.FAILED, reason_code=reason),
        )
    facts = rollup_facts(
        PaperEvaluationInput(
            organization_id=ORG_ID,
            generated_at=GENERATED_AT,
            observations=store.list_for_organization(ORG_ID),
        )
    )
    assert facts.watcher.stale_evidence_count == 1
    assert facts.watcher.provider_outage_count == 1
    assert facts.data_quality.stale_count == 1
    assert facts.data_quality.unavailable_count == 1


def test_query_narrative_is_sibling_not_fact() -> None:
    query = PaperEvaluationQueryService(InMemoryPaperEvaluationStore())
    summary = query.summary(
        organization_id=ORG_ID,
        generated_at=GENERATED_AT,
        narrative="The strategy looks ready to auto-promote.",
    )
    assert summary.narrative is not None
    assert summary.narrative.banner.startswith("NARRATIVE_NOT_FACT")
    assert "auto-promote" in summary.narrative.text
    assert summary.facts.content_hash
    assert "auto-promote" not in summary.facts.content_hash


def test_canonical_paper_evaluation_read_uses_request_session_only() -> None:
    """Measurement reads must not open the process-wide engine (default /alphatrade)."""

    session = MagicMock()
    empty = MagicMock()
    empty.all.return_value = []
    empty.first.return_value = None
    session.scalars.return_value = empty
    session.get.return_value = None
    runtime = MagicMock()
    runtime.candidate_repository.list_for_organization.side_effect = RuntimeError(
        "paper evaluation must not use runtime Candidate list"
    )
    runtime.eligibility_store.latest_for_candidate.side_effect = RuntimeError(
        "paper evaluation must not use runtime eligibility store"
    )
    service = CanonicalReadService(session, runtime)
    result = service.paper_evaluation(organization_id=ORG_ID, learning_venue_mode=None)
    assert result.live_executable is False
    assert result.watcher_activated is False
    assert result.summary.facts.conversion.closed == 0
    assert result.summary.facts.missed_opportunities.counterfactual_pnl is None
    runtime.candidate_repository.list_for_organization.assert_not_called()
    runtime.eligibility_store.latest_for_candidate.assert_not_called()


def test_postgres_store_roundtrip_and_narrative_converge(
    attribution_sessions: sessionmaker[Session],
) -> None:
    with attribution_sessions() as session:
        store = PostgresPaperEvaluationStore(session)
        recorder = PaperEvaluationRecorder(store)
        first = recorder.record(make_observation(narrative_explanation="First wording."))
        second = recorder.record(
            first.model_copy(update={"narrative_explanation": "Second wording."})
        )
        session.commit()
        loaded = store.get(organization_id=ORG_ID, observation_id=first.observation_id)
        assert loaded is not None
        assert loaded.content_hash == first.content_hash == second.content_hash
        assert loaded.narrative_explanation == "Second wording."
        listed = store.list_for_organization(ORG_ID)
        assert len(listed) == 1
        assert store.list_for_organization(OTHER_ORG) == ()
