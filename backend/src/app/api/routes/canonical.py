"""Read-only canonical Phase 8 APIs. No Candidate or TradePlan minting."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    CanonicalEvidenceReaderDep,
    CanonicalRuntimeDep,
    PerpetualMarketMonitorServiceDep,
    SessionDep,
)
from app.evidence_pipeline.http_schemas import CanonicalEvidenceRead
from app.learning_attribution.contracts import LearningVenueMode
from app.market_monitor.http_schemas import MarketMonitorStatusRead
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
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep
from app.services.canonical_reads import CanonicalReadService

router = APIRouter(prefix="/canonical", tags=["canonical"])

_CANONICAL_READ_LIMIT = Depends(
    tenant_rate_limit_dependency("canonical:read", limit=120, window_seconds=3600, user_limit=120)
)


def _reads(session: SessionDep, runtime: CanonicalRuntimeDep) -> CanonicalReadService:
    return CanonicalReadService(session, runtime)


@router.get(
    "/candidates",
    response_model=PaginatedCanonicalCandidates,
    summary="List canonical Candidates",
    dependencies=[_CANONICAL_READ_LIMIT],
)
async def list_canonical_candidates(
    tenant: ReaderDep,
    session: SessionDep,
    runtime: CanonicalRuntimeDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedCanonicalCandidates:
    return _reads(session, runtime).list_candidates(
        organization_id=tenant.organization_id, limit=limit, offset=offset
    )


@router.get(
    "/candidates/{candidate_id}",
    response_model=CanonicalCandidateRead,
    summary="Get one canonical Candidate",
    dependencies=[_CANONICAL_READ_LIMIT],
)
async def get_canonical_candidate(
    candidate_id: UUID,
    tenant: ReaderDep,
    session: SessionDep,
    runtime: CanonicalRuntimeDep,
) -> CanonicalCandidateRead:
    return _reads(session, runtime).get_candidate(
        organization_id=tenant.organization_id, candidate_id=candidate_id
    )


@router.get(
    "/candidates/{candidate_id}/eligibility",
    response_model=CanonicalEligibilityRead,
    summary="Latest ActionEligibility for a Candidate",
    dependencies=[_CANONICAL_READ_LIMIT],
)
async def get_canonical_candidate_eligibility(
    candidate_id: UUID,
    tenant: ReaderDep,
    session: SessionDep,
    runtime: CanonicalRuntimeDep,
) -> CanonicalEligibilityRead:
    return _reads(session, runtime).get_candidate_eligibility(
        organization_id=tenant.organization_id, candidate_id=candidate_id
    )


@router.get(
    "/setup-assessments/{assessment_id}",
    response_model=CanonicalSetupAssessmentRead,
    summary="SetupAssessment identity projection",
    dependencies=[_CANONICAL_READ_LIMIT],
)
async def get_canonical_setup_assessment(
    assessment_id: UUID,
    tenant: ReaderDep,
    session: SessionDep,
    runtime: CanonicalRuntimeDep,
) -> CanonicalSetupAssessmentRead:
    return _reads(session, runtime).get_setup_assessment(
        organization_id=tenant.organization_id, assessment_id=assessment_id
    )


@router.get(
    "/executions/{receipt_id}",
    response_model=CanonicalExecutionReceiptRead,
    summary="Canonical paper execution receipt",
    dependencies=[_CANONICAL_READ_LIMIT],
)
async def get_canonical_execution_receipt(
    receipt_id: UUID,
    tenant: ReaderDep,
    session: SessionDep,
    runtime: CanonicalRuntimeDep,
) -> CanonicalExecutionReceiptRead:
    return _reads(session, runtime).get_execution_receipt(
        organization_id=tenant.organization_id, receipt_id=receipt_id
    )


@router.get(
    "/learning/records/{candidate_id}",
    response_model=CanonicalLearningRecordRead,
    summary="Canonical learning attribution for a Candidate",
    dependencies=[_CANONICAL_READ_LIMIT],
)
async def get_canonical_learning_record(
    candidate_id: UUID,
    tenant: ReaderDep,
    session: SessionDep,
    runtime: CanonicalRuntimeDep,
) -> CanonicalLearningRecordRead:
    return _reads(session, runtime).get_learning_record(
        organization_id=tenant.organization_id, candidate_id=candidate_id
    )


@router.get(
    "/learning/strategy-stats",
    response_model=CanonicalLearningStatsRead,
    summary="Canonical strategy and pattern statistics",
    dependencies=[_CANONICAL_READ_LIMIT],
)
async def get_canonical_strategy_stats(
    tenant: ReaderDep,
    session: SessionDep,
    runtime: CanonicalRuntimeDep,
    learning_venue_mode: LearningVenueMode | None = Query(default=None),
) -> CanonicalLearningStatsRead:
    return _reads(session, runtime).strategy_stats(
        organization_id=tenant.organization_id,
        learning_venue_mode=learning_venue_mode,
    )


@router.get(
    "/paper-evaluation/summary",
    response_model=CanonicalPaperEvaluationRead,
    summary="Continuous paper evaluation measurement summary",
    dependencies=[_CANONICAL_READ_LIMIT],
)
async def get_canonical_paper_evaluation(
    tenant: ReaderDep,
    session: SessionDep,
    runtime: CanonicalRuntimeDep,
    learning_venue_mode: LearningVenueMode | None = Query(default=None),
) -> CanonicalPaperEvaluationRead:
    """Watcher→outcome measurement. Does not activate Watcher or refinements."""
    return _reads(session, runtime).paper_evaluation(
        organization_id=tenant.organization_id,
        learning_venue_mode=learning_venue_mode,
    )


@router.get(
    "/evidence",
    response_model=CanonicalEvidenceRead,
    summary="Canonical USD-M evidence and current price",
    dependencies=[_CANONICAL_READ_LIMIT],
)
async def get_canonical_evidence(
    tenant: ReaderDep,
    service: CanonicalEvidenceReaderDep,
    symbol: str = Query(default="BTCUSDT", min_length=3, max_length=16),
) -> CanonicalEvidenceRead:
    """Read-only perpetual evidence. Deployed APIs serve the worker row, no upstream I/O."""
    return service.read(organization_id=tenant.organization_id, symbol=symbol)


@router.get(
    "/market-status",
    response_model=MarketMonitorStatusRead,
    summary="Continuous USD-M perpetual market status",
    dependencies=[_CANONICAL_READ_LIMIT],
)
def get_canonical_market_status(
    tenant: ReaderDep,
    service: PerpetualMarketMonitorServiceDep,
    symbol: str = Query(default="BTCUSDT", min_length=3, max_length=16),
) -> MarketMonitorStatusRead:
    """Live or replay perpetual status. Never mints Candidates or starts Watcher."""
    _ = tenant
    return service.read(symbol=symbol)
