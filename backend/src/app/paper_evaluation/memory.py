"""In-memory paper-evaluation store. Not a JournalTrade or Candidate writer."""

from __future__ import annotations

from threading import RLock
from uuid import UUID

from app.paper_evaluation.contracts import PaperEvaluationObservation
from app.paper_evaluation.errors import (
    CrossTenantPaperEvaluationError,
    PaperEvaluationConflictError,
)


def _source_key(
    *,
    organization_id: UUID,
    source_system: str,
    source_event_id: str,
    source_event_version: int,
) -> tuple[UUID, str, str, int]:
    return (organization_id, source_system, source_event_id, source_event_version)


class InMemoryPaperEvaluationStore:
    """Thread-safe substitute for PostgreSQL observation rows."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._by_id: dict[tuple[UUID, UUID], PaperEvaluationObservation] = {}
        self._by_source: dict[tuple[UUID, str, str, int], UUID] = {}

    def get(
        self,
        *,
        organization_id: UUID,
        observation_id: UUID,
    ) -> PaperEvaluationObservation | None:
        with self._lock:
            return self._by_id.get((organization_id, observation_id))

    def find(
        self,
        *,
        organization_id: UUID,
        source_system: str,
        source_event_id: str,
        source_event_version: int,
    ) -> PaperEvaluationObservation | None:
        key = _source_key(
            organization_id=organization_id,
            source_system=source_system,
            source_event_id=source_event_id,
            source_event_version=source_event_version,
        )
        with self._lock:
            observation_id = self._by_source.get(key)
            if observation_id is None:
                return None
            return self._by_id.get((organization_id, observation_id))

    def put(self, observation: PaperEvaluationObservation) -> PaperEvaluationObservation:
        if observation.live_executable:
            raise PaperEvaluationConflictError(
                "Paper evaluation observations cannot be live-executable.",
                details={"reason": "live_executable"},
            )
        key = _source_key(
            organization_id=observation.organization_id,
            source_system=observation.source_system,
            source_event_id=observation.source_event_id,
            source_event_version=observation.source_event_version,
        )
        with self._lock:
            existing_id = self._by_source.get(key)
            if existing_id is not None:
                existing = self._by_id[(observation.organization_id, existing_id)]
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
                    updated = existing.model_copy(
                        update={"narrative_explanation": observation.narrative_explanation}
                    )
                    self._by_id[(existing.organization_id, existing.observation_id)] = updated
                    return updated
                return existing
            owner = self._by_id.get((observation.organization_id, observation.observation_id))
            if owner is not None and owner.organization_id != observation.organization_id:
                raise CrossTenantPaperEvaluationError()
            self._by_id[(observation.organization_id, observation.observation_id)] = observation
            self._by_source[key] = observation.observation_id
            return observation

    def list_for_organization(
        self, organization_id: UUID, *, limit: int | None = None
    ) -> tuple[PaperEvaluationObservation, ...]:
        with self._lock:
            rows = [item for (org, _), item in self._by_id.items() if org == organization_id]
            ordered = sorted(rows, key=lambda item: (item.occurred_at, str(item.observation_id)))
            if limit is not None:
                ordered = ordered[-limit:] if limit > 0 else []
            return tuple(ordered)
