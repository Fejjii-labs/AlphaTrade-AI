"""Performance analytics API (Slice 62, portfolio Slice 91A)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from app.core.dependencies import PaperPortfolioServiceDep, PerformanceServiceDep, SessionDep
from app.schemas.performance import (
    PaperPortfolioResponse,
    PerformanceReport,
    PerformanceSnapshotListResponse,
    PerformanceSnapshotResponse,
)
from app.security.rbac import OwnerDep, ReaderDep
from app.services.performance.unified_trade import PortfolioSourceFilter

router = APIRouter(prefix="/performance", tags=["performance"])


@router.get("/report", response_model=PerformanceReport, summary="Account performance report")
def get_performance_report(
    tenant: ReaderDep,
    performance: PerformanceServiceDep,
) -> PerformanceReport:
    return performance.build_report(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.get(
    "/portfolio",
    response_model=PaperPortfolioResponse,
    summary="Unified paper portfolio performance",
)
def get_paper_portfolio(
    tenant: ReaderDep,
    portfolio: PaperPortfolioServiceDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    source: PortfolioSourceFilter = Query(default=PortfolioSourceFilter.ALL),
    symbol: str | None = Query(default=None),
    setup: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    timezone: str | None = Query(default=None),
) -> PaperPortfolioResponse:
    return portfolio.build_portfolio(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        start_date=start_date,
        end_date=end_date,
        source=source,
        symbol=symbol,
        setup=setup,
        timeframe=timeframe,
        timezone=timezone,
    )


@router.get(
    "/snapshots",
    response_model=PerformanceSnapshotListResponse,
    summary="List manual account performance snapshots",
)
def list_performance_snapshots(
    tenant: ReaderDep,
    portfolio: PaperPortfolioServiceDep,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> PerformanceSnapshotListResponse:
    return portfolio.list_snapshots(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


@router.post(
    "/snapshot",
    response_model=PerformanceSnapshotResponse,
    summary="Persist an account performance snapshot",
)
def create_performance_snapshot(
    tenant: OwnerDep,
    performance: PerformanceServiceDep,
    session: SessionDep,
) -> PerformanceSnapshotResponse:
    snapshot = performance.snapshot_account(organization_id=tenant.organization_id)
    session.commit()
    return PerformanceSnapshotResponse.model_validate(snapshot)
