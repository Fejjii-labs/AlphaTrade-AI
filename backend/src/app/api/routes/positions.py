"""Position management API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.core.auth import TenantDep
from app.core.dependencies import PositionServiceDep, SessionDep
from app.core.errors import NotFoundError
from app.schemas.common import PositionStatus
from app.schemas.position import (
    ClosePaperPositionRequest,
    PaginatedPositions,
    Position,
    PositionUpdate,
)
from app.security.rbac import TraderDep
from app.security.tenant import TenantContext, ensure_same_organization

router = APIRouter(prefix="/positions", tags=["positions"])


def _ensure_own_position(position: Position, tenant: TenantContext) -> None:
    """Positions are per-user (the list is user-scoped); a colleague's is not found (M15)."""
    ensure_same_organization(
        position.organization_id, tenant, not_found_message="Position not found"
    )
    if position.user_id != tenant.user_id:
        raise NotFoundError("Position not found")


@router.get("", response_model=PaginatedPositions, summary="List positions")
async def list_positions(
    tenant: TenantDep,
    position_service: PositionServiceDep,
    status: PositionStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedPositions:
    items, total = position_service.list_positions(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return PaginatedPositions(items=items, total=total, limit=limit, offset=offset)


@router.get("/{position_id}", response_model=Position, summary="Get position")
async def get_position(
    position_id: uuid.UUID,
    tenant: TenantDep,
    position_service: PositionServiceDep,
) -> Position:
    position = position_service.get(position_id)
    _ensure_own_position(position, tenant)
    return position


@router.patch("/{position_id}", response_model=Position, summary="Update position")
async def update_position(
    position_id: uuid.UUID,
    body: PositionUpdate,
    tenant: TraderDep,
    position_service: PositionServiceDep,
    session: SessionDep,
) -> Position:
    position = position_service.get(position_id)
    _ensure_own_position(position, tenant)
    result = position_service.update(position_id, body)
    session.commit()
    return result


@router.post(
    "/{position_id}/close-paper",
    response_model=Position,
    summary="Close paper position",
)
async def close_paper_position(
    position_id: uuid.UUID,
    body: ClosePaperPositionRequest,
    tenant: TraderDep,
    position_service: PositionServiceDep,
    session: SessionDep,
) -> Position:
    position = position_service.get(position_id)
    _ensure_own_position(position, tenant)
    result = position_service.close_paper(position_id, body)
    session.commit()
    return result
