"""Market watcher API (Slice 41 — read-only, disabled by default)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    MarketWatcherBridgeServiceDep,
    MarketWatcherServiceDep,
    SessionDep,
    WatcherMonitoringServiceDep,
)
from app.schemas.market_watcher import (
    MarketWatcherBridgeStatus,
    MarketWatcherBridgeTickResult,
    MarketWatcherScanRequest,
    MarketWatcherScanResult,
    MarketWatcherStatus,
    MarketWatcherSummary,
    PaginatedMarketWatcherBridgeHistory,
    PaginatedMarketWatcherHistory,
    PaginatedMarketWatcherObservations,
    PaginatedMarketWatcherRecentScans,
)
from app.schemas.watcher_monitoring import WatcherMonitoringSnapshot
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import OwnerDep, ReaderDep

router = APIRouter(prefix="/market-watcher", tags=["market-watcher"])

_MW_READ_LIMIT = Depends(
    tenant_rate_limit_dependency("market-watcher:read", limit=120, window_seconds=3600)
)
_MW_WRITE_LIMIT = Depends(
    tenant_rate_limit_dependency("market-watcher:write", limit=30, window_seconds=3600)
)


@router.get(
    "/status",
    response_model=MarketWatcherStatus,
    summary="Market watcher status",
    dependencies=[_MW_READ_LIMIT],
)
async def market_watcher_status(
    tenant: ReaderDep,
    service: MarketWatcherServiceDep,
) -> MarketWatcherStatus:
    return service.get_status(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.get(
    "/monitoring",
    response_model=WatcherMonitoringSnapshot,
    summary="Watcher paper-monitoring snapshot (read-only)",
    dependencies=[_MW_READ_LIMIT],
)
def market_watcher_monitoring(
    tenant: ReaderDep,
    service: WatcherMonitoringServiceDep,
) -> WatcherMonitoringSnapshot:
    """Expose existing Watcher runtime evidence. Does not start Watcher."""
    return service.get_snapshot(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.get(
    "/summary",
    response_model=MarketWatcherSummary,
    summary="Market watcher scanner summary (Slice 72)",
    dependencies=[_MW_READ_LIMIT],
)
async def market_watcher_summary(
    tenant: ReaderDep,
    service: MarketWatcherServiceDep,
) -> MarketWatcherSummary:
    return service.get_summary(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.post(
    "/scan",
    response_model=MarketWatcherScanResult,
    summary="Manual read-only market watcher scan",
    dependencies=[_MW_WRITE_LIMIT],
)
async def market_watcher_scan(
    tenant: OwnerDep,
    body: MarketWatcherScanRequest,
    service: MarketWatcherServiceDep,
    session: SessionDep,
) -> MarketWatcherScanResult:
    result = service.scan(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        request=body,
    )
    session.commit()
    return result


@router.get(
    "/history",
    response_model=PaginatedMarketWatcherHistory,
    summary="Recent market watcher scan history",
    dependencies=[_MW_READ_LIMIT],
)
async def market_watcher_history(
    tenant: ReaderDep,
    service: MarketWatcherServiceDep,
) -> PaginatedMarketWatcherHistory:
    return service.list_history(tenant.organization_id)


@router.get(
    "/scans/recent",
    response_model=PaginatedMarketWatcherRecentScans,
    summary="Recent persisted market watcher scan summaries (Slice 75)",
    dependencies=[_MW_READ_LIMIT],
)
async def market_watcher_recent_scans(
    tenant: ReaderDep,
    service: MarketWatcherServiceDep,
    limit: int = Query(default=10, ge=1, le=25),
) -> PaginatedMarketWatcherRecentScans:
    return service.list_recent_scans(tenant.organization_id, limit=limit)


@router.get(
    "/observations",
    response_model=PaginatedMarketWatcherObservations,
    summary="Market watcher observations",
    dependencies=[_MW_READ_LIMIT],
)
async def market_watcher_observations(
    tenant: ReaderDep,
    service: MarketWatcherServiceDep,
    symbol: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedMarketWatcherObservations:
    return service.list_observations(
        tenant.organization_id,
        symbol=symbol,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/bridge/status",
    response_model=MarketWatcherBridgeStatus,
    summary="Market watcher bridge status",
    dependencies=[_MW_READ_LIMIT],
)
async def market_watcher_bridge_status(
    tenant: ReaderDep,
    bridge: MarketWatcherBridgeServiceDep,
) -> MarketWatcherBridgeStatus:
    return bridge.get_status(organization_id=tenant.organization_id)


@router.post(
    "/bridge/tick",
    response_model=MarketWatcherBridgeTickResult,
    summary="Manual market watcher bridge tick (paper scan only)",
    dependencies=[_MW_WRITE_LIMIT],
)
async def market_watcher_bridge_tick(
    tenant: OwnerDep,
    bridge: MarketWatcherBridgeServiceDep,
    session: SessionDep,
) -> MarketWatcherBridgeTickResult:
    result = bridge.tick(organization_id=tenant.organization_id, user_id=tenant.user_id)
    session.commit()
    return result


@router.get(
    "/bridge/history",
    response_model=PaginatedMarketWatcherBridgeHistory,
    summary="Market watcher bridge decision history",
    dependencies=[_MW_READ_LIMIT],
)
async def market_watcher_bridge_history(
    tenant: ReaderDep,
    bridge: MarketWatcherBridgeServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedMarketWatcherBridgeHistory:
    return bridge.list_history(tenant.organization_id, limit=limit, offset=offset)
