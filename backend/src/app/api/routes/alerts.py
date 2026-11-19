"""Alert delivery API extensions (Slice 41)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    AlertDeliveryServiceDep,
    PaperAlertServiceDep,
    SessionDep,
    SettingsDep,
)
from app.schemas.alert_delivery import (
    AlertDeliverPendingResult,
    AlertDeliverResult,
    AlertDeliveryStatusResponse,
    AlertDeliverySummary,
)
from app.schemas.alert_routing import AlertRoutingSummaryResponse
from app.schemas.alerts import (
    PaginatedPaperAlerts,
    PaginatedSetupAlertReview,
    PaperAlert,
    PaperAlertSummary,
    SetupAlertReviewItem,
    SetupAlertReviewSummary,
    SetupAlertReviewUpdate,
)
from app.schemas.common import PaperAlertSeverity, PaperAlertType, SetupAlertReviewStatus
from app.schemas.telegram_alert_delivery import (
    TelegramAlertDeliveryRequest,
    TelegramAlertDeliveryResponse,
)
from app.schemas.telegram_automatic_delivery import (
    AlertDeliveryPreviewRequest,
    AlertDeliveryPreviewResponse,
)
from app.schemas.telegram_test_alert import TelegramTestAlertRequest, TelegramTestAlertResponse
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import OwnerDep, ReaderDep, TraderDep
from app.services.alert_routing_diagnostics_service import build_alert_routing_summary
from app.services.telegram_alert_delivery_service import TelegramAlertDeliveryService
from app.services.telegram_automatic_delivery_service import TelegramAutomaticDeliveryService
from app.services.telegram_test_alert_service import TelegramTestAlertService

router = APIRouter(prefix="/alerts", tags=["alerts"])

_ALERTS_READ_LIMIT = Depends(
    tenant_rate_limit_dependency("alerts:read", limit=120, window_seconds=3600, user_limit=120)
)
_ALERTS_WRITE_LIMIT = Depends(
    tenant_rate_limit_dependency("alerts:write", limit=60, window_seconds=3600, user_limit=60)
)
_ALERTS_DELIVER_LIMIT = Depends(
    tenant_rate_limit_dependency("alerts:deliver", limit=30, window_seconds=3600, user_limit=30)
)
_ALERTS_TELEGRAM_DELIVER_LIMIT = Depends(
    tenant_rate_limit_dependency(
        "alerts:telegram-deliver",
        limit=30,
        window_seconds=3600,
        user_limit=30,
    )
)
_ALERTS_TELEGRAM_TEST_LIMIT = Depends(
    tenant_rate_limit_dependency(
        "alerts:telegram-test",
        limit=10,
        window_seconds=3600,
        user_limit=10,
    )
)
_ALERTS_PREVIEW_LIMIT = Depends(
    tenant_rate_limit_dependency(
        "alerts:delivery-preview",
        limit=30,
        window_seconds=3600,
        user_limit=30,
    )
)


@router.get(
    "",
    response_model=PaginatedPaperAlerts,
    summary="List paper validation alerts",
    dependencies=[_ALERTS_READ_LIMIT],
)
async def list_alerts(
    tenant: ReaderDep,
    service: PaperAlertServiceDep,
    alert_type: PaperAlertType | None = None,
    severity: PaperAlertSeverity | None = None,
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedPaperAlerts:
    return service.list_alerts(
        tenant.organization_id,
        alert_type=alert_type,
        severity=severity,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/summary",
    response_model=PaperAlertSummary,
    summary="Alert summary counts",
    dependencies=[_ALERTS_READ_LIMIT],
)
async def alerts_summary(
    tenant: ReaderDep,
    service: PaperAlertServiceDep,
) -> PaperAlertSummary:
    return service.summary(tenant.organization_id)


@router.get(
    "/routing/summary",
    response_model=AlertRoutingSummaryResponse,
    summary="Alert routing and market watcher bridge diagnostics",
    dependencies=[_ALERTS_READ_LIMIT],
)
async def alert_routing_summary(
    tenant: OwnerDep,
    settings: SettingsDep,
    session: SessionDep,
) -> AlertRoutingSummaryResponse:
    """Read-only operator summary of alert routing and bridge readiness."""
    return build_alert_routing_summary(
        settings=settings,
        session=session,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.post(
    "/delivery/preview",
    response_model=AlertDeliveryPreviewResponse,
    summary="Preview eligible alerts for automatic Telegram delivery (read-only)",
    dependencies=[_ALERTS_PREVIEW_LIMIT],
)
async def preview_alert_delivery(
    body: AlertDeliveryPreviewRequest,
    tenant: OwnerDep,
    settings: SettingsDep,
    session: SessionDep,
) -> AlertDeliveryPreviewResponse:
    """Read-only preview — does not send Telegram or mutate alerts."""
    service = TelegramAutomaticDeliveryService(session, settings)
    return service.preview(
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.post(
    "/test-telegram",
    response_model=TelegramTestAlertResponse,
    summary="Send owner-gated Telegram test alert (paper only, no trades)",
    dependencies=[_ALERTS_TELEGRAM_TEST_LIMIT],
)
async def send_telegram_test_alert(
    body: TelegramTestAlertRequest,
    tenant: OwnerDep,
    settings: SettingsDep,
    session: SessionDep,
) -> TelegramTestAlertResponse:
    """One safe manual Telegram test — requires explicit confirmation; never executes trades."""
    service = TelegramTestAlertService(session, settings)
    result = service.send_manual_test(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        confirm=body.confirm,
        message=body.message,
    )
    session.commit()
    return result


@router.get(
    "/delivery-status",
    response_model=AlertDeliveryStatusResponse,
    summary="External alert delivery configuration status",
    dependencies=[_ALERTS_READ_LIMIT],
)
async def alert_delivery_status(
    tenant: ReaderDep,
    delivery: AlertDeliveryServiceDep,
) -> AlertDeliveryStatusResponse:
    return delivery.get_status(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.get(
    "/delivery-summary",
    response_model=AlertDeliverySummary,
    summary="Alert delivery status counts for tenant",
    dependencies=[_ALERTS_READ_LIMIT],
)
async def alert_delivery_summary(
    tenant: ReaderDep,
    delivery: AlertDeliveryServiceDep,
) -> AlertDeliverySummary:
    return delivery.delivery_summary(tenant.organization_id)


@router.post(
    "/deliver-pending",
    response_model=AlertDeliverPendingResult,
    summary="Deliver pending alerts (owner/admin)",
    dependencies=[_ALERTS_DELIVER_LIMIT],
)
async def deliver_pending_alerts(
    tenant: OwnerDep,
    delivery: AlertDeliveryServiceDep,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> AlertDeliverPendingResult:
    result = delivery.deliver_pending(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        limit=limit,
    )
    session.commit()
    return result


@router.patch("/read-all", summary="Mark all alerts as read", dependencies=[_ALERTS_WRITE_LIMIT])
async def mark_all_alerts_read(
    tenant: TraderDep,
    service: PaperAlertServiceDep,
    session: SessionDep,
) -> dict[str, int]:
    count = service.mark_all_read(tenant.organization_id, user_id=tenant.user_id)
    session.commit()
    return {"marked_read": count}


@router.get(
    "/setup-review",
    response_model=PaginatedSetupAlertReview,
    summary="List scanner-created setup alerts for review",
    dependencies=[_ALERTS_READ_LIMIT],
)
async def list_setup_alert_review(
    tenant: ReaderDep,
    service: PaperAlertServiceDep,
    status: SetupAlertReviewStatus | None = Query(default=None, alias="status"),
    condition: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    direction: str | None = None,
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedSetupAlertReview:
    return service.list_setup_review(
        tenant.organization_id,
        review_status=status,
        condition=condition,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/setup-review/summary",
    response_model=SetupAlertReviewSummary,
    summary="Setup alert review summary counts",
    dependencies=[_ALERTS_READ_LIMIT],
)
async def setup_alert_review_summary(
    tenant: ReaderDep,
    service: PaperAlertServiceDep,
) -> SetupAlertReviewSummary:
    return service.setup_review_summary(tenant.organization_id)


@router.patch(
    "/setup-review/{alert_id}",
    response_model=SetupAlertReviewItem,
    summary="Update setup alert review state",
    dependencies=[_ALERTS_WRITE_LIMIT],
)
async def update_setup_alert_review(
    alert_id: uuid.UUID,
    body: SetupAlertReviewUpdate,
    tenant: TraderDep,
    service: PaperAlertServiceDep,
    session: SessionDep,
) -> SetupAlertReviewItem:
    result = service.update_setup_review(
        alert_id,
        body,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.get(
    "/{alert_id}",
    response_model=PaperAlert,
    summary="Get alert by id",
    dependencies=[_ALERTS_READ_LIMIT],
)
async def get_alert(
    alert_id: uuid.UUID,
    tenant: ReaderDep,
    service: PaperAlertServiceDep,
) -> PaperAlert:
    return service.get_alert(alert_id, organization_id=tenant.organization_id)


@router.patch(
    "/{alert_id}/read",
    response_model=PaperAlert,
    summary="Mark alert as read",
    dependencies=[_ALERTS_WRITE_LIMIT],
)
async def mark_alert_read(
    alert_id: uuid.UUID,
    tenant: TraderDep,
    service: PaperAlertServiceDep,
    session: SessionDep,
) -> PaperAlert:
    result = service.mark_read(
        alert_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post(
    "/{alert_id}/deliver-telegram",
    response_model=TelegramAlertDeliveryResponse,
    summary="Deliver one in-app alert to Telegram (owner, confirmation required)",
    dependencies=[_ALERTS_TELEGRAM_DELIVER_LIMIT],
)
async def deliver_alert_to_telegram(
    alert_id: uuid.UUID,
    body: TelegramAlertDeliveryRequest,
    tenant: OwnerDep,
    settings: SettingsDep,
    session: SessionDep,
) -> TelegramAlertDeliveryResponse:
    """Send one selected in-app alert to Telegram — no trades, no bulk delivery."""
    service = TelegramAlertDeliveryService(session, settings)
    result = service.deliver_alert(
        alert_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        confirm=body.confirm,
    )
    session.commit()
    return result


@router.post(
    "/{alert_id}/deliver",
    response_model=AlertDeliverResult,
    summary="Deliver single alert externally (owner/admin)",
    dependencies=[_ALERTS_DELIVER_LIMIT],
)
async def deliver_alert(
    alert_id: uuid.UUID,
    tenant: OwnerDep,
    delivery: AlertDeliveryServiceDep,
    session: SessionDep,
) -> AlertDeliverResult:
    result = delivery.deliver_alert(
        alert_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result
