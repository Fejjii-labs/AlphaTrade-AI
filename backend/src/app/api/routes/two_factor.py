"""Two-factor (TOTP) settings API for the signed-in user.

Login's second step lives in ``auth.py`` (``POST /auth/login/two-factor``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.auth import TenantDep
from app.core.config import Settings, get_settings
from app.core.dependencies import AuditServiceDep, SessionDep
from app.schemas.account import MessageResponse
from app.schemas.two_factor import (
    TwoFactorCodeRequest,
    TwoFactorRecoveryCodesResponse,
    TwoFactorSetupResponse,
    TwoFactorStatusResponse,
)
from app.security.rate_limit import tenant_rate_limit_dependency
from app.services.two_factor_service import TwoFactorService

router = APIRouter(prefix="/auth/two-factor", tags=["auth"])

_TWO_FACTOR_SETTINGS_LIMIT = Depends(
    tenant_rate_limit_dependency("auth:two-factor", limit=30, window_seconds=900)
)


def get_two_factor_service(
    session: SessionDep,
    audit_service: AuditServiceDep,
    settings: Settings = Depends(get_settings),
) -> TwoFactorService:
    return TwoFactorService(session, settings, audit_service=audit_service)


TwoFactorServiceDep = Annotated[TwoFactorService, Depends(get_two_factor_service)]


@router.get("", response_model=TwoFactorStatusResponse)
def two_factor_status(tenant: TenantDep, service: TwoFactorServiceDep) -> TwoFactorStatusResponse:
    return service.status(tenant.user_id)


@router.post(
    "/setup", response_model=TwoFactorSetupResponse, dependencies=[_TWO_FACTOR_SETTINGS_LIMIT]
)
def start_two_factor_setup(
    tenant: TenantDep, service: TwoFactorServiceDep, session: SessionDep
) -> TwoFactorSetupResponse:
    """New secret for the authenticator app. Shown once; not on until confirmed."""
    result = service.start_setup(user_id=tenant.user_id, organization_id=tenant.organization_id)
    session.commit()
    return result


@router.post(
    "/confirm",
    response_model=TwoFactorRecoveryCodesResponse,
    dependencies=[_TWO_FACTOR_SETTINGS_LIMIT],
)
def confirm_two_factor_setup(
    body: TwoFactorCodeRequest,
    tenant: TenantDep,
    service: TwoFactorServiceDep,
    session: SessionDep,
) -> TwoFactorRecoveryCodesResponse:
    """Turn 2FA on with a valid code; returns 10 recovery codes (shown once)."""
    result = service.confirm_setup(
        user_id=tenant.user_id, organization_id=tenant.organization_id, code=body.code
    )
    session.commit()
    return result


@router.post("/disable", response_model=MessageResponse, dependencies=[_TWO_FACTOR_SETTINGS_LIMIT])
def disable_two_factor(
    body: TwoFactorCodeRequest,
    tenant: TenantDep,
    service: TwoFactorServiceDep,
    session: SessionDep,
) -> MessageResponse:
    """Needs a current authenticator code. Deletes recovery codes, revokes remembered devices."""
    service.disable(user_id=tenant.user_id, organization_id=tenant.organization_id, code=body.code)
    session.commit()
    return MessageResponse(message="Two-factor authentication is off.")


@router.post(
    "/recovery-codes",
    response_model=TwoFactorRecoveryCodesResponse,
    dependencies=[_TWO_FACTOR_SETTINGS_LIMIT],
)
def regenerate_recovery_codes(
    body: TwoFactorCodeRequest,
    tenant: TenantDep,
    service: TwoFactorServiceDep,
    session: SessionDep,
) -> TwoFactorRecoveryCodesResponse:
    """Needs a current authenticator code. Replaces every previous recovery code."""
    result = service.regenerate_recovery_codes(
        user_id=tenant.user_id, organization_id=tenant.organization_id, code=body.code
    )
    session.commit()
    return result


@router.post(
    "/devices/revoke", response_model=MessageResponse, dependencies=[_TWO_FACTOR_SETTINGS_LIMIT]
)
def revoke_remembered_devices(
    tenant: TenantDep, service: TwoFactorServiceDep, session: SessionDep
) -> MessageResponse:
    """Forget every remembered device: the next login on each asks for a code again."""
    revoked = service.revoke_devices(user_id=tenant.user_id, organization_id=tenant.organization_id)
    session.commit()
    return MessageResponse(message=f"Forgot {revoked} remembered device(s).")
