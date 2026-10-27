"""Read-only HTTP contracts for canonical Phase 8 surfaces.

These wrap existing Candidate, ActionEligibility, ExecutionReceipt, and
LearningQueryService authorities. They do not mint identity.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.learning_attribution.adapters.analytics import AttributionAnalyticsSnapshot
from app.learning_attribution.contracts import AttributionRecord, LearningVenueMode
from app.paper_evaluation.contracts import PaperEvaluationSummary
from app.schemas.common import StrictModel
from app.schemas.execution_protocol import ExecutionProjectionView, ExecutionReceiptView
from app.signal_fusion.action_eligibility import ActionEligibilityEvaluation
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.enums import SetupAssessmentState


class CanonicalCandidateRead(StrictModel):
    authority: Literal["canonical"] = "canonical"
    candidate: Candidate
    display_name: str = Field(
        default="", description='Trader-facing name, e.g. "Nested · BTCUSDT · Long · 15m".'
    )


class PaginatedCanonicalCandidates(StrictModel):
    items: list[CanonicalCandidateRead]
    total: int
    limit: int
    offset: int


class CanonicalSetupAssessmentRead(StrictModel):
    """Lineage projection of SetupAssessment identity. Not a second assessment writer."""

    authority: Literal["canonical_lineage_projection"] = "canonical_lineage_projection"
    assessment_id: UUID
    organization_id: UUID
    candidate_id: UUID
    assessment_state: SetupAssessmentState
    assessment_content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    evidence_window_hash: str
    strategy_version_id: UUID
    setup_definition_id: UUID
    fusion_policy_version: str
    valid_until: datetime
    live_executable: Literal[False] = False


class CanonicalEligibilityRead(StrictModel):
    authority: Literal["canonical"] = "canonical"
    evaluation: ActionEligibilityEvaluation


class CanonicalExecutionReceiptRead(StrictModel):
    authority: Literal["canonical"] = "canonical"
    live_executable: Literal[False] = False
    receipt: ExecutionReceiptView
    projection: ExecutionProjectionView | None = None


class CanonicalLearningRecordRead(StrictModel):
    authority: Literal["canonical"] = "canonical"
    record: AttributionRecord


class CanonicalLearningStatsRead(StrictModel):
    authority: Literal["canonical"] = "canonical"
    venue_mode: LearningVenueMode | None = None
    snapshot: AttributionAnalyticsSnapshot


class CanonicalPaperEvaluationRead(StrictModel):
    """Operator-visible paper evaluation. Not a trading or activation authority."""

    authority: Literal["canonical"] = "canonical"
    live_executable: Literal[False] = False
    watcher_activated: Literal[False] = False
    summary: PaperEvaluationSummary
