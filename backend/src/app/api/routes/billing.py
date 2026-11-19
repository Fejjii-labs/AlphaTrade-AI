"""Billing API (Slice 26)."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request

from app.core.dependencies import SessionDep, SettingsDep
from app.schemas.billing import (
    BillingCustomerCreate,
    BillingCustomerView,
    BillingStatusResponse,
    CheckoutRequest,
    CheckoutResponse,
    PortalResponse,
    SubscriptionPlanView,
    UsageExportRequest,
    UsageExportResponse,
)
from app.security.rbac import OwnerDep, ReaderDep
from app.services.billing_service import BillingService

router = APIRouter(prefix="/billing", tags=["billing"])


def _billing_service(session: SessionDep, settings: SettingsDep) -> BillingService:
    return BillingService(session, settings)


@router.get("/plans", response_model=list[SubscriptionPlanView], summary="List subscription plans")
def list_plans(
    tenant: ReaderDep,
    session: SessionDep,
    settings: SettingsDep,
) -> list[SubscriptionPlanView]:
    _ = tenant
    return BillingService(session, settings).list_plans()


@router.get("/status", response_model=BillingStatusResponse, summary="Billing status")
def billing_status(
    tenant: ReaderDep,
    session: SessionDep,
    settings: SettingsDep,
) -> BillingStatusResponse:
    return BillingService(session, settings).get_status(tenant.organization_id)


@router.post(
    "/customer",
    response_model=BillingCustomerView,
    summary="Create billing customer (OWNER)",
)
def create_billing_customer(
    body: BillingCustomerCreate,
    tenant: OwnerDep,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> BillingCustomerView:
    service = BillingService(session, settings)
    return service.create_customer(
        tenant.organization_id,
        body,
        actor_user_id=tenant.user_id,
        billing_email_fallback=tenant.email,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/checkout", response_model=CheckoutResponse, summary="Start checkout (OWNER)")
def billing_checkout(
    body: CheckoutRequest,
    tenant: OwnerDep,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> CheckoutResponse:
    return BillingService(session, settings).create_checkout(
        tenant.organization_id,
        body,
        actor_user_id=tenant.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/portal", response_model=PortalResponse, summary="Customer portal (OWNER)")
def billing_portal(
    tenant: OwnerDep,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> PortalResponse:
    return BillingService(session, settings).create_portal(
        tenant.organization_id,
        actor_user_id=tenant.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/usage/export", response_model=UsageExportResponse, summary="Export usage (OWNER)")
def billing_usage_export(
    body: UsageExportRequest,
    tenant: OwnerDep,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> UsageExportResponse:
    return BillingService(session, settings).export_usage(
        tenant.organization_id,
        body,
        actor_user_id=tenant.user_id,
        request_id=getattr(request.state, "request_id", None),
    )


@router.post("/webhook", summary="Billing provider webhook")
async def billing_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict[str, str]:
    payload = await request.body()
    service = BillingService(session, settings)
    return service.handle_webhook(
        payload,
        stripe_signature,
        request_id=getattr(request.state, "request_id", None),
    )
