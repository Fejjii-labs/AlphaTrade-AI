"""Usage and cost tracking API."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.dependencies import QuotaServiceDep, SessionDep, UsageServiceDep
from app.schemas.usage import (
    OrganizationQuotaUpdate,
    PaginatedUsageEvents,
    QuotaStatus,
    UsageFeatureBreakdown,
    UsageProviderBreakdown,
    UsageSummary,
)
from app.security.rbac import OwnerDep, ReaderDep

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/events", response_model=PaginatedUsageEvents, summary="List usage events")
def list_usage_events(
    tenant: ReaderDep,
    usage_service: UsageServiceDep,
    request_id: str | None = Query(default=None),
    feature: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedUsageEvents:
    items, total = usage_service.list_events(
        organization_id=tenant.organization_id,
        request_id=request_id,
        feature=feature,
        limit=limit,
        offset=offset,
    )
    return PaginatedUsageEvents(items=items, total=total, limit=limit, offset=offset)


@router.get("/summary", response_model=UsageSummary, summary="Usage cost summary")
def usage_summary(
    tenant: ReaderDep,
    usage_service: UsageServiceDep,
) -> UsageSummary:
    return usage_service.summarize(organization_id=tenant.organization_id)


@router.get(
    "/by-feature",
    response_model=list[UsageFeatureBreakdown],
    summary="Usage breakdown by feature",
)
def usage_by_feature(
    tenant: ReaderDep,
    usage_service: UsageServiceDep,
) -> list[UsageFeatureBreakdown]:
    return usage_service.summarize_by_feature(organization_id=tenant.organization_id)


@router.get(
    "/by-provider",
    response_model=list[UsageProviderBreakdown],
    summary="Usage breakdown by provider",
)
def usage_by_provider(
    tenant: ReaderDep,
    usage_service: UsageServiceDep,
) -> list[UsageProviderBreakdown]:
    return usage_service.summarize_by_provider(organization_id=tenant.organization_id)


@router.get("/quota", response_model=QuotaStatus, summary="Organization quota status")
def usage_quota(
    tenant: ReaderDep,
    quota_service: QuotaServiceDep,
) -> QuotaStatus:
    return quota_service.get_status(tenant.organization_id)


@router.patch("/quota", response_model=QuotaStatus, summary="Update organization quotas")
def update_usage_quota(
    body: OrganizationQuotaUpdate,
    tenant: OwnerDep,
    quota_service: QuotaServiceDep,
    session: SessionDep,
) -> QuotaStatus:
    quota_service.update_quota(
        tenant.organization_id,
        body,
        actor_user_id=tenant.user_id,
    )
    session.commit()
    return quota_service.get_status(tenant.organization_id)
