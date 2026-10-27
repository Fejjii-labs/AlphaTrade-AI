"""Read adapters over existing canonical authorities. No minting."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models import UserStrategy, UserStrategyVersion
from app.learning_attribution.contracts import LearningVenueMode
from app.learning_attribution.query import LearningQueryService
from app.paper_evaluation.journal_facts import SqlAlchemyJournalExcursionPort
from app.paper_evaluation.query import PaperEvaluationQueryService
from app.persistence.attribution_postgres import PostgresAttributionStore
from app.persistence.eligibility_postgres import latest_evaluations_for_organization
from app.persistence.paper_evaluation_postgres import PostgresPaperEvaluationStore
from app.repositories.execution_protocol import (
    ExecutionCommandRepository,
    ExecutionProjectionRepository,
    ExecutionReceiptRepository,
)
from app.runtime.canonical import ProductionCanonicalRuntime
from app.schemas.canonical_reads import (
    CanonicalCandidateRead,
    CanonicalEligibilityRead,
    CanonicalExecutionReceiptRead,
    CanonicalLearningRecordRead,
    CanonicalLearningStatsRead,
    CanonicalPaperEvaluationRead,
    CanonicalSetupAssessmentRead,
    PaginatedCanonicalCandidates,
)
from app.schemas.execution_protocol import ExecutionProjectionView, ExecutionReceiptView
from app.services.display_names import display_name
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.enums import SetupAssessmentState


class CanonicalReadService:
    """Tenant-scoped reads for the Phase 8 decision frontend."""

    def __init__(self, session: Session, runtime: ProductionCanonicalRuntime) -> None:
        self._session = session
        self._runtime = runtime
        self._receipts = ExecutionReceiptRepository(session)
        self._commands = ExecutionCommandRepository(session)
        self._projections = ExecutionProjectionRepository(session)
        self._learning = LearningQueryService(PostgresAttributionStore(session))
        self._paper_evaluation = PaperEvaluationQueryService(
            PostgresPaperEvaluationStore(session),
            attribution_store=PostgresAttributionStore(session),
            journal=SqlAlchemyJournalExcursionPort(session),
        )

    def list_candidates(
        self,
        *,
        organization_id: UUID,
        limit: int,
        offset: int,
    ) -> PaginatedCanonicalCandidates:
        items, total = self._runtime.candidate_repository.list_for_organization(
            organization_id, limit=limit, offset=offset
        )
        return PaginatedCanonicalCandidates(
            items=self._candidate_reads(organization_id, items),
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_candidate(self, *, organization_id: UUID, candidate_id: UUID) -> CanonicalCandidateRead:
        candidate = self._runtime.lifecycle.get_by_candidate_id(organization_id, candidate_id)
        if candidate is None:
            raise NotFoundError("Canonical Candidate is unknown in this organization.")
        return self._candidate_reads(organization_id, [candidate])[0]

    def _candidate_reads(
        self, organization_id: UUID, candidates: Sequence[Candidate]
    ) -> list[CanonicalCandidateRead]:
        version_ids = {item.strategy_version_id for item in candidates}
        names = (
            dict(
                self._session.execute(
                    select(UserStrategyVersion.id, UserStrategy.name)
                    .join(UserStrategy, UserStrategy.id == UserStrategyVersion.strategy_id)
                    .where(
                        UserStrategyVersion.id.in_(version_ids),
                        UserStrategy.organization_id == organization_id,
                    )
                )
                .tuples()
                .all()
            )
            if version_ids
            else {}
        )
        return [
            CanonicalCandidateRead(
                candidate=item,
                display_name=display_name(
                    names.get(item.strategy_version_id),
                    item.evidence_identity.instrument.base_asset
                    + item.evidence_identity.instrument.quote_asset,
                    item.direction,
                    item.timeframe,
                ),
            )
            for item in candidates
        ]

    def get_setup_assessment(
        self, *, organization_id: UUID, assessment_id: UUID
    ) -> CanonicalSetupAssessmentRead:
        candidate = self._runtime.candidate_repository.get_by_assessment_id(
            organization_id, assessment_id
        )
        if candidate is None:
            raise NotFoundError("SetupAssessment identity is unknown in this organization.")
        evaluation = self._runtime.eligibility_store.latest_for_candidate(
            organization_id=organization_id, candidate_id=candidate.candidate_id
        )
        return CanonicalSetupAssessmentRead(
            assessment_id=candidate.assessment_id,
            organization_id=candidate.organization_id,
            candidate_id=candidate.candidate_id,
            assessment_state=SetupAssessmentState.CONFIRMED_SETUP,
            assessment_content_hash=(
                None if evaluation is None else evaluation.setup_assessment_content_hash
            ),
            evidence_window_hash=candidate.evidence_window_hash,
            strategy_version_id=candidate.strategy_version_id,
            setup_definition_id=candidate.setup_definition_id,
            fusion_policy_version=candidate.fusion_policy_version,
            valid_until=candidate.valid_until,
        )

    def get_candidate_eligibility(
        self, *, organization_id: UUID, candidate_id: UUID
    ) -> CanonicalEligibilityRead:
        if self._runtime.lifecycle.get_by_candidate_id(organization_id, candidate_id) is None:
            raise NotFoundError("Canonical Candidate is unknown in this organization.")
        evaluation = self._runtime.eligibility_store.latest_for_candidate(
            organization_id=organization_id, candidate_id=candidate_id
        )
        if evaluation is None:
            raise NotFoundError("ActionEligibility is unknown for this Candidate.")
        return CanonicalEligibilityRead(evaluation=evaluation)

    def get_execution_receipt(
        self, *, organization_id: UUID, receipt_id: UUID
    ) -> CanonicalExecutionReceiptRead:
        row = self._receipts.get(receipt_id)
        if row is None:
            row = self._receipts.get_by_command(receipt_id)
        if row is None or row.organization_id != organization_id:
            raise NotFoundError("Canonical execution receipt is unknown in this organization.")
        command = self._commands.get(row.command_id)
        if command is None or command.organization_id != organization_id:
            raise NotFoundError("Canonical execution command is unknown in this organization.")
        projection = self._projections.get_by_receipt(row.id)
        return CanonicalExecutionReceiptRead(
            receipt=ExecutionReceiptView(
                receipt_id=row.id,
                command_id=command.id,
                operation=command.operation,
                authorization_id=row.authorization_id,
                organization_id=row.organization_id,
                user_id=row.user_id,
                account_id=row.account_id,
                created_at=row.created_at,
                outcome=command.outcome,
                blocked_reason_code=command.blocked_reason_code,
            ),
            projection=(
                None if projection is None else ExecutionProjectionView.model_validate(projection)
            ),
        )

    def get_learning_record(
        self, *, organization_id: UUID, candidate_id: UUID
    ) -> CanonicalLearningRecordRead:
        record = self._learning.get_record(
            organization_id=organization_id, candidate_id=candidate_id
        )
        if record is None:
            raise NotFoundError("Learning attribution is unknown for this Candidate.")
        return CanonicalLearningRecordRead(record=record)

    def strategy_stats(
        self,
        *,
        organization_id: UUID,
        learning_venue_mode: LearningVenueMode | None,
    ) -> CanonicalLearningStatsRead:
        return CanonicalLearningStatsRead(
            venue_mode=learning_venue_mode,
            snapshot=self._learning.strategy_pattern_stats(
                organization_id=organization_id,
                learning_venue_mode=learning_venue_mode,
            ),
        )

    def paper_evaluation(
        self,
        *,
        organization_id: UUID,
        learning_venue_mode: LearningVenueMode | None,
    ) -> CanonicalPaperEvaluationRead:
        # Read eligibility on the request session. Runtime repositories may open a
        # process-wide engine (default database ``alphatrade``) when threading.local
        # bind_session does not follow the async endpoint thread.
        eligibility = latest_evaluations_for_organization(
            self._session, organization_id=organization_id
        )
        return CanonicalPaperEvaluationRead(
            summary=self._paper_evaluation.summary(
                organization_id=organization_id,
                learning_venue_mode=learning_venue_mode,
                eligibility=eligibility,
            )
        )
