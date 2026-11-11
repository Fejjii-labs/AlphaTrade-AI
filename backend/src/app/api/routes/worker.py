"""Background worker health and manual pause/resume endpoints (Slice 59)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request

from app.core.dependencies import SessionDep, SettingsDep
from app.observability.context import get_or_create_trace_id
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType
from app.schemas.worker import WorkerControlResponse, WorkerHealthResponse
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import PlatformOperatorDep, ReaderDep
from app.security.tenant import TenantContext
from app.services.audit_service import AuditService
from app.workers.repository import MarketScanRunRepository, WorkerHeartbeatRepository

router = APIRouter(prefix="/worker", tags=["worker"])

_WORKER_READ_LIMIT = Depends(
    tenant_rate_limit_dependency("worker:read", limit=120, window_seconds=3600)
)
_WORKER_WRITE_LIMIT = Depends(
    tenant_rate_limit_dependency("worker:write", limit=30, window_seconds=3600)
)


@router.get(
    "/health",
    response_model=WorkerHealthResponse,
    summary="Background worker health",
    dependencies=[_WORKER_READ_LIMIT],
)
async def worker_health(
    _tenant: ReaderDep,
    settings: SettingsDep,
    session: SessionDep,
) -> WorkerHealthResponse:
    heartbeat = WorkerHeartbeatRepository(session).get_by_name(settings.worker_name)
    failures = len(MarketScanRunRepository(session).list_dead_letters(limit=50))

    if heartbeat is None:
        return WorkerHealthResponse(
            worker_name=settings.worker_name,
            configured=settings.worker_enabled,
            running=False,
            paused=False,
            status="unknown",
            cycle_count=0,
            last_beat_at=None,
            seconds_since_beat=None,
            recent_failures=failures,
            detail="No heartbeat recorded yet.",
        )

    last_beat = heartbeat.last_beat_at
    if last_beat.tzinfo is None:  # SQLite returns naive datetimes
        last_beat = last_beat.replace(tzinfo=UTC)
    seconds_since = (datetime.now(UTC) - last_beat).total_seconds()
    # Consider live if a beat arrived within three scan intervals.
    liveness_window = settings.worker_scan_interval_seconds * 3
    running = seconds_since <= liveness_window and heartbeat.status != "error"

    return WorkerHealthResponse(
        worker_name=heartbeat.worker_name,
        configured=settings.worker_enabled,
        running=running and not heartbeat.paused,
        paused=heartbeat.paused,
        status=heartbeat.status,
        cycle_count=heartbeat.cycle_count,
        last_beat_at=last_beat,
        seconds_since_beat=seconds_since,
        recent_failures=failures,
        detail=heartbeat.detail,
    )


@router.post(
    "/pause",
    response_model=WorkerControlResponse,
    summary="Pause the background worker",
    dependencies=[_WORKER_WRITE_LIMIT],
)
async def worker_pause(
    request: Request,
    tenant: PlatformOperatorDep,
    settings: SettingsDep,
    session: SessionDep,
) -> WorkerControlResponse:
    return _set_paused(session, settings.worker_name, request=request, tenant=tenant, paused=True)


@router.post(
    "/resume",
    response_model=WorkerControlResponse,
    summary="Resume the background worker",
    dependencies=[_WORKER_WRITE_LIMIT],
)
async def worker_resume(
    request: Request,
    tenant: PlatformOperatorDep,
    settings: SettingsDep,
    session: SessionDep,
) -> WorkerControlResponse:
    return _set_paused(session, settings.worker_name, request=request, tenant=tenant, paused=False)


def _set_paused(
    session: SessionDep,
    worker_name: str,
    *,
    request: Request,
    tenant: TenantContext,
    paused: bool,
) -> WorkerControlResponse:
    repo = WorkerHeartbeatRepository(session)
    row = repo.set_paused(worker_name=worker_name, paused=paused)
    # Fail closed if the audit cannot persist, committing it with the control change.
    AuditService(session, strict_mode=True).record(
        AuditRecordCreate(
            request_id=getattr(request.state, "request_id", ""),
            trace_id=get_or_create_trace_id(),
            event_type=AuditEventType.WORKER_PAUSED if paused else AuditEventType.WORKER_RESUMED,
            resource_type="worker",
            resource_id=worker_name,
            actor_type=ActorType.USER,
            user_id=tenant.user_id,
            organization_id=tenant.organization_id,
            metadata={"worker_name": worker_name, "paused": paused},
        )
    )
    session.commit()
    return WorkerControlResponse(worker_name=row.worker_name, paused=row.paused, status=row.status)
