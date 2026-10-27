"""Notification preferences API (Slice 46)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import (
    AlertDeliveryServiceDep,
    NotificationPreferencesServiceDep,
    SessionDep,
)
from app.schemas.notifications import (
    NotificationPreferencesResponse,
    NotificationPreferencesUpdate,
    NotificationTestResult,
)
from app.schemas.telegram_policy import TelegramPolicyOptions
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import ReaderDep, TraderDep
from app.services.notifications.telegram_policy_options import telegram_policy_options

router = APIRouter(prefix="/notifications", tags=["notifications"])

_PREFS_READ = Depends(
    tenant_rate_limit_dependency(
        "notifications:read", limit=120, window_seconds=3600, user_limit=120
    )
)
_PREFS_WRITE = Depends(
    tenant_rate_limit_dependency(
        "notifications:write", limit=30, window_seconds=3600, user_limit=30
    )
)
_PREFS_TEST = Depends(
    tenant_rate_limit_dependency("notifications:test", limit=10, window_seconds=3600, user_limit=10)
)


@router.get(
    "/preferences",
    response_model=NotificationPreferencesResponse,
    summary="Get notification preferences",
    dependencies=[_PREFS_READ],
)
async def get_notification_preferences(
    tenant: ReaderDep,
    service: NotificationPreferencesServiceDep,
) -> NotificationPreferencesResponse:
    return service.get(organization_id=tenant.organization_id, user_id=tenant.user_id)


@router.get(
    "/telegram/options",
    response_model=TelegramPolicyOptions,
    summary="Named choices for Telegram policy filters",
    dependencies=[_PREFS_READ],
)
async def get_telegram_policy_options(
    tenant: ReaderDep, session: SessionDep
) -> TelegramPolicyOptions:
    return telegram_policy_options(
        session, organization_id=tenant.organization_id, user_id=tenant.user_id
    )


@router.patch(
    "/preferences",
    response_model=NotificationPreferencesResponse,
    summary="Update notification preferences",
    dependencies=[_PREFS_WRITE],
)
async def update_notification_preferences(
    payload: NotificationPreferencesUpdate,
    tenant: TraderDep,
    service: NotificationPreferencesServiceDep,
    session: SessionDep,
) -> NotificationPreferencesResponse:
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
    "/preferences/reset-defaults",
    response_model=NotificationPreferencesResponse,
    summary="Reset notification preferences to defaults",
    dependencies=[_PREFS_WRITE],
)
async def reset_notification_preferences(
    tenant: TraderDep,
    service: NotificationPreferencesServiceDep,
    session: SessionDep,
) -> NotificationPreferencesResponse:
    result = service.reset_defaults(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/test",
    response_model=NotificationTestResult,
    summary="Send safe test notification (no trade execution)",
    dependencies=[_PREFS_TEST],
)
async def send_test_notification(
    tenant: TraderDep,
    delivery: AlertDeliveryServiceDep,
    session: SessionDep,
) -> NotificationTestResult:
    result = delivery.send_test_notification(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result
