"""Trading analytics API (Slice 31)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from app.core.dependencies import AnalyticsFacadeDep
from app.schemas.analytics import (
    DisciplineScoreResult,
    RiskBehaviorAnalytics,
    SetupAnalyticsResponse,
    TradeReviewAnalytics,
)
from app.schemas.common import SetupType
from app.security.rbac import ReaderDep

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get(
    "/setups", response_model=SetupAnalyticsResponse, summary="Setup performance statistics"
)
def get_setup_analytics(
    tenant: ReaderDep,
    analytics: AnalyticsFacadeDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    setup_type: SetupType | None = Query(default=None),
) -> SetupAnalyticsResponse:
    return analytics.setup_analytics(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        start_date=start_date,
        end_date=end_date,
        setup_type=setup_type,
    )


@router.get("/trade-review", response_model=TradeReviewAnalytics, summary="Trade review analytics")
def get_trade_review_analytics(
    tenant: ReaderDep,
    analytics: AnalyticsFacadeDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> TradeReviewAnalytics:
    return analytics.trade_review(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/discipline", response_model=DisciplineScoreResult, summary="Discipline score")
def get_discipline_score(
    tenant: ReaderDep,
    analytics: AnalyticsFacadeDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> DisciplineScoreResult:
    return analytics.discipline(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        start_date=start_date,
        end_date=end_date,
    )


@router.get(
    "/risk-behavior", response_model=RiskBehaviorAnalytics, summary="Risk behavior dashboard"
)
def get_risk_behavior_analytics(
    tenant: ReaderDep,
    analytics: AnalyticsFacadeDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> RiskBehaviorAnalytics:
    return analytics.risk_behavior(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        start_date=start_date,
        end_date=end_date,
    )
