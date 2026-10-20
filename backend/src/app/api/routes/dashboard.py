"""Dashboard summary API (Slice 44)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.attention.contracts import AttentionQueue
from app.attention.reader import AttentionQueueService
from app.core.config import Settings
from app.core.dependencies import (
    BloFinSyncServiceDep,
    DashboardSummaryServiceDep,
    SessionDep,
    SettingsDep,
)
from app.core.errors import ExchangeDemoInactiveError, NotFoundError
from app.daily_review.contracts import DailyReview
from app.daily_review.reader import DailyReviewService
from app.daily_review.service import daily_window
from app.schemas.common import MembershipRole
from app.schemas.dashboard import DashboardSummary
from app.schemas.dashboard_demo_account import DashboardDemoAccount
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import OwnerDep, ReaderDep
from app.services.dashboard.demo_account import demo_account_active, preserved_demo_account
from app.services.dashboard.demo_activity_performance import present_demo_account
from app.services.dashboard.demo_performance import attach_demo_performance

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_DASHBOARD_READ_LIMIT = Depends(
    tenant_rate_limit_dependency("dashboard:read", limit=120, window_seconds=3600, user_limit=120)
)


def _demo_account_scope(settings: Settings, organization_id: UUID) -> bool:
    configured = settings.governed_blofin_demo_organization_id
    if not configured:
        return True  # Legacy read-only sync has its existing organization-scoped snapshots.
    try:
        return UUID(configured) == organization_id
    except ValueError:
        return False


@router.get(
    "/demo-account",
    response_model=DashboardDemoAccount,
    summary="Latest saved BloFin demo account snapshot",
    dependencies=[_DASHBOARD_READ_LIMIT],
)
def demo_account(
    tenant: ReaderDep,
    service: BloFinSyncServiceDep,
    settings: SettingsDep,
    response: Response,
    session: SessionDep,
) -> DashboardDemoAccount:
    response.headers["Cache-Control"] = "private, no-store"
    if not demo_account_active(settings) or not _demo_account_scope(
        settings, tenant.organization_id
    ):
        return present_demo_account(
            DashboardDemoAccount(
                status="inactive",
                message="BloFin demo sync is not configured. Configure it in Exchange settings.",
            ),
            session=session,
            settings=settings,
            tenant=tenant,
        )
    can_refresh = tenant.membership_role is MembershipRole.OWNER
    try:
        snapshot = service.latest(organization_id=tenant.organization_id)
    except NotFoundError:
        return present_demo_account(
            DashboardDemoAccount(
                status="not_synced",
                can_refresh=can_refresh,
                message="No demo snapshot yet. Refresh to retrieve balances and open positions.",
            ),
            session=session,
            settings=settings,
            tenant=tenant,
        )
    result = preserved_demo_account(
        snapshot,
        service=service,
        organization_id=tenant.organization_id,
        settings=settings,
        can_refresh=can_refresh,
    )
    return present_demo_account(
        attach_demo_performance(result, session=session, settings=settings, tenant=tenant),
        session=session,
        settings=settings,
        tenant=tenant,
    )


@router.post(
    "/demo-account/refresh",
    response_model=DashboardDemoAccount,
    summary="Fetch and save BloFin demo balances and positions",
    dependencies=[
        Depends(
            tenant_rate_limit_dependency(
                "dashboard:demo-refresh", limit=240, window_seconds=3600, user_limit=240
            )
        )
    ],
)
def refresh_demo_account(
    tenant: OwnerDep,
    service: BloFinSyncServiceDep,
    settings: SettingsDep,
    session: SessionDep,
    response: Response,
) -> DashboardDemoAccount:
    response.headers["Cache-Control"] = "private, no-store"
    if not _demo_account_scope(settings, tenant.organization_id):
        raise NotFoundError("BloFin demo account is not configured for this organization.")
    if not demo_account_active(settings):
        raise ExchangeDemoInactiveError("BloFin demo account sync is not configured.")
    result = service.sync(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        include_instrument_metadata=True,
    )
    session.commit()
    account = preserved_demo_account(
        result.snapshot,
        service=service,
        organization_id=tenant.organization_id,
        settings=settings,
        can_refresh=True,
    )
    return present_demo_account(
        attach_demo_performance(account, session=session, settings=settings, tenant=tenant),
        session=session,
        settings=settings,
        tenant=tenant,
    )


@router.get(
    "/attention",
    response_model=AttentionQueue,
    summary="Read-only paper attention queue for the current tenant and user",
    dependencies=[_DASHBOARD_READ_LIMIT],
)
def attention_queue(tenant: ReaderDep, session: SessionDep, response: Response) -> AttentionQueue:
    response.headers["Cache-Control"] = "private, no-store"
    return AttentionQueueService(session).queue(
        organization_id=tenant.organization_id, user_id=tenant.user_id, now=datetime.now(UTC)
    )


@router.get(
    "/summary",
    response_model=DashboardSummary,
    summary="Paper-only dashboard summary",
    dependencies=[_DASHBOARD_READ_LIMIT],
)
async def dashboard_summary(
    tenant: ReaderDep,
    service: DashboardSummaryServiceDep,
) -> DashboardSummary:
    return service.summarize(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.get(
    "/daily-review",
    response_model=DailyReview,
    summary="Recorded Daily Review for the current tenant and user",
    dependencies=[_DASHBOARD_READ_LIMIT],
)
def daily_review(
    tenant: ReaderDep,
    session: SessionDep,
    response: Response,
    day: date | None = Query(default=None, alias="date"),
    timezone: str = Query(default="UTC", min_length=1, max_length=100),
) -> DailyReview:
    try:
        zone = ZoneInfo(timezone)
        generated_at = datetime.now(UTC)
        window = daily_window(day or generated_at.astimezone(zone).date(), timezone)
    except (ZoneInfoNotFoundError, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail="Invalid review date or timezone.") from exc
    response.headers["Cache-Control"] = "private, no-store"
    return DailyReviewService(session).review(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        window=window,
        generated_at=generated_at,
    )
