"""Read-only paper-evaluation query service. Not a trading authority."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.learning_attribution.contracts import AttributionRecord, LearningVenueMode
from app.learning_attribution.ports import AttributionStore
from app.paper_evaluation.contracts import (
    PaperEvaluationNarrative,
    PaperEvaluationSummary,
)
from app.paper_evaluation.hashing import hashed_facts
from app.paper_evaluation.identity import summary_id_for
from app.paper_evaluation.metrics import PaperEvaluationInput, rollup_facts
from app.paper_evaluation.ports import JournalExcursionPort, PaperEvaluationStore
from app.paper_evaluation.refinement import refinement_suggestions
from app.signal_fusion.action_eligibility import ActionEligibilityEvaluation

# The summary rolls up at most this many of the newest observations, so API
# latency and memory stay bounded as a tenant's history grows (M10).
PAPER_EVALUATION_SUMMARY_LIMIT = 20_000


class PaperEvaluationQueryService:
    """Tenant-scoped measurement reads over observations + attribution + journal."""

    def __init__(
        self,
        store: PaperEvaluationStore,
        *,
        attribution_store: AttributionStore | None = None,
        journal: JournalExcursionPort | None = None,
    ) -> None:
        self._store = store
        self._attribution_store = attribution_store
        self._journal = journal

    def summary(
        self,
        *,
        organization_id: UUID,
        learning_venue_mode: LearningVenueMode | None = None,
        eligibility: tuple[ActionEligibilityEvaluation, ...] = (),
        generated_at: datetime | None = None,
        narrative: str | None = None,
    ) -> PaperEvaluationSummary:
        observations = self._store.list_for_organization(
            organization_id, limit=PAPER_EVALUATION_SUMMARY_LIMIT
        )
        attributions = self._attributions(
            organization_id=organization_id, learning_venue_mode=learning_venue_mode
        )
        journal_ids = tuple(
            record.facts.journal_trade_id
            for record in attributions
            if record.facts.journal_trade_id is not None
        )
        journal = {}
        if self._journal is not None and journal_ids:
            journal = self._journal.facts_for(
                organization_id=organization_id, journal_trade_ids=journal_ids
            )
        facts = rollup_facts(
            PaperEvaluationInput(
                organization_id=organization_id,
                generated_at=generated_at or datetime.now(UTC),
                observations=observations,
                attributions=attributions,
                eligibility=eligibility,
                journal=journal,
            )
        )
        if learning_venue_mode is not None:
            facts = hashed_facts(
                facts.model_copy(update={"learning_venue_mode": learning_venue_mode})
            )
        labeled = None if not narrative else PaperEvaluationNarrative(text=narrative)
        return PaperEvaluationSummary(
            summary_id=summary_id_for(
                organization_id=organization_id, facts_hash=facts.content_hash
            ),
            organization_id=organization_id,
            facts=facts,
            refinements=refinement_suggestions(facts, narrative=narrative),
            narrative=labeled,
        )

    def _attributions(
        self,
        *,
        organization_id: UUID,
        learning_venue_mode: LearningVenueMode | None,
    ) -> tuple[AttributionRecord, ...]:
        if self._attribution_store is None:
            return ()
        records = self._attribution_store.list_for_organization(organization_id)
        if learning_venue_mode is None:
            return records
        return tuple(
            record for record in records if record.facts.learning_venue_mode is learning_venue_mode
        )
