"""Autonomous trading settings API (issue #284)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import AutonomousTradingSettingsServiceDep, SessionDep
from app.schemas.autonomous_activity import AutonomousActivityView
from app.schemas.autonomous_trading_settings import (
    AutonomousTradingSettingsUpdate,
    AutonomousTradingSettingsView,
)
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import OwnerDep, ReaderDep
from app.services.autonomous_activity import AutonomousActivityService
from app.services.autonomous_trading_settings_service import AutonomousSettingsValidationError

router = APIRouter(prefix="/autonomous", tags=["autonomous-trading"])

_READ = Depends(
    tenant_rate_limit_dependency(
        "autonomous:settings:read", limit=120, window_seconds=3600, user_limit=120
    )
)
# Own bucket so polling the activity card never throttles settings reads, and
# the reverse (Griller C2 on #368).
_ACTIVITY_READ = Depends(
    tenant_rate_limit_dependency(
        "autonomous:activity:read", limit=120, window_seconds=3600, user_limit=120
    )
)
_WRITE = Depends(
    tenant_rate_limit_dependency(
        "autonomous:settings:write", limit=30, window_seconds=3600, user_limit=30
    )
)


@router.get(
    "/settings",
    response_model=AutonomousTradingSettingsView,
    summary="Get autonomous trading settings",
    dependencies=[_READ],
)
async def get_autonomous_trading_settings(
    tenant: ReaderDep,
    service: AutonomousTradingSettingsServiceDep,
) -> AutonomousTradingSettingsView:
    return service.get(organization_id=tenant.organization_id, user_id=tenant.user_id)


@router.get(
    "/activity",
    response_model=AutonomousActivityView,
    summary="What the autonomous paper bot did today",
    dependencies=[_ACTIVITY_READ],
)
async def get_autonomous_activity(
    tenant: ReaderDep,
    session: SessionDep,
) -> AutonomousActivityView:
    return AutonomousActivityService(session).view(
        organization_id=tenant.organization_id, user_id=tenant.user_id
    )


@router.patch(
    "/settings",
    response_model=AutonomousTradingSettingsView,
    summary="Update autonomous trading settings",
    dependencies=[_WRITE],
)
async def update_autonomous_trading_settings(
    payload: AutonomousTradingSettingsUpdate,
    tenant: OwnerDep,
    service: AutonomousTradingSettingsServiceDep,
    session: SessionDep,
) -> AutonomousTradingSettingsView:
    try:
        result = service.update(
            payload,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
        )
        session.commit()
        return result
    except AutonomousSettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
