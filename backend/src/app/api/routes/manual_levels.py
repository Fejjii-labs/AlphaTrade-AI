"""Manual chart levels API (Slice 33)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response

from app.core.dependencies import ManualLevelServiceDep, SessionDep
from app.schemas.manual_levels import (
    ManualChartLevel,
    ManualChartLevelCreate,
    ManualChartLevelUpdate,
    ManualLevelCreate,
    PaginatedManualChartLevels,
)
from app.security.rbac import TraderDep

router = APIRouter(prefix="/manual-levels", tags=["manual-levels"])


@router.post("", response_model=ManualChartLevel, summary="Create manual chart level")
def create_manual_level(
    body: ManualLevelCreate,
    tenant: TraderDep,
    service: ManualLevelServiceDep,
    session: SessionDep,
) -> ManualChartLevel:
    payload = ManualChartLevelCreate(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        symbol=body.symbol,
        exchange=body.exchange,
        timeframe=body.timeframe,
        level_type=body.level_type,
        price=body.price,
        price_low=body.price_low,
        price_high=body.price_high,
        label=body.label,
        notes=body.notes,
        enabled=body.enabled,
    )
    result = service.create(payload)
    session.commit()
    return result


@router.get("", response_model=PaginatedManualChartLevels, summary="List manual chart levels")
def list_manual_levels(
    tenant: TraderDep,
    service: ManualLevelServiceDep,
    symbol: str | None = Query(default=None),
    exchange: str | None = Query(default=None),
    enabled_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedManualChartLevels:
    items, total = service.list_levels(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        symbol=symbol,
        exchange=exchange,
        enabled_only=enabled_only,
        limit=limit,
        offset=offset,
    )
    return PaginatedManualChartLevels(items=items, total=total, limit=limit, offset=offset)


@router.get("/{level_id}", response_model=ManualChartLevel, summary="Get manual chart level")
def get_manual_level(
    level_id: uuid.UUID,
    tenant: TraderDep,
    service: ManualLevelServiceDep,
) -> ManualChartLevel:
    return service.get(level_id, organization_id=tenant.organization_id, user_id=tenant.user_id)


@router.patch("/{level_id}", response_model=ManualChartLevel, summary="Update manual chart level")
def update_manual_level(
    level_id: uuid.UUID,
    body: ManualChartLevelUpdate,
    tenant: TraderDep,
    service: ManualLevelServiceDep,
    session: SessionDep,
) -> ManualChartLevel:
    result = service.update(
        level_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.delete("/{level_id}", status_code=204, summary="Delete manual chart level")
def delete_manual_level(
    level_id: uuid.UUID,
    tenant: TraderDep,
    service: ManualLevelServiceDep,
    session: SessionDep,
) -> Response:
    service.delete(level_id, organization_id=tenant.organization_id, user_id=tenant.user_id)
    session.commit()
    return Response(status_code=204)
