"""Market watcher API (Slice 41 — read-only, disabled by default)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.dependencies import (
    WatcherMonitoringServiceDep,
)
from app.schemas.watcher_monitoring import WatcherMonitoringSnapshot
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep

router = APIRouter(prefix="/market-watcher", tags=["market-watcher"])

_MW_READ_LIMIT = Depends(
    tenant_rate_limit_dependency("market-watcher:read", limit=120, window_seconds=3600)
)
_MW_WRITE_LIMIT = Depends(
    tenant_rate_limit_dependency("market-watcher:write", limit=30, window_seconds=3600)
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
