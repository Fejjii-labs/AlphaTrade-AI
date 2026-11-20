"""Research Validation Loop API (AT-035 — advisory paper-queue promotion)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import ResearchValidationServiceDep, SessionDep
from app.schemas.research_validation import (
    ResearchValidationEvidenceResponse,
    ResearchValidationPromoteRequest,
    ResearchValidationPromoteResult,
    ResearchValidationStatusResponse,
)
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep, TraderDep

router = APIRouter(prefix="/research-validation", tags=["research-validation"])

_RESEARCH_VALIDATION_READ = Depends(
    tenant_rate_limit_dependency("research-validation:read", limit=120, window_seconds=3600)
)
_RESEARCH_VALIDATION_WRITE = Depends(
    tenant_rate_limit_dependency("research-validation:write", limit=60, window_seconds=3600)
)


@router.get(
    "/evidence",
    response_model=ResearchValidationEvidenceResponse,
    summary="List research validation evidence for completed backtests",
    dependencies=[_RESEARCH_VALIDATION_READ],
)
def list_research_validation_evidence(
    tenant: ReaderDep,
    service: ResearchValidationServiceDep,
    backtest_run_id: uuid.UUID | None = Query(default=None),
    strategy_id: uuid.UUID | None = Query(default=None),
    strategy_version_id: uuid.UUID | None = Query(default=None),
) -> ResearchValidationEvidenceResponse:
    return service.list_evidence(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        backtest_run_id=backtest_run_id,
        strategy_id=strategy_id,
        strategy_version_id=strategy_version_id,
    )


@router.post(
    "/promote",
    response_model=ResearchValidationPromoteResult,
    summary="Promote eligible research evidence into the paper validation candidate queue",
    dependencies=[_RESEARCH_VALIDATION_WRITE],
)
def promote_research_validation_candidate(
    payload: ResearchValidationPromoteRequest,
    tenant: TraderDep,
    service: ResearchValidationServiceDep,
    session: SessionDep,
) -> ResearchValidationPromoteResult:
    result = service.promote(
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/backtests/{backtest_run_id}/status",
    response_model=ResearchValidationStatusResponse,
    summary="Research validation status for a backtest run",
    dependencies=[_RESEARCH_VALIDATION_READ],
)
def research_validation_backtest_status(
    backtest_run_id: uuid.UUID,
    tenant: ReaderDep,
    service: ResearchValidationServiceDep,
) -> ResearchValidationStatusResponse:
    return service.get_status(
        backtest_run_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
