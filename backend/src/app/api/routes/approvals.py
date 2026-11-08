"""Human approval workflow API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.core.auth import TenantDep
from app.core.dependencies import ApprovalServiceDep, SessionDep, WorkflowServiceDep
from app.core.errors import NotFoundError
from app.schemas.approval import (
    ApprovalActionRequest,
    ApprovalAuthorization,
    ApprovalDecisionRequest,
    ApprovalRequest,
    PaginatedApprovalRequests,
    PlanApprovalRequestCreate,
)
from app.schemas.common import ApprovalAction, ApprovalStatus
from app.schemas.trade_plan import AuthorizationChannel
from app.schemas.workflow import ApprovalWorkflowView
from app.security.rbac import TraderDep
from app.security.tenant import ensure_same_organization

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=PaginatedApprovalRequests, summary="List approvals")
async def list_approvals(
    tenant: TenantDep,
    approval_service: ApprovalServiceDep,
    status: ApprovalStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedApprovalRequests:
    items, total = approval_service.list_approvals(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return PaginatedApprovalRequests(items=items, total=total, limit=limit, offset=offset)


@router.post(
    "/plan-revisions/{revision_id}",
    response_model=ApprovalRequest,
    summary="Create an approval request for one immutable plan revision",
)
async def create_plan_revision_approval(
    revision_id: uuid.UUID,
    body: PlanApprovalRequestCreate,
    tenant: TraderDep,
    approval_service: ApprovalServiceDep,
    session: SessionDep,
) -> ApprovalRequest:
    result = approval_service.create_for_plan_revision(
        revision_id=revision_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        authorization_expires_at=body.authorization_expires_at,
        approval_reason=body.reason,
    )
    session.commit()
    return result


@router.get("/{approval_id}", response_model=ApprovalRequest, summary="Get approval")
async def get_approval(
    approval_id: uuid.UUID,
    tenant: TenantDep,
    approval_service: ApprovalServiceDep,
) -> ApprovalRequest:
    return approval_service.get_scoped(
        approval_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.get(
    "/{approval_id}/workflow",
    response_model=ApprovalWorkflowView,
    summary="Get approval with linked proposal and paper execution eligibility",
)
async def get_approval_workflow(
    approval_id: uuid.UUID,
    tenant: TenantDep,
    workflow_service: WorkflowServiceDep,
) -> ApprovalWorkflowView:
    view = workflow_service.get_approval_workflow(
        approval_id,
        organization_id=tenant.organization_id,
    )
    ensure_same_organization(
        view.approval.organization_id, tenant, not_found_message="Approval not found"
    )
    if view.approval.user_id != tenant.user_id:
        raise NotFoundError("Approval not found")
    return view


@router.post("/{approval_id}/approve", response_model=ApprovalRequest)
async def approve(
    approval_id: uuid.UUID,
    body: ApprovalActionRequest,
    tenant: TraderDep,
    approval_service: ApprovalServiceDep,
    session: SessionDep,
) -> ApprovalRequest:
    # Approvals are per-user like list/get: a colleague's is not found (M15).
    approval_service.get_scoped(
        approval_id, organization_id=tenant.organization_id, user_id=tenant.user_id
    )
    result = approval_service.decide(
        approval_id,
        ApprovalDecisionRequest(
            action=ApprovalAction.APPROVE,
            reason=body.reason,
            authorization_assertion=body.authorization_assertion,
        ),
        principal_organization_id=tenant.organization_id,
        principal_user_id=tenant.user_id,
        channel=AuthorizationChannel.API,
    )
    session.commit()
    return result


@router.post("/{approval_id}/reject", response_model=ApprovalRequest)
async def reject(
    approval_id: uuid.UUID,
    body: ApprovalActionRequest,
    tenant: TraderDep,
    approval_service: ApprovalServiceDep,
    session: SessionDep,
) -> ApprovalRequest:
    # Approvals are per-user like list/get: a colleague's is not found (M15).
    approval_service.get_scoped(
        approval_id, organization_id=tenant.organization_id, user_id=tenant.user_id
    )
    result = approval_service.decide(
        approval_id,
        ApprovalDecisionRequest(action=ApprovalAction.REJECT, reason=body.reason),
        principal_organization_id=tenant.organization_id,
        principal_user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post("/{approval_id}/modify", response_model=ApprovalRequest)
async def modify(
    approval_id: uuid.UUID,
    body: ApprovalActionRequest,
    tenant: TraderDep,
    approval_service: ApprovalServiceDep,
    session: SessionDep,
) -> ApprovalRequest:
    # Approvals are per-user like list/get: a colleague's is not found (M15).
    approval_service.get_scoped(
        approval_id, organization_id=tenant.organization_id, user_id=tenant.user_id
    )
    result = approval_service.decide(
        approval_id,
        ApprovalDecisionRequest(
            action=ApprovalAction.MODIFY,
            reason=body.reason,
            modified_fields=body.modified_fields,
        ),
        principal_organization_id=tenant.organization_id,
        principal_user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post("/{approval_id}/needs-more-analysis", response_model=ApprovalRequest)
async def needs_more_analysis(
    approval_id: uuid.UUID,
    body: ApprovalActionRequest,
    tenant: TraderDep,
    approval_service: ApprovalServiceDep,
    session: SessionDep,
) -> ApprovalRequest:
    # Approvals are per-user like list/get: a colleague's is not found (M15).
    approval_service.get_scoped(
        approval_id, organization_id=tenant.organization_id, user_id=tenant.user_id
    )
    result = approval_service.decide(
        approval_id,
        ApprovalDecisionRequest(
            action=ApprovalAction.NEEDS_MORE_ANALYSIS,
            reason=body.reason,
        ),
        principal_organization_id=tenant.organization_id,
        principal_user_id=tenant.user_id,
    )
    session.commit()
    return result


@router.post("/{approval_id}/decide", response_model=ApprovalRequest)
async def decide_approval(
    approval_id: uuid.UUID,
    body: ApprovalDecisionRequest,
    tenant: TraderDep,
    approval_service: ApprovalServiceDep,
    session: SessionDep,
) -> ApprovalRequest:
    # Approvals are per-user like list/get: a colleague's is not found (M15).
    approval_service.get_scoped(
        approval_id, organization_id=tenant.organization_id, user_id=tenant.user_id
    )
    result = approval_service.decide(
        approval_id,
        body,
        principal_organization_id=tenant.organization_id,
        principal_user_id=tenant.user_id,
        channel=AuthorizationChannel.API,
    )
    session.commit()
    return result


@router.get(
    "/authorizations/{authorization_id}",
    response_model=ApprovalAuthorization,
    summary="Get an account-bound approval authorization",
)
async def get_authorization(
    authorization_id: uuid.UUID,
    tenant: TenantDep,
    approval_service: ApprovalServiceDep,
) -> ApprovalAuthorization:
    return approval_service.get_authorization(
        authorization_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.post(
    "/authorizations/{authorization_id}/revoke",
    response_model=ApprovalAuthorization,
    summary="Revoke an available approval authorization",
)
async def revoke_authorization(
    authorization_id: uuid.UUID,
    tenant: TraderDep,
    approval_service: ApprovalServiceDep,
    session: SessionDep,
) -> ApprovalAuthorization:
    result = approval_service.revoke_authorization(
        authorization_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    session.commit()
    return result
