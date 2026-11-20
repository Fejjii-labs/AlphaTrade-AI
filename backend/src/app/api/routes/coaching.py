"""Coaching API (Slice 87 — read-only prompts + optional lesson journaling).

Read endpoints compute deterministic coaching prompts from existing paper
validation records. The save endpoint persists into the existing lesson
candidate workflow only — never orders, proposals, approvals, execution,
exchange, engine, scanner, worker, or Telegram paths.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import CoachingServiceDep, SessionDep
from app.schemas.coaching import (
    CoachingCategory,
    CoachingExplainResponse,
    CoachingPromptsResponse,
    CoachingSaveRequest,
    CoachingSummaryResponse,
)
from app.schemas.common import LessonSeverity
from app.schemas.lesson import LessonCandidate
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep, TraderDep

router = APIRouter(prefix="/coaching", tags=["coaching"])

_COACHING_READ_LIMIT = Depends(
    tenant_rate_limit_dependency("coaching:read", limit=120, window_seconds=3600, user_limit=120)
)

_MinSample = Query(default=5, ge=1, le=100)
_Limit = Query(default=20, ge=1, le=100)


@router.get(
    "/prompts",
    response_model=CoachingPromptsResponse,
    summary="Live-computed coaching prompts from validation outcomes",
    dependencies=[_COACHING_READ_LIMIT],
)
def coaching_prompts(
    tenant: ReaderDep,
    service: CoachingServiceDep,
    category: CoachingCategory | None = Query(default=None),
    severity: LessonSeverity | None = Query(default=None),
    limit: int = _Limit,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> CoachingPromptsResponse:
    return service.prompts(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
        category=category,
        severity=severity,
        limit=limit,
        saved_lookup_user_id=tenant.user_id,
    )


@router.get(
    "/summary",
    response_model=CoachingSummaryResponse,
    summary="Coaching prompt counts by category and severity",
    dependencies=[_COACHING_READ_LIMIT],
)
def coaching_summary(
    tenant: ReaderDep,
    service: CoachingServiceDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> CoachingSummaryResponse:
    return service.summary(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
        saved_lookup_user_id=tenant.user_id,
    )


@router.get(
    "/prompts/{category}/{matched_key}/explain",
    response_model=CoachingExplainResponse,
    summary="Detailed factor breakdown for one coaching pattern",
    dependencies=[_COACHING_READ_LIMIT],
)
def coaching_explain(
    tenant: ReaderDep,
    service: CoachingServiceDep,
    category: CoachingCategory,
    matched_key: str,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> CoachingExplainResponse:
    return service.explain(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
        category=category,
        matched_key=matched_key,
        saved_lookup_user_id=tenant.user_id,
    )


@router.post(
    "/prompts/save",
    response_model=LessonCandidate,
    summary="Save a coaching prompt into the lesson review queue",
)
def coaching_save_prompt(
    body: CoachingSaveRequest,
    tenant: TraderDep,
    service: CoachingServiceDep,
    session: SessionDep,
) -> LessonCandidate:
    result = service.save_prompt(
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result
