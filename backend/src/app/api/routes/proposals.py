"""Trade proposal API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.core.auth import TenantDep
from app.core.dependencies import ProposalServiceDep, SessionDep, WorkflowServiceDep
from app.schemas.proposal import (
    LossAcceptanceUpdate,
    PaginatedTradeProposals,
    ProposalStatusUpdate,
    TradeProposal,
    TradeProposalCreate,
)
from app.schemas.trade_plan import TradePlanRevision
from app.schemas.workflow import ProposalWorkflowView
from app.security.rbac import TraderDep
from app.security.tenant import ensure_same_organization

router = APIRouter(prefix="/proposals", tags=["proposals"])


@router.post("", response_model=TradeProposal, summary="Create trade proposal")
async def create_proposal(
    body: TradeProposalCreate,
    tenant: TraderDep,
    proposal_service: ProposalServiceDep,
    session: SessionDep,
) -> TradeProposal:
    payload = body.model_copy(
        update={"organization_id": tenant.organization_id, "user_id": tenant.user_id}
    )
    result = proposal_service.create(payload)
    session.commit()
    return result


@router.get("", response_model=PaginatedTradeProposals, summary="List trade proposals")
async def list_proposals(
    tenant: TenantDep,
    proposal_service: ProposalServiceDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedTradeProposals:
    items, total = proposal_service.list_proposals(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        limit=limit,
        offset=offset,
    )
    return PaginatedTradeProposals(items=items, total=total, limit=limit, offset=offset)


@router.get("/{proposal_id}", response_model=TradeProposal, summary="Get trade proposal")
async def get_proposal(
    proposal_id: uuid.UUID,
    tenant: TenantDep,
    proposal_service: ProposalServiceDep,
) -> TradeProposal:
    proposal = proposal_service.get(
        proposal_id,
        organization_id=tenant.organization_id,
    )
    ensure_same_organization(
        proposal.organization_id, tenant, not_found_message="Trade proposal not found"
    )
    return proposal


@router.get(
    "/{proposal_id}/workflow",
    response_model=ProposalWorkflowView,
    summary="Get proposal with linked approval and paper execution eligibility",
)
async def get_proposal_workflow(
    proposal_id: uuid.UUID,
    tenant: TenantDep,
    workflow_service: WorkflowServiceDep,
) -> ProposalWorkflowView:
    view = workflow_service.get_proposal_workflow(
        proposal_id,
        organization_id=tenant.organization_id,
    )
    ensure_same_organization(
        view.proposal.organization_id, tenant, not_found_message="Trade proposal not found"
    )
    return view


@router.patch(
    "/{proposal_id}/status",
    response_model=TradeProposal,
    summary="Update proposal status",
)
async def update_proposal_status(
    proposal_id: uuid.UUID,
    body: ProposalStatusUpdate,
    tenant: TraderDep,
    proposal_service: ProposalServiceDep,
    session: SessionDep,
) -> TradeProposal:
    proposal = proposal_service.get(
        proposal_id,
        organization_id=tenant.organization_id,
    )
    ensure_same_organization(
        proposal.organization_id, tenant, not_found_message="Trade proposal not found"
    )
    result = proposal_service.update_status(
        proposal_id,
        body,
        organization_id=tenant.organization_id,
    )
    session.commit()
    return result


@router.patch(
    "/{proposal_id}/loss-acceptance",
    response_model=TradeProposal,
    summary="Confirm or reject planned loss",
)
async def update_loss_acceptance(
    proposal_id: uuid.UUID,
    body: LossAcceptanceUpdate,
    tenant: TraderDep,
    proposal_service: ProposalServiceDep,
    session: SessionDep,
) -> TradeProposal:
    proposal = proposal_service.get(
        proposal_id,
        organization_id=tenant.organization_id,
    )
    ensure_same_organization(
        proposal.organization_id, tenant, not_found_message="Trade proposal not found"
    )
    result = proposal_service.update_loss_acceptance(
        proposal_id,
        body,
        organization_id=tenant.organization_id,
    )
    session.commit()
    return result


@router.get(
    "/{proposal_id}/revisions",
    response_model=list[TradePlanRevision],
    summary="List immutable plan revisions",
)
async def list_trade_plan_revisions(
    proposal_id: uuid.UUID,
    tenant: TenantDep,
    proposal_service: ProposalServiceDep,
) -> list[TradePlanRevision]:
    return proposal_service.list_revisions(
        proposal_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.get(
    "/{proposal_id}/revisions/{revision_id}",
    response_model=TradePlanRevision,
    summary="Get one immutable plan revision",
)
async def get_trade_plan_revision(
    proposal_id: uuid.UUID,
    revision_id: uuid.UUID,
    tenant: TenantDep,
    proposal_service: ProposalServiceDep,
) -> TradePlanRevision:
    return proposal_service.get_revision(
        proposal_id,
        revision_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
