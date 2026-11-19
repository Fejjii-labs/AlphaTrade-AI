"""Learning analytics API (Slice 84 — read-only, record derived).

All endpoints are read-only summaries over the manual paper validation workflow.
They never create orders, proposals, approvals, executions, or automation, and
never call the runtime engine, scanner, worker, exchange, or Telegram paths.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import LearningAnalyticsServiceDep
from app.schemas.learning_analytics import (
    BehaviorInsightsResponse,
    ConfidenceOutcomeResponse,
    DisciplineAnalyticsResponse,
    LearningAnalyticsSummaryResponse,
    LessonThemesResponse,
    SetupDimension,
    SetupPerformanceResponse,
    SetupRankingResponse,
)
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep

router = APIRouter(prefix="/learning-analytics", tags=["learning-analytics"])

_LEARNING_ANALYTICS_READ_LIMIT = Depends(
    tenant_rate_limit_dependency(
        "learning-analytics:read", limit=120, window_seconds=3600, user_limit=120
    )
)

_MinSample = Query(default=5, ge=1, le=100)


@router.get(
    "/summary",
    response_model=LearningAnalyticsSummaryResponse,
    summary="Paper validation learning summary",
    dependencies=[_LEARNING_ANALYTICS_READ_LIMIT],
)
def learning_summary(
    tenant: ReaderDep,
    service: LearningAnalyticsServiceDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> LearningAnalyticsSummaryResponse:
    return service.summary(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
    )


@router.get(
    "/setup-performance",
    response_model=SetupPerformanceResponse,
    summary="Setup performance by dimension",
    dependencies=[_LEARNING_ANALYTICS_READ_LIMIT],
)
def setup_performance(
    tenant: ReaderDep,
    service: LearningAnalyticsServiceDep,
    dimension: SetupDimension = Query(default=SetupDimension.CONDITION),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> SetupPerformanceResponse:
    return service.setup_performance(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        dimension=dimension,
        min_sample=min_sample,
    )


@router.get(
    "/discipline",
    response_model=DisciplineAnalyticsResponse,
    summary="Discipline analytics and score",
    dependencies=[_LEARNING_ANALYTICS_READ_LIMIT],
)
def discipline_analytics(
    tenant: ReaderDep,
    service: LearningAnalyticsServiceDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> DisciplineAnalyticsResponse:
    return service.discipline(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
    )


@router.get(
    "/confidence-outcome",
    response_model=ConfidenceOutcomeResponse,
    summary="Confidence bucket vs outcome correlation",
    dependencies=[_LEARNING_ANALYTICS_READ_LIMIT],
)
def confidence_outcome(
    tenant: ReaderDep,
    service: LearningAnalyticsServiceDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> ConfidenceOutcomeResponse:
    return service.confidence_outcome(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
    )


@router.get(
    "/behavior-insights",
    response_model=BehaviorInsightsResponse,
    summary="Derived behavior insights",
    dependencies=[_LEARNING_ANALYTICS_READ_LIMIT],
)
def behavior_insights(
    tenant: ReaderDep,
    service: LearningAnalyticsServiceDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> BehaviorInsightsResponse:
    return service.behavior_insights(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
    )


@router.get(
    "/lessons",
    response_model=LessonThemesResponse,
    summary="Recurring lesson themes",
    dependencies=[_LEARNING_ANALYTICS_READ_LIMIT],
)
def lesson_themes(
    tenant: ReaderDep,
    service: LearningAnalyticsServiceDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> LessonThemesResponse:
    return service.lessons(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
    )


@router.get(
    "/setup-ranking",
    response_model=SetupRankingResponse,
    summary="Read-only setup ranking (no automation)",
    dependencies=[_LEARNING_ANALYTICS_READ_LIMIT],
)
def setup_ranking(
    tenant: ReaderDep,
    service: LearningAnalyticsServiceDep,
    dimension: SetupDimension = Query(default=SetupDimension.CONDITION),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> SetupRankingResponse:
    return service.setup_ranking(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        dimension=dimension,
        min_sample=min_sample,
    )
