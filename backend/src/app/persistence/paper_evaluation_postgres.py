"""PostgreSQL adapter for PaperEvaluationStore.

Persists measurement observations without becoming a JournalTrade or Candidate
writer. Caller owns the unit of work. Duplicate source identity converges.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.paper_evaluation import PaperEvaluationObservationRow
from app.paper_evaluation.contracts import PaperEvaluationObservation
from app.paper_evaluation.errors import (
    CrossTenantPaperEvaluationError,
    PaperEvaluationConflictError,
)
from app.persistence.unique import is_unique_violation

_UNIQUES = (
    "uq_paper_evaluation_observations_source",
    "uq_paper_evaluation_observations_org_id",
    "pk_paper_evaluation_observations",
)


class PostgresPaperEvaluationStore:
    """Durable PaperEvaluationStore. In-memory remains the unit-test default."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self,
        *,
        organization_id: UUID,
        observation_id: UUID,
    ) -> PaperEvaluationObservation | None:
        row = self._session.get(PaperEvaluationObservationRow, observation_id)
        if row is None or row.organization_id != organization_id:
            return None
        return _from_row(row)

    def find(
        self,
        *,
        organization_id: UUID,
        source_system: str,
        source_event_id: str,
        source_event_version: int,
    ) -> PaperEvaluationObservation | None:
        row = self._session.scalars(
            select(PaperEvaluationObservationRow).where(
                PaperEvaluationObservationRow.organization_id == organization_id,
                PaperEvaluationObservationRow.source_system == source_system,
                PaperEvaluationObservationRow.source_event_id == source_event_id,
                PaperEvaluationObservationRow.source_event_version == source_event_version,
            )
        ).first()
        return None if row is None else _from_row(row)

    def put(self, observation: PaperEvaluationObservation) -> PaperEvaluationObservation:
        if observation.organization_id is None:
            raise CrossTenantPaperEvaluationError()
        existing = self.find(
            organization_id=observation.organization_id,
            source_system=observation.source_system,
            source_event_id=observation.source_event_id,
            source_event_version=observation.source_event_version,
        )
        if existing is not None:
            if existing.organization_id != observation.organization_id:
                raise CrossTenantPaperEvaluationError()
            if existing.content_hash != observation.content_hash:
                raise PaperEvaluationConflictError(
                    "Paper evaluation source identity replayed with conflicting facts.",
                    details={"reason": "conflicting_source_identity"},
                )
            if (
                observation.narrative_explanation is not None
                and observation.narrative_explanation != existing.narrative_explanation
            ):
                row = self._session.get(PaperEvaluationObservationRow, existing.observation_id)
                if row is None or row.organization_id != observation.organization_id:
                    raise CrossTenantPaperEvaluationError()
                row.narrative_explanation = observation.narrative_explanation
                self._session.flush()
                return _from_row(row)
            return existing
        row = _to_row(observation)
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
        except IntegrityError as exc:
            if not is_unique_violation(exc, *_UNIQUES):
                raise
            loaded = self.find(
                organization_id=observation.organization_id,
                source_system=observation.source_system,
                source_event_id=observation.source_event_id,
                source_event_version=observation.source_event_version,
            )
            if loaded is None:
                raise
            if loaded.content_hash != observation.content_hash:
                raise PaperEvaluationConflictError(
                    "Paper evaluation source identity replayed with conflicting facts.",
                    details={"reason": "conflicting_source_identity"},
                ) from exc
            return loaded
        return _from_row(row)

    def list_for_organization(
        self, organization_id: UUID, *, limit: int | None = None
    ) -> tuple[PaperEvaluationObservation, ...]:
        statement = select(PaperEvaluationObservationRow).where(
            PaperEvaluationObservationRow.organization_id == organization_id
        )
        if limit is None:
            rows = self._session.scalars(
                statement.order_by(
                    PaperEvaluationObservationRow.occurred_at,
                    PaperEvaluationObservationRow.observation_id,
                )
            ).all()
            return tuple(_from_row(row) for row in rows)
        newest = self._session.scalars(
            statement.order_by(
                PaperEvaluationObservationRow.occurred_at.desc(),
                PaperEvaluationObservationRow.observation_id.desc(),
            ).limit(max(limit, 0))
        ).all()
        return tuple(_from_row(row) for row in reversed(newest))


def _to_row(observation: PaperEvaluationObservation) -> PaperEvaluationObservationRow:
    payload: dict[str, Any] = observation.model_dump(mode="json")
    payload.pop("narrative_explanation", None)
    return PaperEvaluationObservationRow(
        observation_id=observation.observation_id,
        organization_id=observation.organization_id,
        stage=observation.stage.value,
        source_system=observation.source_system,
        source_event_id=observation.source_event_id,
        source_event_version=observation.source_event_version,
        occurred_at=observation.occurred_at,
        strategy_version_id=observation.strategy_version_id,
        setup_definition_id=observation.setup_definition_id,
        candidate_id=observation.candidate_id,
        assessment_id=observation.assessment_id,
        journal_trade_id=observation.journal_trade_id,
        scan_status=observation.scan_status,
        reason_code=observation.reason_code,
        assessment_state=(
            None if observation.assessment_state is None else observation.assessment_state.value
        ),
        eligibility_state=(
            None if observation.eligibility_state is None else observation.eligibility_state.value
        ),
        data_quality=observation.data_quality.value,
        replayed=observation.replayed,
        plan_approved=observation.plan_approved,
        rejected=observation.rejected,
        skipped=observation.skipped,
        filled=observation.filled,
        closed=observation.closed,
        executed_outcome=observation.executed_outcome,
        result=None if observation.result is None else observation.result.value,
        net_pnl=observation.net_pnl,
        mfe_amount=observation.mfe_amount,
        mae_amount=observation.mae_amount,
        capture_pct=observation.capture_pct,
        planned_risk_amount=observation.planned_risk_amount,
        setup_quality=None
        if observation.setup_quality is None
        else observation.setup_quality.value,
        execution_quality=(
            None if observation.execution_quality is None else observation.execution_quality.value
        ),
        risk_adherence=(
            None if observation.risk_adherence is None else observation.risk_adherence.value
        ),
        trader_behavior=(
            None if observation.trader_behavior is None else observation.trader_behavior.value
        ),
        rule_compliance=(
            None if observation.rule_compliance is None else observation.rule_compliance.value
        ),
        learning_venue_mode=observation.learning_venue_mode.value,
        live_executable=False,
        content_hash=observation.content_hash,
        narrative_explanation=observation.narrative_explanation,
        payload=payload,
    )


def _from_row(row: PaperEvaluationObservationRow) -> PaperEvaluationObservation:
    body = dict(row.payload)
    body["narrative_explanation"] = row.narrative_explanation
    body["content_hash"] = row.content_hash
    return PaperEvaluationObservation.model_validate(body)
