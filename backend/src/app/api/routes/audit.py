"""Audit event query API."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.auth import TenantDep
from app.core.dependencies import AuditServiceDep
from app.schemas.audit import PaginatedAuditRecords
from app.schemas.common import AuditEventType

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events", response_model=PaginatedAuditRecords, summary="List audit events")
def list_audit_events(
    tenant: TenantDep,
    audit_service: AuditServiceDep,
    request_id: str | None = Query(default=None),
    event_type: AuditEventType | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedAuditRecords:
    items, total = audit_service.list_records(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        request_id=request_id,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    return PaginatedAuditRecords(items=items, total=total, limit=limit, offset=offset)
