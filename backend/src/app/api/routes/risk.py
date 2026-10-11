"""Risk engine API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import (
    KillSwitchServiceDep,
    LossAcceptanceServiceDep,
    PositionSizingServiceDep,
    RiskServiceDep,
    RiskSettingsServiceDep,
    SessionDep,
)
from app.core.errors import ConflictError, ValidationAppError
from app.schemas.position_sizing import (
    LossAcceptanceRequest,
    LossAcceptanceResult,
    PositionSizingRequest,
    PositionSizingResult,
)
from app.schemas.risk import (
    KillSwitchMutationRequest,
    KillSwitchStatus,
    RiskCheckRequest,
    RiskCheckResult,
    UserRiskSettingsResponse,
    UserRiskSettingsUpdate,
)
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import OwnerDep, ReaderDep, TraderDep

router = APIRouter(prefix="/risk", tags=["risk"])

_RISK_SETTINGS_READ = Depends(
    tenant_rate_limit_dependency(
        "risk:settings:read", limit=120, window_seconds=3600, user_limit=120
    )
)
_RISK_SETTINGS_WRITE = Depends(
    tenant_rate_limit_dependency(
        "risk:settings:write", limit=30, window_seconds=3600, user_limit=30
    )
)
_KILL_SWITCH_READ = Depends(
    tenant_rate_limit_dependency(
        "risk:kill-switch:read", limit=120, window_seconds=3600, user_limit=120
    )
)
_KILL_SWITCH_WRITE = Depends(
    tenant_rate_limit_dependency(
        "risk:kill-switch:write", limit=20, window_seconds=3600, user_limit=20
    )
)


@router.get(
    "/settings",
    response_model=UserRiskSettingsResponse,
    summary="Get user risk settings",
    dependencies=[_RISK_SETTINGS_READ],
)
async def get_risk_settings(
    tenant: ReaderDep,
    service: RiskSettingsServiceDep,
) -> UserRiskSettingsResponse:
    return service.get(organization_id=tenant.organization_id, user_id=tenant.user_id)


@router.patch(
    "/settings",
    response_model=UserRiskSettingsResponse,
    summary="Update user risk settings",
    dependencies=[_RISK_SETTINGS_WRITE],
)
async def update_risk_settings(
    payload: UserRiskSettingsUpdate,
    tenant: TraderDep,
    service: RiskSettingsServiceDep,
    session: SessionDep,
) -> UserRiskSettingsResponse:
    try:
        result = service.update(
            payload,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
        )
        session.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/settings/reset-defaults",
    response_model=UserRiskSettingsResponse,
    summary="Reset user risk settings to defaults",
    dependencies=[_RISK_SETTINGS_WRITE],
)
async def reset_risk_settings(
    tenant: TraderDep,
    service: RiskSettingsServiceDep,
    session: SessionDep,
) -> UserRiskSettingsResponse:
    result = service.reset_defaults(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/kill-switch",
    response_model=KillSwitchStatus,
    summary="Get organization kill switch status",
    dependencies=[_KILL_SWITCH_READ],
)
def get_kill_switch(
    tenant: ReaderDep,
    service: KillSwitchServiceDep,
    session: SessionDep,
) -> KillSwitchStatus:
    """Read-only; available while execution is blocked."""
    # Run sync database work off the event loop and own the outer transaction:
    # the service's lazy default insert releases only its initialization savepoint.
    try:
        result = service.get_status(organization_id=tenant.organization_id)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


@router.post(
    "/kill-switch/activate",
    response_model=KillSwitchStatus,
    summary="Activate organization kill switch",
    dependencies=[_KILL_SWITCH_WRITE],
)
async def activate_kill_switch(
    payload: KillSwitchMutationRequest,
    tenant: OwnerDep,
    service: KillSwitchServiceDep,
    session: SessionDep,
) -> KillSwitchStatus:
    try:
        result = service.activate(
            organization_id=tenant.organization_id,
            actor_user_id=tenant.user_id,
            payload=payload,
        )
        session.commit()
        return result
    except ValidationAppError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/kill-switch/deactivate",
    response_model=KillSwitchStatus,
    summary="Deactivate organization kill switch",
    dependencies=[_KILL_SWITCH_WRITE],
)
async def deactivate_kill_switch(
    payload: KillSwitchMutationRequest,
    tenant: OwnerDep,
    service: KillSwitchServiceDep,
    session: SessionDep,
) -> KillSwitchStatus:
    try:
        result = service.deactivate(
            organization_id=tenant.organization_id,
            actor_user_id=tenant.user_id,
            payload=payload,
        )
        session.commit()
        return result
    except ValidationAppError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/check", response_model=RiskCheckResult, summary="Run deterministic risk check")
async def check_risk(
    request: RiskCheckRequest,
    risk_service: RiskServiceDep,
    _tenant: TraderDep,
) -> RiskCheckResult:
    return risk_service.check(request)


@router.post("/size", response_model=PositionSizingResult, summary="Calculate position size v2")
async def calculate_position_size(
    request: PositionSizingRequest,
    sizing_service: PositionSizingServiceDep,
    _tenant: TraderDep,
) -> PositionSizingResult:
    return sizing_service.calculate(request)


@router.post(
    "/loss-acceptance",
    response_model=LossAcceptanceResult,
    summary="Evaluate loss acceptance gate",
)
async def evaluate_loss_acceptance(
    request: LossAcceptanceRequest,
    loss_service: LossAcceptanceServiceDep,
    _tenant: TraderDep,
) -> LossAcceptanceResult:
    return loss_service.evaluate(
        planned_loss_amount=request.planned_loss_amount,
        request=request,
    )
