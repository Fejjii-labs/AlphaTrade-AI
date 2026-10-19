"""Execution API — paper mode only."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError

from app.core.auth import TenantDep
from app.core.dependencies import (
    ExecutionServiceDep,
    ProposalServiceDep,
    SessionDep,
    SettingsDep,
    UsageServiceDep,
)
from app.core.errors import ValidationAppError
from app.db.models import ApprovalAuthorization as ApprovalAuthorizationRow
from app.schemas.execution import PaginatedPaperOrders, PaperOrder, PaperOrderRequest
from app.schemas.execution_account import (
    PaperAccountRegistration,
    PaperAccountStatus,
    RegisterPaperAccountRequest,
)
from app.schemas.execution_protocol import (
    ClosePaperPlanHttpRequest,
    ClosePaperPlanRequest,
    ClosePaperPlanResult,
    ExecutePaperPlanHttpRequest,
    ExecutePaperPlanRequest,
    ExecutePaperPlanResult,
    ExecutionCommandOutcome,
)
from app.schemas.manual_demo import (
    ManualDemoAttempt,
    ManualDemoCancelRequest,
    ManualDemoConfirmation,
    ManualDemoHistory,
    ManualDemoHistoryFilter,
    ManualDemoInstrument,
    ManualDemoPreview,
    ManualDemoPreviewRequest,
    ManualDemoStatus,
)
from app.schemas.trade_plan import EntrySide
from app.schemas.usage import UsageEventCreate
from app.security.quota_enforcement import require_quota
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import OwnerDep, TraderDep
from app.security.tenant import ensure_same_organization
from app.services.execution_account_service import ExecutionAccountService
from app.services.manual_demo_history import ManualDemoHistoryService
from app.services.manual_demo_service import ManualDemoService

router = APIRouter(prefix="/execution", tags=["execution"])

_EXECUTION_RATE_LIMIT = Depends(
    tenant_rate_limit_dependency(
        "execution:paper",
        limit=30,
        window_seconds=3600,
        ip_limit=60,
        user_limit=30,
    )
)
_PAPER_EXECUTION_QUOTA = require_quota("paper_execution")
_PAPER_PLAN_RATE_LIMIT = Depends(
    tenant_rate_limit_dependency(
        "execution:paper-plan",
        limit=30,
        window_seconds=3600,
        ip_limit=60,
        user_limit=30,
    )
)


@router.get("/accounts/paper", response_model=PaperAccountStatus)
def paper_account_status(tenant: TenantDep, session: SessionDep) -> PaperAccountStatus:
    return ExecutionAccountService(session).status(tenant)


@router.post("/accounts/paper", response_model=PaperAccountRegistration)
def register_paper_account(
    body: RegisterPaperAccountRequest,
    request: Request,
    tenant: OwnerDep,
    session: SessionDep,
) -> PaperAccountRegistration:
    """Register/reuse PAPER/NET identity only; does not authorize or dispatch."""
    try:
        result = ExecutionAccountService(session).register(
            tenant,
            request_id=getattr(request.state, "request_id", str(uuid.uuid4())),
            trace_id=getattr(request.state, "trace_id", str(uuid.uuid4())),
        )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


@router.post(
    "/paper",
    response_model=PaperOrder,
    summary="Place a paper order",
    dependencies=[_EXECUTION_RATE_LIMIT, _PAPER_EXECUTION_QUOTA],
)
async def place_paper_order(
    body: PaperOrderRequest,
    tenant: TraderDep,
    proposal_service: ProposalServiceDep,
    execution_service: ExecutionServiceDep,
    usage_service: UsageServiceDep,
    session: SessionDep,
) -> PaperOrder:
    proposal = proposal_service.get(
        body.proposal_id,
        organization_id=tenant.organization_id,
    )
    ensure_same_organization(proposal.organization_id, tenant)
    placement = execution_service.place_paper_order(body)

    # Replays and concurrent losers have no new unit-of-work to commit. The
    # convergence path already rolled back its dedicated request session.
    if not placement.created_new:
        return placement.order

    usage_service.record(
        UsageEventCreate(
            request_id=str(body.idempotency_key),
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            feature="paper_execution",
            provider="paper-engine",
            input_tokens=0,
            output_tokens=0,
            provider_metadata={"cost_source": "unavailable"},
        )
    )
    # One authoritative commit: business + audit (flushed in service) + usage.
    session.commit()
    return placement.order


@router.post(
    "/paper-plan",
    response_model=ExecutePaperPlanResult,
    summary="Execute an approved paper trade plan revision",
    dependencies=[_PAPER_PLAN_RATE_LIMIT, _PAPER_EXECUTION_QUOTA],
)
async def execute_paper_plan(
    body: ExecutePaperPlanHttpRequest,
    tenant: TraderDep,
    execution_service: ExecutionServiceDep,
    usage_service: UsageServiceDep,
    session: SessionDep,
) -> ExecutePaperPlanResult:
    from app.services.approval_service import assert_human_may_execute

    stored = session.get(ApprovalAuthorizationRow, body.authorization_id)
    if (
        stored is not None
        and stored.organization_id == tenant.organization_id
        and stored.user_id == tenant.user_id
    ):
        assert_human_may_execute(stored)
    result = execution_service.execute_paper_plan(
        ExecutePaperPlanRequest(
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            account_id=body.account_id,
            authorization_id=body.authorization_id,
            revision_id=body.revision_id,
            idempotency_key=body.idempotency_key,
            correlation_id=body.correlation_id,
        )
    )
    # Replay still commits so any healing journal/attribution writes in this
    # request survive process restart. Usage is metered only on first ALLOW.
    if not result.replayed and result.outcome is ExecutionCommandOutcome.ALLOW:
        usage_service.record(
            UsageEventCreate(
                request_id=body.idempotency_key,
                organization_id=tenant.organization_id,
                user_id=tenant.user_id,
                feature="paper_execution",
                provider="paper-engine",
                input_tokens=0,
                output_tokens=0,
                provider_metadata={"cost_source": "unavailable"},
            )
        )
    session.commit()
    return result


@router.post(
    "/paper-plan/close",
    response_model=ClosePaperPlanResult,
    summary="Close a filled canonical paper plan and record the outcome",
    dependencies=[_PAPER_PLAN_RATE_LIMIT],
)
async def close_paper_plan(
    body: ClosePaperPlanHttpRequest,
    tenant: TraderDep,
    execution_service: ExecutionServiceDep,
    session: SessionDep,
) -> ClosePaperPlanResult:
    """Explicit paper exit. Does not read market data or place an exchange order."""

    result = execution_service.close_canonical_paper_plan(
        ClosePaperPlanRequest(
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            account_id=body.account_id,
            revision_id=body.revision_id,
            command_id=body.command_id,
            exit_price=body.exit_price,
            fees=body.fees,
            funding=body.funding,
            slippage=body.slippage,
            exit_reason=body.exit_reason,
            occurred_at=body.occurred_at,
            idempotency_key=body.idempotency_key,
        )
    )
    session.commit()
    return result


@router.get("/orders", response_model=PaginatedPaperOrders, summary="List paper orders")
async def list_orders(
    tenant: TenantDep,
    execution_service: ExecutionServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedPaperOrders:
    items, total = execution_service.list_orders(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        limit=limit,
        offset=offset,
    )
    return PaginatedPaperOrders(items=items, total=total, limit=limit, offset=offset)


@router.get("/orders/{order_id}", response_model=PaperOrder, summary="Get paper order")
async def get_order(
    order_id: uuid.UUID,
    tenant: TenantDep,
    execution_service: ExecutionServiceDep,
) -> PaperOrder:
    order = execution_service.get_order(order_id)
    ensure_same_organization(order.organization_id, tenant)
    return order


@router.get("/manual-demo/instrument", dependencies=[_PAPER_PLAN_RATE_LIMIT])
def manual_demo_instrument(
    tenant: OwnerDep,
    session: SessionDep,
    settings: SettingsDep,
) -> ManualDemoInstrument:
    return ManualDemoService(session, settings).instrument(tenant)


@router.post("/manual-demo/preview", dependencies=[_PAPER_PLAN_RATE_LIMIT])
def preview_manual_demo(
    body: ManualDemoPreviewRequest,
    tenant: OwnerDep,
    session: SessionDep,
    settings: SettingsDep,
) -> ManualDemoPreview:
    return ManualDemoService(session, settings).preview(tenant, body)


@router.post("/manual-demo/confirm", dependencies=[_PAPER_PLAN_RATE_LIMIT, _PAPER_EXECUTION_QUOTA])
def confirm_manual_demo(
    body: ManualDemoConfirmation,
    tenant: OwnerDep,
    session: SessionDep,
    settings: SettingsDep,
) -> ManualDemoStatus:
    return ManualDemoService(session, settings).confirm(tenant, body)


@router.get("/manual-demo/commands")
def list_manual_demo_commands(
    tenant: OwnerDep,
    session: SessionDep,
    account_id: uuid.UUID | None = None,
    symbol: str | None = None,
    side: EntrySide | None = None,
    requested_quantity: Decimal | None = Query(default=None, gt=0),
    since: datetime | None = None,
    until: datetime | None = None,
    submission_status: Literal[
        "attempt", "blocked", "submitted", "uncertain", "filled"
    ] = "attempt",
    limit: int = Query(default=10, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=10000),
) -> ManualDemoHistory:
    try:
        filters = ManualDemoHistoryFilter(
            account_id=account_id,
            symbol=symbol,
            side=side,
            requested_quantity=requested_quantity,
            since=since,
            until=until,
            submission_status=submission_status,
            limit=limit,
            offset=offset,
        )
    except ValidationError as exc:
        raise ValidationAppError(
            "History filters require timezone-aware dates and a valid time range."
        ) from exc
    return ManualDemoHistoryService(session).list(tenant, filters)


@router.get("/manual-demo/commands/{command_id}")
def get_manual_demo_command(
    command_id: uuid.UUID, tenant: OwnerDep, session: SessionDep
) -> ManualDemoAttempt:
    return ManualDemoHistoryService(session).get(tenant, command_id)


@router.post("/manual-demo/{command_id}/resolve", dependencies=[_PAPER_PLAN_RATE_LIMIT])
def resolve_manual_demo_command(
    command_id: uuid.UUID,
    body: ManualDemoCancelRequest,
    tenant: OwnerDep,
    session: SessionDep,
    settings: SettingsDep,
) -> ManualDemoStatus:
    return ManualDemoService(session, settings).resolve(tenant, command_id)


@router.post("/manual-demo/{command_id}/reconcile", dependencies=[_PAPER_PLAN_RATE_LIMIT])
def reconcile_manual_demo(
    command_id: uuid.UUID,
    tenant: OwnerDep,
    session: SessionDep,
    settings: SettingsDep,
) -> ManualDemoStatus:
    return ManualDemoService(session, settings).reconcile(tenant, command_id)


@router.post("/manual-demo/{command_id}/cancel", dependencies=[_PAPER_PLAN_RATE_LIMIT])
def cancel_manual_demo(
    command_id: uuid.UUID,
    body: ManualDemoCancelRequest,
    tenant: OwnerDep,
    session: SessionDep,
    settings: SettingsDep,
) -> ManualDemoStatus:
    return ManualDemoService(session, settings).cancel(tenant, command_id)
