"""Paper-signal orchestration API (AT-038 — paper-only)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import PaperSignalOrchestrationServiceDep, SessionDep
from app.schemas.common import PaperSignalOrchestrationStatus
from app.schemas.paper_signal_orchestration import (
    PaperSignalOrchestrationApproveRequest,
    PaperSignalOrchestrationApproveResult,
    PaperSignalOrchestrationDecisionItem,
    PaperSignalOrchestrationEvaluateResult,
    PaperSignalOrchestrationListResponse,
)
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep, TraderDep

router = APIRouter(tags=["paper-signal-orchestration"])

_READ_LIMIT = Depends(
    tenant_rate_limit_dependency("paper_signal_orch:read", limit=120, window_seconds=3600)
)
_WRITE_LIMIT = Depends(
    tenant_rate_limit_dependency("paper_signal_orch:write", limit=60, window_seconds=3600)
)


@router.get(
    "/paper-signal-orchestration/decisions",
    response_model=PaperSignalOrchestrationListResponse,
    summary="List paper-signal orchestration decisions",
    dependencies=[_READ_LIMIT],
)
def list_decisions(
    tenant: ReaderDep,
    service: PaperSignalOrchestrationServiceDep,
    status: PaperSignalOrchestrationStatus | None = Query(default=None),
    symbol: str | None = Query(default=None, max_length=30),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> PaperSignalOrchestrationListResponse:
    return service.list_decisions(
        organization_id=tenant.organization_id,
        status=status,
        symbol=symbol,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/paper-signal-orchestration/decisions/{decision_id}",
    response_model=PaperSignalOrchestrationDecisionItem,
    summary="Paper-signal orchestration decision detail",
    dependencies=[_READ_LIMIT],
)
def get_decision(
    decision_id: uuid.UUID,
    tenant: ReaderDep,
    service: PaperSignalOrchestrationServiceDep,
) -> PaperSignalOrchestrationDecisionItem:
    return service.get_decision(decision_id, organization_id=tenant.organization_id)


@router.post(
    "/paper-signal-orchestration/signals/{signal_id}/evaluate",
    response_model=PaperSignalOrchestrationEvaluateResult,
    summary="Evaluate TradingView signal eligibility (no mode side effects)",
    dependencies=[_WRITE_LIMIT],
)
def evaluate_signal(
    signal_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperSignalOrchestrationServiceDep,
    session: SessionDep,
    request: Request,
) -> PaperSignalOrchestrationEvaluateResult:
    result = service.evaluate(
        signal_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        request_id=getattr(request.state, "request_id", None),
        advance=False,
    )
    session.commit()
    return result


@router.post(
    "/paper-signal-orchestration/signals/{signal_id}/orchestrate",
    response_model=PaperSignalOrchestrationEvaluateResult,
    summary="Evaluate and advance paper-signal orchestration by configured mode",
    dependencies=[_WRITE_LIMIT],
)
def orchestrate_signal(
    signal_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperSignalOrchestrationServiceDep,
    session: SessionDep,
    request: Request,
) -> PaperSignalOrchestrationEvaluateResult:
    result = service.orchestrate(
        signal_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return result


@router.post(
    "/paper-signal-orchestration/decisions/{decision_id}/approve-paper-proposal",
    response_model=PaperSignalOrchestrationApproveResult,
    summary="Approve creation of a paper trade proposal (approval_required mode)",
    dependencies=[_WRITE_LIMIT],
)
def approve_paper_proposal(
    decision_id: uuid.UUID,
    payload: PaperSignalOrchestrationApproveRequest,
    tenant: TraderDep,
    service: PaperSignalOrchestrationServiceDep,
    session: SessionDep,
    request: Request,
) -> PaperSignalOrchestrationApproveResult:
    result = service.approve_paper_proposal(
        decision_id,
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    session.commit()
    return result
