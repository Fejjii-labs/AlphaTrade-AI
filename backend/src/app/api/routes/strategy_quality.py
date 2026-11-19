"""Strategy quality and detector performance API (Slice 89 — read-only).

All endpoints are read-only detector performance analytics derived from manual
paper validation outcomes. They never create orders, proposals, approvals,
executions, or automation, never change strategy rules, never enable or disable
detectors, and never call the runtime engine, scanner, worker, exchange, or
Telegram paths.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import StrategyQualityServiceDep
from app.schemas.strategy_quality import (
    DetectorExplainResponse,
    StrategyQualityDetectorsResponse,
    StrategyQualitySummaryResponse,
)
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep

router = APIRouter(prefix="/strategy-quality", tags=["strategy-quality"])

_STRATEGY_QUALITY_READ_LIMIT = Depends(
    tenant_rate_limit_dependency(
        "strategy-quality:read", limit=120, window_seconds=3600, user_limit=120
    )
)

_MinSample = Query(default=5, ge=1, le=100)


@router.get(
    "/detectors",
    response_model=StrategyQualityDetectorsResponse,
    summary="Per-detector quality reports from paper validation outcomes",
    dependencies=[_STRATEGY_QUALITY_READ_LIMIT],
)
def strategy_quality_detectors(
    tenant: ReaderDep,
    service: StrategyQualityServiceDep,
    condition: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> StrategyQualityDetectorsResponse:
    return service.detectors(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
        condition=condition,
        timeframe=timeframe,
    )


@router.get(
    "/summary",
    response_model=StrategyQualitySummaryResponse,
    summary="Detector counts by trust tier and verdict with a quality ranking",
    dependencies=[_STRATEGY_QUALITY_READ_LIMIT],
)
def strategy_quality_summary(
    tenant: ReaderDep,
    service: StrategyQualityServiceDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> StrategyQualitySummaryResponse:
    return service.summary(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
    )


@router.get(
    "/detectors/{condition}/explain",
    response_model=DetectorExplainResponse,
    summary="Detailed quality breakdown for one detector including timeframes",
    dependencies=[_STRATEGY_QUALITY_READ_LIMIT],
)
def strategy_quality_explain(
    tenant: ReaderDep,
    service: StrategyQualityServiceDep,
    condition: str,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    min_sample: int = _MinSample,
) -> DetectorExplainResponse:
    return service.explain(
        organization_id=tenant.organization_id,
        user_id=user_id,
        start_date=start_date,
        end_date=end_date,
        min_sample=min_sample,
        condition=condition,
    )
