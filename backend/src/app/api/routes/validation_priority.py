"""Validation prioritization API (Slice 85 — read-only, record derived).

All endpoints are read-only rankings of pending paper validation run plans and
candidates. They never create orders, proposals, approvals, executions, or
automation, never start a validation session, and never call the runtime
engine, scanner, worker, exchange, or Telegram paths.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import ValidationPriorityServiceDep
from app.schemas.validation_priority import (
    PriorityItemType,
    ValidationPriorityExplainResponse,
    ValidationPriorityQueueResponse,
    ValidationPrioritySummaryResponse,
)
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep

router = APIRouter(prefix="/validation-priority", tags=["validation-priority"])

_VALIDATION_PRIORITY_READ_LIMIT = Depends(
    tenant_rate_limit_dependency(
        "validation-priority:read", limit=120, window_seconds=3600, user_limit=120
    )
)

_MinSample = Query(default=5, ge=1, le=100)
_Limit = Query(default=20, ge=1, le=100)


@router.get(
    "/queue",
    response_model=ValidationPriorityQueueResponse,
    summary="Ranked queue of pending setups to validate next",
    dependencies=[_VALIDATION_PRIORITY_READ_LIMIT],
)
def validation_priority_queue(
    tenant: ReaderDep,
    service: ValidationPriorityServiceDep,
    item_type: PriorityItemType | None = Query(default=None),
    limit: int = _Limit,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> ValidationPriorityQueueResponse:
    return service.queue(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
        item_type=item_type,
        limit=limit,
    )


@router.get(
    "/summary",
    response_model=ValidationPrioritySummaryResponse,
    summary="Pending setup counts by action label and reliability",
    dependencies=[_VALIDATION_PRIORITY_READ_LIMIT],
)
def validation_priority_summary(
    tenant: ReaderDep,
    service: ValidationPriorityServiceDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> ValidationPrioritySummaryResponse:
    return service.summary(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
    )


@router.get(
    "/explain/{item_type}/{item_id}",
    response_model=ValidationPriorityExplainResponse,
    summary="Detailed priority factor breakdown for one pending setup",
    dependencies=[_VALIDATION_PRIORITY_READ_LIMIT],
)
def validation_priority_explain(
    tenant: ReaderDep,
    service: ValidationPriorityServiceDep,
    item_type: PriorityItemType,
    item_id: uuid.UUID,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> ValidationPriorityExplainResponse:
    return service.explain(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
        item_type=item_type,
        item_id=item_id,
    )
