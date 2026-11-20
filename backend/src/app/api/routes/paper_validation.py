"""Paper validation runtime API (Slice 39-40 — paper only)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    PaperSchedulerServiceDep,
    PaperValidationCandidateServiceDep,
    PaperValidationDraftServiceDep,
    PaperValidationRunPlanServiceDep,
    PaperValidationRunSessionServiceDep,
    PaperValidationRuntimeServiceDep,
    PaperValidationSessionObservationServiceDep,
    PaperValidationSessionResultServiceDep,
    SessionDep,
)
from app.schemas.common import PaperTradeStatus
from app.schemas.paper_scheduler import (
    PaginatedPaperRuntimeHistory,
    PaperSchedulerConfigUpdate,
    PaperSchedulerStatus,
    PaperSchedulerTickResult,
)
from app.schemas.paper_validation import (
    PaginatedPaperPositions,
    PaginatedPaperSignals,
    PaginatedPaperTrades,
    PaperScanResult,
    PaperTickResult,
    PaperValidationMetrics,
    PaperValidationRun,
)
from app.schemas.paper_validation_candidate import (
    PaginatedPaperValidationCandidates,
    PaperValidationCandidateItem,
    PaperValidationCandidateQueueRequest,
    PaperValidationCandidateQueueResult,
    PaperValidationCandidateStatusUpdate,
    PaperValidationCandidateSummary,
)
from app.schemas.paper_validation_draft import (
    PaginatedPaperValidationDrafts,
    PaperValidationDraftItem,
    PaperValidationDraftPrepUpdateRequest,
    PaperValidationDraftSummary,
)
from app.schemas.paper_validation_run_plan import (
    PaginatedPaperValidationRunPlans,
    PaperValidationRunPlanCreateRequest,
    PaperValidationRunPlanCreateResult,
    PaperValidationRunPlanItem,
    PaperValidationRunPlanStatusUpdate,
    PaperValidationRunPlanSummary,
)
from app.schemas.paper_validation_run_session import (
    PaginatedPaperValidationRunSessions,
    PaperValidationRunSessionItem,
    PaperValidationRunSessionStartRequest,
    PaperValidationRunSessionStartResult,
    PaperValidationRunSessionStatusUpdate,
)
from app.schemas.paper_validation_session_observation import (
    PaginatedPaperValidationSessionObservations,
    PaperValidationSessionObservationCreateRequest,
    PaperValidationSessionObservationItem,
)
from app.schemas.paper_validation_session_result import (
    PaperValidationSessionResultCreateRequest,
    PaperValidationSessionResultCreateResult,
    PaperValidationSessionResultItem,
    PaperValidationSessionResultUpdateRequest,
)
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import OwnerDep, ReaderDep, TraderDep

router = APIRouter(prefix="/paper-validation", tags=["paper-validation"])

_PAPER_SCHEDULER_READ = Depends(
    tenant_rate_limit_dependency("paper-validation:scheduler:read", limit=120, window_seconds=3600)
)
_PAPER_SCHEDULER_WRITE = Depends(
    tenant_rate_limit_dependency("paper-validation:scheduler:write", limit=30, window_seconds=3600)
)
_PAPER_RUNTIME_WRITE = Depends(
    tenant_rate_limit_dependency("paper-validation:runtime:write", limit=60, window_seconds=3600)
)


@router.get(
    "/scheduler/status",
    response_model=PaperSchedulerStatus,
    summary="Paper validation scheduler status",
    dependencies=[_PAPER_SCHEDULER_READ],
)
def get_scheduler_status(
    tenant: TraderDep,
    service: PaperSchedulerServiceDep,
) -> PaperSchedulerStatus:
    return service.get_status(organization_id=tenant.organization_id)


@router.post(
    "/scheduler/tick",
    response_model=PaperSchedulerTickResult,
    summary="Manual paper validation scheduler tick",
    dependencies=[_PAPER_SCHEDULER_WRITE],
)
def scheduler_tick(
    tenant: OwnerDep,
    service: PaperSchedulerServiceDep,
    session: SessionDep,
) -> PaperSchedulerTickResult:
    result = service.tick(organization_id=tenant.organization_id, user_id=tenant.user_id)
    session.commit()
    return result


@router.patch(
    "/scheduler/config",
    response_model=PaperSchedulerStatus,
    summary="Update tenant paper scheduler config",
    dependencies=[_PAPER_SCHEDULER_WRITE],
)
def update_scheduler_config(
    payload: PaperSchedulerConfigUpdate,
    tenant: OwnerDep,
    service: PaperSchedulerServiceDep,
    session: SessionDep,
) -> PaperSchedulerStatus:
    result = service.update_config(
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/scheduler/history",
    response_model=PaginatedPaperRuntimeHistory,
    summary="Scheduler and runtime cycle history",
    dependencies=[_PAPER_SCHEDULER_READ],
)
def list_scheduler_history(
    tenant: TraderDep,
    service: PaperSchedulerServiceDep,
    run_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedPaperRuntimeHistory:
    return service.list_history(
        organization_id=tenant.organization_id,
        run_id=run_id,
        limit=limit,
        offset=offset,
    )


_PAPER_DRAFT_READ = Depends(
    tenant_rate_limit_dependency("paper-validation:drafts:read", limit=120, window_seconds=3600)
)
_PAPER_DRAFT_PREP_WRITE = Depends(
    tenant_rate_limit_dependency("paper-validation:drafts:prep", limit=60, window_seconds=3600)
)
_PAPER_CANDIDATE_READ = Depends(
    tenant_rate_limit_dependency("paper-validation:candidates:read", limit=120, window_seconds=3600)
)
_PAPER_CANDIDATE_WRITE = Depends(
    tenant_rate_limit_dependency("paper-validation:candidates:write", limit=60, window_seconds=3600)
)
_PAPER_RUN_PLAN_READ = Depends(
    tenant_rate_limit_dependency("paper-validation:run-plans:read", limit=120, window_seconds=3600)
)
_PAPER_RUN_PLAN_WRITE = Depends(
    tenant_rate_limit_dependency("paper-validation:run-plans:write", limit=60, window_seconds=3600)
)
_PAPER_RUN_SESSION_READ = Depends(
    tenant_rate_limit_dependency(
        "paper-validation:run-sessions:read", limit=120, window_seconds=3600
    )
)
_PAPER_RUN_SESSION_WRITE = Depends(
    tenant_rate_limit_dependency(
        "paper-validation:run-sessions:write", limit=30, window_seconds=3600
    )
)
_PAPER_SESSION_OBSERVATION_WRITE = Depends(
    tenant_rate_limit_dependency(
        "paper-validation:session-observations:write", limit=60, window_seconds=3600
    )
)
_PAPER_SESSION_RESULT_WRITE = Depends(
    tenant_rate_limit_dependency(
        "paper-validation:session-results:write", limit=30, window_seconds=3600
    )
)


@router.get(
    "/drafts/summary",
    response_model=PaperValidationDraftSummary,
    summary="Paper validation draft summary",
    dependencies=[_PAPER_DRAFT_READ],
)
def paper_validation_draft_summary(
    tenant: ReaderDep,
    service: PaperValidationDraftServiceDep,
) -> PaperValidationDraftSummary:
    return service.draft_summary(tenant.organization_id)


@router.get(
    "/drafts",
    response_model=PaginatedPaperValidationDrafts,
    summary="List non-executable paper validation drafts",
    dependencies=[_PAPER_DRAFT_READ],
)
def list_paper_validation_drafts(
    tenant: ReaderDep,
    service: PaperValidationDraftServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedPaperValidationDrafts:
    return service.list_drafts(
        tenant.organization_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/drafts/{draft_id}",
    response_model=PaperValidationDraftItem,
    summary="Get a paper validation draft",
    dependencies=[_PAPER_DRAFT_READ],
)
def get_paper_validation_draft(
    draft_id: uuid.UUID,
    tenant: ReaderDep,
    service: PaperValidationDraftServiceDep,
) -> PaperValidationDraftItem:
    return service.get_draft(draft_id, organization_id=tenant.organization_id)


@router.patch(
    "/drafts/{draft_id}/prep",
    response_model=PaperValidationDraftItem,
    summary="Update paper validation draft prep context (planning only)",
    dependencies=[_PAPER_DRAFT_PREP_WRITE],
)
def update_paper_validation_draft_prep(
    draft_id: uuid.UUID,
    payload: PaperValidationDraftPrepUpdateRequest,
    tenant: TraderDep,
    service: PaperValidationDraftServiceDep,
    session: SessionDep,
) -> PaperValidationDraftItem:
    result = service.update_prep(
        draft_id,
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/drafts/{draft_id}/queue",
    response_model=PaperValidationCandidateQueueResult,
    summary="Queue a ready paper validation draft as a candidate (no run)",
    dependencies=[_PAPER_CANDIDATE_WRITE],
)
def queue_paper_validation_candidate(
    draft_id: uuid.UUID,
    payload: PaperValidationCandidateQueueRequest,
    tenant: TraderDep,
    service: PaperValidationCandidateServiceDep,
    session: SessionDep,
) -> PaperValidationCandidateQueueResult:
    result = service.queue_from_draft(
        draft_id,
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/candidates/summary",
    response_model=PaperValidationCandidateSummary,
    summary="Paper validation candidate queue summary",
    dependencies=[_PAPER_CANDIDATE_READ],
)
def paper_validation_candidate_summary(
    tenant: ReaderDep,
    service: PaperValidationCandidateServiceDep,
) -> PaperValidationCandidateSummary:
    return service.candidate_summary(tenant.organization_id)


@router.get(
    "/candidates",
    response_model=PaginatedPaperValidationCandidates,
    summary="List paper validation candidates",
    dependencies=[_PAPER_CANDIDATE_READ],
)
def list_paper_validation_candidates(
    tenant: ReaderDep,
    service: PaperValidationCandidateServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedPaperValidationCandidates:
    return service.list_candidates(
        tenant.organization_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/candidates/{candidate_id}",
    response_model=PaperValidationCandidateItem,
    summary="Get a paper validation candidate",
    dependencies=[_PAPER_CANDIDATE_READ],
)
def get_paper_validation_candidate(
    candidate_id: uuid.UUID,
    tenant: ReaderDep,
    service: PaperValidationCandidateServiceDep,
) -> PaperValidationCandidateItem:
    return service.get_candidate(candidate_id, organization_id=tenant.organization_id)


@router.patch(
    "/candidates/{candidate_id}",
    response_model=PaperValidationCandidateItem,
    summary="Update paper validation candidate status (no run)",
    dependencies=[_PAPER_CANDIDATE_WRITE],
)
def update_paper_validation_candidate_status(
    candidate_id: uuid.UUID,
    payload: PaperValidationCandidateStatusUpdate,
    tenant: TraderDep,
    service: PaperValidationCandidateServiceDep,
    session: SessionDep,
) -> PaperValidationCandidateItem:
    result = service.update_status(
        candidate_id,
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/candidates/{candidate_id}/plan",
    response_model=PaperValidationRunPlanCreateResult,
    summary="Create a paper validation run plan from a reviewing candidate (no run)",
    dependencies=[_PAPER_RUN_PLAN_WRITE],
)
def create_paper_validation_run_plan(
    candidate_id: uuid.UUID,
    payload: PaperValidationRunPlanCreateRequest,
    tenant: TraderDep,
    service: PaperValidationRunPlanServiceDep,
    session: SessionDep,
) -> PaperValidationRunPlanCreateResult:
    result = service.create_from_candidate(
        candidate_id,
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/run-plans/summary",
    response_model=PaperValidationRunPlanSummary,
    summary="Paper validation run plan summary",
    dependencies=[_PAPER_RUN_PLAN_READ],
)
def paper_validation_run_plan_summary(
    tenant: ReaderDep,
    service: PaperValidationRunPlanServiceDep,
) -> PaperValidationRunPlanSummary:
    return service.plan_summary(tenant.organization_id)


@router.get(
    "/run-plans",
    response_model=PaginatedPaperValidationRunPlans,
    summary="List paper validation run plans",
    dependencies=[_PAPER_RUN_PLAN_READ],
)
def list_paper_validation_run_plans(
    tenant: ReaderDep,
    service: PaperValidationRunPlanServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedPaperValidationRunPlans:
    return service.list_plans(
        tenant.organization_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/run-plans/{plan_id}",
    response_model=PaperValidationRunPlanItem,
    summary="Get a paper validation run plan",
    dependencies=[_PAPER_RUN_PLAN_READ],
)
def get_paper_validation_run_plan(
    plan_id: uuid.UUID,
    tenant: ReaderDep,
    service: PaperValidationRunPlanServiceDep,
) -> PaperValidationRunPlanItem:
    return service.get_plan(plan_id, organization_id=tenant.organization_id)


@router.patch(
    "/run-plans/{plan_id}",
    response_model=PaperValidationRunPlanItem,
    summary="Update paper validation run plan status (no run)",
    dependencies=[_PAPER_RUN_PLAN_WRITE],
)
def update_paper_validation_run_plan_status(
    plan_id: uuid.UUID,
    payload: PaperValidationRunPlanStatusUpdate,
    tenant: TraderDep,
    service: PaperValidationRunPlanServiceDep,
    session: SessionDep,
) -> PaperValidationRunPlanItem:
    result = service.update_status(
        plan_id,
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/run-plans/{plan_id}/start",
    response_model=PaperValidationRunSessionStartResult,
    summary="Manually start a paper validation run session from a planned run plan (no engine)",
    dependencies=[_PAPER_RUN_SESSION_WRITE],
)
def start_paper_validation_run_session(
    plan_id: uuid.UUID,
    payload: PaperValidationRunSessionStartRequest,
    tenant: OwnerDep,
    service: PaperValidationRunSessionServiceDep,
    session: SessionDep,
) -> PaperValidationRunSessionStartResult:
    result = service.start_from_plan(
        plan_id,
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/run-sessions",
    response_model=PaginatedPaperValidationRunSessions,
    summary="List paper validation run sessions",
    dependencies=[_PAPER_RUN_SESSION_READ],
)
def list_paper_validation_run_sessions(
    tenant: ReaderDep,
    service: PaperValidationRunSessionServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedPaperValidationRunSessions:
    return service.list_sessions(
        tenant.organization_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/run-sessions/{session_id}",
    response_model=PaperValidationRunSessionItem,
    summary="Get a paper validation run session",
    dependencies=[_PAPER_RUN_SESSION_READ],
)
def get_paper_validation_run_session(
    session_id: uuid.UUID,
    tenant: ReaderDep,
    service: PaperValidationRunSessionServiceDep,
) -> PaperValidationRunSessionItem:
    return service.get_session(session_id, organization_id=tenant.organization_id)


@router.patch(
    "/run-sessions/{session_id}",
    response_model=PaperValidationRunSessionItem,
    summary="Complete or cancel a paper validation run session (no engine)",
    dependencies=[_PAPER_RUN_SESSION_WRITE],
)
def update_paper_validation_run_session_status(
    session_id: uuid.UUID,
    payload: PaperValidationRunSessionStatusUpdate,
    tenant: OwnerDep,
    service: PaperValidationRunSessionServiceDep,
    session: SessionDep,
) -> PaperValidationRunSessionItem:
    result = service.update_status(
        session_id,
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/run-sessions/{session_id}/observations",
    response_model=PaginatedPaperValidationSessionObservations,
    summary="List observations for a paper validation run session",
    dependencies=[_PAPER_RUN_SESSION_READ],
)
def list_paper_validation_session_observations(
    session_id: uuid.UUID,
    tenant: ReaderDep,
    service: PaperValidationSessionObservationServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedPaperValidationSessionObservations:
    return service.list_observations(
        session_id,
        organization_id=tenant.organization_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/run-sessions/{session_id}/observations",
    response_model=PaperValidationSessionObservationItem,
    summary="Record a manual observation for a running session (no engine)",
    dependencies=[_PAPER_SESSION_OBSERVATION_WRITE],
)
def record_paper_validation_session_observation(
    session_id: uuid.UUID,
    payload: PaperValidationSessionObservationCreateRequest,
    tenant: TraderDep,
    service: PaperValidationSessionObservationServiceDep,
    session: SessionDep,
) -> PaperValidationSessionObservationItem:
    result = service.record_observation(
        session_id,
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/run-sessions/{session_id}/result",
    response_model=PaperValidationSessionResultItem,
    summary="Get the outcome result for a paper validation run session",
    dependencies=[_PAPER_RUN_SESSION_READ],
)
def get_paper_validation_session_result(
    session_id: uuid.UUID,
    tenant: ReaderDep,
    service: PaperValidationSessionResultServiceDep,
) -> PaperValidationSessionResultItem:
    return service.get_result(session_id, organization_id=tenant.organization_id)


@router.post(
    "/run-sessions/{session_id}/result",
    response_model=PaperValidationSessionResultCreateResult,
    summary="Record the outcome for a running session (no engine)",
    dependencies=[_PAPER_SESSION_RESULT_WRITE],
)
def record_paper_validation_session_result(
    session_id: uuid.UUID,
    payload: PaperValidationSessionResultCreateRequest,
    tenant: OwnerDep,
    service: PaperValidationSessionResultServiceDep,
    session: SessionDep,
) -> PaperValidationSessionResultCreateResult:
    result = service.record_result(
        session_id,
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.patch(
    "/run-sessions/{session_id}/result",
    response_model=PaperValidationSessionResultItem,
    summary="Update the outcome for a running session (no engine)",
    dependencies=[_PAPER_SESSION_RESULT_WRITE],
)
def update_paper_validation_session_result(
    session_id: uuid.UUID,
    payload: PaperValidationSessionResultUpdateRequest,
    tenant: OwnerDep,
    service: PaperValidationSessionResultServiceDep,
    session: SessionDep,
) -> PaperValidationSessionResultItem:
    result = service.update_result(
        session_id,
        payload,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/{run_id}",
    response_model=PaperValidationRun,
    summary="Get paper validation run",
)
def get_paper_validation_run(
    run_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperValidationRuntimeServiceDep,
) -> PaperValidationRun:
    return service.get_run(run_id, organization_id=tenant.organization_id)


@router.post(
    "/{run_id}/scan",
    response_model=PaperScanResult,
    summary="Scan market for paper signals",
    dependencies=[_PAPER_RUNTIME_WRITE],
)
def scan_paper_validation(
    run_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperValidationRuntimeServiceDep,
    session: SessionDep,
) -> PaperScanResult:
    result = service.scan(
        run_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/{run_id}/tick",
    response_model=PaperTickResult,
    summary="Advance paper trade monitoring (manual tick)",
    dependencies=[_PAPER_RUNTIME_WRITE],
)
def tick_paper_validation(
    run_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperValidationRuntimeServiceDep,
    session: SessionDep,
) -> PaperTickResult:
    result = service.tick(
        run_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/{run_id}/stop",
    response_model=PaperValidationRun,
    summary="Stop paper validation run",
    dependencies=[_PAPER_RUNTIME_WRITE],
)
def stop_paper_validation(
    run_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperValidationRuntimeServiceDep,
    session: SessionDep,
) -> PaperValidationRun:
    result = service.stop(
        run_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/{run_id}/signals",
    response_model=PaginatedPaperSignals,
    summary="List paper signals for a run",
)
def list_paper_signals(
    run_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperValidationRuntimeServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedPaperSignals:
    return service.list_signals(
        run_id,
        organization_id=tenant.organization_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{run_id}/trades",
    response_model=PaginatedPaperTrades,
    summary="List paper trades for a run",
)
def list_paper_trades(
    run_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperValidationRuntimeServiceDep,
    status: PaperTradeStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> PaginatedPaperTrades:
    return service.list_trades(
        run_id,
        organization_id=tenant.organization_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{run_id}/positions",
    response_model=PaginatedPaperPositions,
    summary="List open paper positions",
)
def list_paper_positions(
    run_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperValidationRuntimeServiceDep,
) -> PaginatedPaperPositions:
    items = service.list_open_positions(run_id, organization_id=tenant.organization_id)
    return PaginatedPaperPositions(items=items, total=len(items))


@router.get(
    "/{run_id}/metrics",
    response_model=PaperValidationMetrics,
    summary="Paper validation metrics for a run",
)
def get_paper_validation_metrics(
    run_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperValidationRuntimeServiceDep,
) -> PaperValidationMetrics:
    return service.get_metrics(run_id, organization_id=tenant.organization_id)
