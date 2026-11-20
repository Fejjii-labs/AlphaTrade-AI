"""Human versus system comparison API (Slice 33-36)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.core.dependencies import HumanVsSystemServiceDep
from app.schemas.human_vs_system import HumanVsSystemComparison
from app.security.rbac import TenantDep

router = APIRouter(prefix="/human-vs-system", tags=["human-vs-system"])


@router.get("/{trade_id}", response_model=HumanVsSystemComparison, summary="Compare trade to plan")
def compare_human_vs_system(
    trade_id: uuid.UUID,
    tenant: TenantDep,
    service: HumanVsSystemServiceDep,
) -> HumanVsSystemComparison:
    return service.compare(
        trade_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.post(
    "/{trade_id}/analyze",
    response_model=HumanVsSystemComparison,
    summary="Run full discipline analysis for trade",
)
def analyze_human_vs_system(
    trade_id: uuid.UUID,
    tenant: TenantDep,
    service: HumanVsSystemServiceDep,
) -> HumanVsSystemComparison:
    return service.compare(
        trade_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
