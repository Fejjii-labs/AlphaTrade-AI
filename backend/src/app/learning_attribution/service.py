"""Learning attribution application service.

Composes ``JournalLifecycleProjector`` (the only JournalTrade writer) with a
record-only fact layer. SetupAssessment, Candidate, and TradePlan are inputs.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import JournalTrade
from app.learning_attribution.contracts import (
    ATTRIBUTION_SCHEMA,
    AttributionCommand,
    AttributionEvent,
    AttributionFacts,
    AttributionRecord,
    AttributionResult,
    HumanVsSystemAttributionFacts,
    LearningVenueMode,
    StrategyPatternStatFacts,
    TradePlanLineageRef,
)
from app.learning_attribution.errors import (
    CrossTenantAttributionError,
    ExecutedOutcomeForbiddenError,
    LearningAttributionConflictError,
    NarrativeCannotRewriteFactsError,
)
from app.learning_attribution.identity import attribution_id_for, lineage_payload_from_snapshot
from app.learning_attribution.lineage import (
    refuse_market_truth_rewrite,
    validate_attribution_command,
)
from app.learning_attribution.memory import InMemoryAttributionStore
from app.learning_attribution.ports import AttributionStore
from app.learning_attribution.quality import (
    behavior_facts,
    executed_trade_outcome,
    execution_quality_facts,
    outcome_facts,
    planned_setup_quality,
    planned_setup_quality_from_identity,
    risk_adherence_facts,
    strategy_pattern_facts,
)
from app.repositories.journal_trades import JournalTradeRepository
from app.schemas.common import JournalLifecycleEventType
from app.schemas.journal_lifecycle import (
    LINEAGE_PAYLOAD_KEY,
    JournalLifecycleEventInput,
    JournalProjectionResult,
)
from app.services.canonical_serialization import canonical_sha256
from app.services.journal_lifecycle_lineage import extract_lineage_map
from app.services.journal_lifecycle_projector import JournalLifecycleProjector
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.enums import SetupAssessmentState
from app.signal_fusion.types import hashed_model


class LearningAttributionService:
    """Downstream consumer of journal projection. Not a trading authority."""

    def __init__(
        self,
        session: Session,
        projector: JournalLifecycleProjector,
        store: AttributionStore | None = None,
    ) -> None:
        self._session = session
        self._projector = projector
        self._store = store if store is not None else InMemoryAttributionStore()
        self._trades = JournalTradeRepository(session)

    def apply(self, command: AttributionCommand) -> AttributionResult:
        validate_attribution_command(command)
        self._refuse_narrative_fact_overrides(command)
        event = self._event_with_lineage(command)
        existing = self._store.find_event(
            organization_id=command.organization_id,
            source_system=event.source_system,
            source_aggregate=event.source_aggregate,
            event_type=event.event_type.value,
            source_event_id=event.source_event_id,
            source_event_version=event.source_event_version,
            supersession=event.supersession,
            account_id=event.account_id,
        )
        projection = self._projector.project(
            event,
            organization_id=command.organization_id,
            user_id=command.user_id,
            actor_user_id=command.actor_user_id,
        )
        event_hash = self._projector_event_hash(command.organization_id, event)
        if existing is not None and existing.event_content_hash != event_hash:
            raise LearningAttributionConflictError(
                "Attribution source identity replayed with conflicting content.",
                details={"reason": "conflicting_source_identity"},
            )
        trade = self._load_trade(command.organization_id, projection.journal_trade_id)
        facts = self._build_facts(command, projection, trade)
        refuse_market_truth_rewrite(
            original_assessment_hash=command.lineage.assessment.content_hash,
            original_window_hash=command.lineage.assessment.evidence_window_hash,
            facts_assessment_hash=facts.setup_quality.assessment_content_hash,
            facts_window_hash=facts.setup_quality.evidence_window_hash,
        )
        event_row = AttributionEvent(
            event_type=event.event_type,
            source_system=event.source_system,
            source_aggregate=event.source_aggregate,
            source_event_id=event.source_event_id,
            source_event_version=event.source_event_version,
            supersession=event.supersession,
            event_content_hash=event_hash,
            facts_hash=facts.content_hash,
            journal_trade_id=projection.journal_trade_id,
            projection=projection,
            narrative_explanation=command.narrative_explanation,
        )
        previous = self._store.get(
            organization_id=command.organization_id,
            candidate_id=command.lineage.candidate.candidate_id,
        )
        self._assert_sticky_candidate_lifecycle(command, previous)
        if existing is not None:
            if previous is None:
                raise LearningAttributionConflictError(
                    "Attribution event exists without an aggregate record.",
                    details={"reason": "attribution_store_inconsistent"},
                )
            return AttributionResult(
                record=previous,
                created=False,
                replayed=True,
                journal_trade_id=previous.journal_trade_id,
                executed_trade_outcome=previous.facts.outcome.eligible,
                facts_hash=previous.facts.content_hash,
            )
        folded = self._fold_pattern(previous, facts.strategy_pattern)
        facts = facts.model_copy(update={"strategy_pattern": folded, "content_hash": "0" * 64})
        facts = hashed_model(facts)
        events = (() if previous is None else previous.events) + (event_row,)
        record = AttributionRecord(
            attribution_id=facts.attribution_id,
            organization_id=command.organization_id,
            candidate_id=command.lineage.candidate.candidate_id,
            execution_lifecycle_id=self._bound_lifecycle(command, previous, projection),
            journal_trade_id=projection.journal_trade_id
            if projection.journal_trade_id is not None
            else (None if previous is None else previous.journal_trade_id),
            facts=facts,
            events=events,
            narrative_explanation=command.narrative_explanation,
        )
        self._store.put(record)
        return AttributionResult(
            record=record,
            created=previous is None,
            replayed=projection.replayed,
            journal_trade_id=record.journal_trade_id,
            executed_trade_outcome=facts.outcome.eligible,
            facts_hash=facts.content_hash,
        )

    def apply_projected(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        candidate: Candidate,
        assessment_id: UUID,
        assessment_content_hash: str,
        evidence_window_hash: str,
        trade_plan: TradePlanLineageRef,
        event: JournalLifecycleEventInput,
        projection: JournalProjectionResult,
        learning_venue_mode: LearningVenueMode,
    ) -> AttributionResult:
        """Attribute an already-projected journal event. Does not write JournalTrade."""

        if candidate.organization_id != organization_id:
            raise CrossTenantAttributionError()
        if candidate.assessment_id != assessment_id:
            raise LearningAttributionConflictError(
                "Candidate assessment_id does not match stored SetupAssessment identity.",
                details={"reason": "assessment_identity_conflict"},
            )
        if candidate.evidence_window_hash != evidence_window_hash:
            raise LearningAttributionConflictError(
                "Candidate evidence_window_hash does not match stored SetupAssessment identity.",
                details={"reason": "evidence_window_conflict"},
            )
        if trade_plan.candidate_id != candidate.candidate_id:
            raise LearningAttributionConflictError(
                "TradePlan lineage candidate_id does not match Candidate.",
                details={"reason": "lineage_identity_mismatch"},
            )
        if event.account_id != trade_plan.account_id:
            raise LearningAttributionConflictError(
                "Attribution account does not match TradePlan lineage account.",
                details={"reason": "account_mismatch"},
            )
        existing = self._store.find_event(
            organization_id=organization_id,
            source_system=event.source_system,
            source_aggregate=event.source_aggregate,
            event_type=event.event_type.value,
            source_event_id=event.source_event_id,
            source_event_version=event.source_event_version,
            supersession=event.supersession,
            account_id=event.account_id,
        )
        event_hash = self._projector_event_hash(organization_id, event)
        if existing is not None and existing.event_content_hash != event_hash:
            raise LearningAttributionConflictError(
                "Attribution source identity replayed with conflicting content.",
                details={"reason": "conflicting_source_identity"},
            )
        trade = self._load_trade(organization_id, projection.journal_trade_id)
        setup = planned_setup_quality_from_identity(
            assessment_id=assessment_id,
            assessment_state=SetupAssessmentState.CONFIRMED_SETUP,
            assessment_content_hash=assessment_content_hash,
            evidence_window_hash=evidence_window_hash,
        )
        refuse_market_truth_rewrite(
            original_assessment_hash=assessment_content_hash,
            original_window_hash=evidence_window_hash,
            facts_assessment_hash=setup.assessment_content_hash,
            facts_window_hash=setup.evidence_window_hash,
        )
        execution = execution_quality_facts(
            event_type=event.event_type,
            trade=trade,
            payload=dict(event.payload),
        )
        risk = risk_adherence_facts(
            event_type=event.event_type,
            trade=trade,
            payload=dict(event.payload),
        )
        behavior = behavior_facts(
            event_type=event.event_type,
            candidate=candidate,
            trade=trade,
            payload=dict(event.payload),
        )
        outcome = outcome_facts(event_type=event.event_type, trade=trade)
        pattern = strategy_pattern_facts(
            candidate=candidate,
            event_type=event.event_type,
            trade=trade,
            outcome=outcome,
            learning_venue_mode=learning_venue_mode,
            payload=dict(event.payload),
        )
        human = HumanVsSystemAttributionFacts(
            decision_actor=behavior.actor,
            setup_quality_axis=setup.axis,
            execution_quality_axis=execution.axis,
            risk_adherence_axis=risk.axis,
            trader_behavior_axis=behavior.axis,
            planned_entry_price=execution.planned_entry_price,
            actual_entry_price=execution.actual_entry_price,
            entry_deviation_bps=execution.entry_deviation_bps,
            planned_stop_price=execution.planned_stop_price,
            actual_exit_price=execution.actual_exit_price,
            net_pnl=outcome.net_pnl,
            result=outcome.result,
            executed_trade_outcome=outcome.eligible,
            journal_trade_id=outcome.journal_trade_id,
            candidate_id=candidate.candidate_id,
            strategy_version_id=candidate.strategy_version_id,
            setup_definition_id=candidate.setup_definition_id,
        )
        lifecycle_id = event.execution_lifecycle_id
        draft = AttributionFacts(
            schema_version=ATTRIBUTION_SCHEMA,
            attribution_id=attribution_id_for(
                organization_id=organization_id,
                candidate_id=candidate.candidate_id,
            ),
            organization_id=organization_id,
            account_id=trade_plan.account_id,
            user_id=user_id,
            candidate_id=candidate.candidate_id,
            candidate_content_hash=candidate.content_hash,
            uniqueness_tuple_hash=candidate.uniqueness_tuple().canonical_hash(),
            execution_lifecycle_id=lifecycle_id,
            journal_trade_id=projection.journal_trade_id,
            assessment_id=assessment_id,
            evidence_window_hash=evidence_window_hash,
            trade_plan_revision_id=trade_plan.revision_id,
            learning_venue_mode=learning_venue_mode,
            setup_quality=setup,
            execution_quality=execution,
            risk_adherence=risk,
            trader_behavior=behavior,
            outcome=outcome,
            human_vs_system=human,
            strategy_pattern=pattern,
            projection_replayed=projection.replayed,
            projection_skipped_reason=projection.skipped_reason,
            content_hash="0" * 64,
        )
        facts = hashed_model(draft)
        event_row = AttributionEvent(
            event_type=event.event_type,
            source_system=event.source_system,
            source_aggregate=event.source_aggregate,
            source_event_id=event.source_event_id,
            source_event_version=event.source_event_version,
            supersession=event.supersession,
            event_content_hash=event_hash,
            facts_hash=facts.content_hash,
            journal_trade_id=projection.journal_trade_id,
            projection=projection,
        )
        previous = self._store.get(
            organization_id=organization_id,
            candidate_id=candidate.candidate_id,
        )
        if previous is not None:
            if previous.organization_id != organization_id:
                raise CrossTenantAttributionError()
            if (
                previous.execution_lifecycle_id is not None
                and lifecycle_id is not None
                and previous.execution_lifecycle_id != lifecycle_id
            ):
                raise LearningAttributionConflictError(
                    "Candidate is already bound to a different execution lifecycle.",
                    details={"reason": "candidate_lifecycle_conflict"},
                )
            if previous.facts.learning_venue_mode is not learning_venue_mode:
                raise LearningAttributionConflictError(
                    "Learning venue mode conflict for this attribution aggregate.",
                    details={"reason": "learning_venue_mode_conflict"},
                )
            if previous.facts.assessment_id != assessment_id:
                raise LearningAttributionConflictError(
                    "SetupAssessment identity conflict for this attribution aggregate.",
                    details={"reason": "assessment_identity_conflict"},
                )
            if previous.facts.evidence_window_hash != evidence_window_hash:
                raise LearningAttributionConflictError(
                    "Evidence window hash conflict for this attribution aggregate.",
                    details={"reason": "evidence_window_conflict"},
                )
            executing = event.event_type in {
                JournalLifecycleEventType.APPROVED_PLAN,
                JournalLifecycleEventType.FILL,
                JournalLifecycleEventType.CLOSE,
                JournalLifecycleEventType.RECONCILE,
            }
            if executing and (
                previous.facts.strategy_pattern.rejected or previous.facts.strategy_pattern.skipped
            ):
                raise ExecutedOutcomeForbiddenError(
                    "REJECT/SKIP attribution cannot later become an executed trade outcome.",
                    details={"reason": "reject_skip_cannot_execute"},
                )
        if existing is not None:
            if previous is None:
                raise LearningAttributionConflictError(
                    "Attribution event exists without an aggregate record.",
                    details={"reason": "attribution_store_inconsistent"},
                )
            return AttributionResult(
                record=previous,
                created=False,
                replayed=True,
                journal_trade_id=previous.journal_trade_id,
                executed_trade_outcome=previous.facts.outcome.eligible,
                facts_hash=previous.facts.content_hash,
            )
        folded = self._fold_pattern(previous, facts.strategy_pattern)
        facts = facts.model_copy(update={"strategy_pattern": folded, "content_hash": "0" * 64})
        facts = hashed_model(facts)
        events = (() if previous is None else previous.events) + (event_row,)
        record = AttributionRecord(
            attribution_id=facts.attribution_id,
            organization_id=organization_id,
            candidate_id=candidate.candidate_id,
            execution_lifecycle_id=(
                previous.execution_lifecycle_id
                if previous is not None and previous.execution_lifecycle_id is not None
                else lifecycle_id
            ),
            journal_trade_id=projection.journal_trade_id
            if projection.journal_trade_id is not None
            else (None if previous is None else previous.journal_trade_id),
            facts=facts,
            events=events,
        )
        self._store.put(record)
        return AttributionResult(
            record=record,
            created=previous is None,
            replayed=projection.replayed,
            journal_trade_id=record.journal_trade_id,
            executed_trade_outcome=facts.outcome.eligible,
            facts_hash=facts.content_hash,
        )

    def _event_with_lineage(self, command: AttributionCommand) -> JournalLifecycleEventInput:
        event = command.event
        lifecycle_id = event.execution_lifecycle_id or command.lineage.execution_lifecycle_id
        payload = dict(event.payload)
        lineage_dict = lineage_payload_from_snapshot(
            command.lineage.model_copy(update={"execution_lifecycle_id": lifecycle_id})
        )
        existing = extract_lineage_map(payload)
        incoming = {key: str(value) for key, value in lineage_dict.items()}
        for key, value in incoming.items():
            if key in existing and existing[key] != value:
                raise LearningAttributionConflictError(
                    "Event payload lineage conflicts with the attribution snapshot.",
                    details={"reason": "payload_lineage_conflict", "key": key},
                )
        payload[LINEAGE_PAYLOAD_KEY] = lineage_dict
        return event.model_copy(
            update={
                "payload": payload,
                "execution_lifecycle_id": lifecycle_id,
            }
        )

    def _build_facts(
        self,
        command: AttributionCommand,
        projection: JournalProjectionResult,
        trade: JournalTrade | None,
    ) -> AttributionFacts:
        candidate = command.lineage.candidate
        assessment = command.lineage.assessment
        event_type = command.event.event_type
        setup = planned_setup_quality(assessment)
        execution = execution_quality_facts(
            event_type=event_type,
            trade=trade,
            payload=dict(command.event.payload),
        )
        risk = risk_adherence_facts(
            event_type=event_type,
            trade=trade,
            payload=dict(command.event.payload),
        )
        behavior = behavior_facts(
            event_type=event_type,
            candidate=candidate,
            trade=trade,
            payload=dict(command.event.payload),
        )
        outcome = outcome_facts(event_type=event_type, trade=trade)
        if event_type in {
            JournalLifecycleEventType.REJECT,
            JournalLifecycleEventType.SKIP,
        } and executed_trade_outcome(event_type, trade):
            raise LearningAttributionConflictError(
                "REJECT/SKIP cannot create executed trade outcomes.",
                details={"reason": "reject_skip_outcome"},
            )
        pattern = strategy_pattern_facts(
            candidate=candidate,
            event_type=event_type,
            trade=trade,
            outcome=outcome,
            learning_venue_mode=command.learning_venue_mode,
            payload=dict(command.event.payload),
        )
        human = HumanVsSystemAttributionFacts(
            decision_actor=behavior.actor,
            setup_quality_axis=setup.axis,
            execution_quality_axis=execution.axis,
            risk_adherence_axis=risk.axis,
            trader_behavior_axis=behavior.axis,
            planned_entry_price=execution.planned_entry_price,
            actual_entry_price=execution.actual_entry_price,
            entry_deviation_bps=execution.entry_deviation_bps,
            planned_stop_price=execution.planned_stop_price,
            actual_exit_price=execution.actual_exit_price,
            net_pnl=outcome.net_pnl,
            result=outcome.result,
            executed_trade_outcome=outcome.eligible,
            journal_trade_id=outcome.journal_trade_id,
            candidate_id=candidate.candidate_id,
            strategy_version_id=candidate.strategy_version_id,
            setup_definition_id=candidate.setup_definition_id,
        )
        lifecycle_id = (
            command.event.execution_lifecycle_id or command.lineage.execution_lifecycle_id
        )
        plan = command.lineage.trade_plan
        draft = AttributionFacts(
            schema_version=ATTRIBUTION_SCHEMA,
            attribution_id=attribution_id_for(
                organization_id=command.organization_id,
                candidate_id=candidate.candidate_id,
            ),
            organization_id=command.organization_id,
            account_id=command.lineage.account_id,
            user_id=command.user_id,
            candidate_id=candidate.candidate_id,
            candidate_content_hash=candidate.content_hash,
            uniqueness_tuple_hash=candidate.uniqueness_tuple().canonical_hash(),
            execution_lifecycle_id=lifecycle_id,
            journal_trade_id=projection.journal_trade_id,
            assessment_id=assessment.assessment_id,
            evidence_window_hash=assessment.evidence_window_hash,
            trade_plan_revision_id=None if plan is None else plan.revision_id,
            learning_venue_mode=command.learning_venue_mode,
            setup_quality=setup,
            execution_quality=execution,
            risk_adherence=risk,
            trader_behavior=behavior,
            outcome=outcome,
            human_vs_system=human,
            strategy_pattern=pattern,
            projection_replayed=projection.replayed,
            projection_skipped_reason=projection.skipped_reason,
            content_hash="0" * 64,
        )
        return hashed_model(draft)

    def _fold_pattern(
        self,
        previous: AttributionRecord | None,
        current: StrategyPatternStatFacts,
    ) -> StrategyPatternStatFacts:
        if previous is None:
            return current
        prior = previous.facts.strategy_pattern
        autonomous = prior.autonomous_approval or current.autonomous_approval
        return current.model_copy(
            update={
                "candidate_confirmed": True,
                "rejected": prior.rejected or current.rejected,
                "skipped": prior.skipped or current.skipped,
                "autonomous_approval": autonomous,
                # Autonomous stays sticky. A later fill or close with no channel
                # must not OR plan_approved back to a human approval.
                "plan_approved": (
                    False if autonomous else (prior.plan_approved or current.plan_approved)
                ),
                "filled": prior.filled or current.filled,
                "closed": prior.closed or current.closed,
                "executed_outcome": prior.executed_outcome or current.executed_outcome,
                "win": prior.win or current.win,
                "loss": prior.loss or current.loss,
                "breakeven": prior.breakeven or current.breakeven,
            }
        )

    def _assert_sticky_candidate_lifecycle(
        self,
        command: AttributionCommand,
        previous: AttributionRecord | None,
    ) -> None:
        if previous is None:
            return
        if previous.organization_id != command.organization_id:
            raise LearningAttributionConflictError(
                "Attribution record belongs to a different organization.",
                details={"reason": "cross_tenant_attribution"},
            )
        incoming_lifecycle = (
            command.event.execution_lifecycle_id or command.lineage.execution_lifecycle_id
        )
        if (
            previous.execution_lifecycle_id is not None
            and incoming_lifecycle is not None
            and previous.execution_lifecycle_id != incoming_lifecycle
        ):
            raise LearningAttributionConflictError(
                "Candidate is already bound to a different execution lifecycle.",
                details={"reason": "candidate_lifecycle_conflict"},
            )
        if previous.facts.uniqueness_tuple_hash != (
            command.lineage.candidate.uniqueness_tuple().canonical_hash()
        ):
            raise LearningAttributionConflictError(
                "Candidate uniqueness tuple conflict for this attribution aggregate.",
                details={"reason": "uniqueness_tuple_conflict"},
            )
        if previous.facts.assessment_id != command.lineage.assessment.assessment_id:
            raise LearningAttributionConflictError(
                "SetupAssessment identity conflict for this attribution aggregate.",
                details={"reason": "assessment_identity_conflict"},
            )
        if previous.facts.evidence_window_hash != command.lineage.assessment.evidence_window_hash:
            raise LearningAttributionConflictError(
                "Evidence window hash conflict for this attribution aggregate.",
                details={"reason": "evidence_window_conflict"},
            )
        if previous.facts.learning_venue_mode is not command.learning_venue_mode:
            raise LearningAttributionConflictError(
                "Learning venue mode conflict for this attribution aggregate.",
                details={"reason": "learning_venue_mode_conflict"},
            )
        executing = command.event.event_type in {
            JournalLifecycleEventType.APPROVED_PLAN,
            JournalLifecycleEventType.FILL,
            JournalLifecycleEventType.CLOSE,
            JournalLifecycleEventType.RECONCILE,
        }
        if executing and (
            previous.facts.strategy_pattern.rejected or previous.facts.strategy_pattern.skipped
        ):
            raise ExecutedOutcomeForbiddenError(
                "REJECT/SKIP attribution cannot later become an executed trade outcome.",
                details={"reason": "reject_skip_cannot_execute"},
            )

    def _bound_lifecycle(
        self,
        command: AttributionCommand,
        previous: AttributionRecord | None,
        projection: JournalProjectionResult,
    ) -> UUID | None:
        if previous is not None and previous.execution_lifecycle_id is not None:
            return previous.execution_lifecycle_id
        if command.event.execution_lifecycle_id is not None:
            return command.event.execution_lifecycle_id
        if command.lineage.execution_lifecycle_id is not None:
            return command.lineage.execution_lifecycle_id
        trade_id = projection.journal_trade_id
        if trade_id is None:
            return None
        trade = self._load_trade(command.organization_id, trade_id)
        return None if trade is None else trade.execution_lifecycle_id

    def _load_trade(
        self,
        organization_id: UUID,
        journal_trade_id: UUID | None,
    ) -> JournalTrade | None:
        if journal_trade_id is None:
            return None
        return self._trades.get_scoped(journal_trade_id, organization_id=organization_id)

    def _projector_event_hash(
        self, organization_id: UUID, event: JournalLifecycleEventInput
    ) -> str:
        return canonical_sha256(
            {
                "organization_id": str(organization_id),
                "account_id": str(event.account_id),
                "event_type": event.event_type.value,
                "execution_lifecycle_id": (
                    str(event.execution_lifecycle_id) if event.execution_lifecycle_id else None
                ),
                "source_system": event.source_system,
                "source_aggregate": event.source_aggregate,
                "source_event_id": event.source_event_id,
                "source_event_version": event.source_event_version,
                "supersession": event.supersession,
                "payload": event.payload,
            }
        )

    def _refuse_narrative_fact_overrides(self, command: AttributionCommand) -> None:
        text = command.narrative_explanation
        if not text:
            return
        lowered = text.lower()
        forbidden = (
            "evidence_window_hash=",
            "assessment_state=",
            "rewrite_setup",
            "override_facts",
        )
        if any(token in lowered for token in forbidden):
            raise NarrativeCannotRewriteFactsError(
                "Narrative explanation cannot carry setup-truth or fact overrides.",
                details={"reason": "narrative_cannot_rewrite_facts"},
            )
