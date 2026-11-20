"""Organization management API (invitations groundwork)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.core.auth import AccountServiceDep, TenantDep
from app.core.dependencies import SessionDep
from app.schemas.account import (
    AcceptInvitationRequest,
    CreateInvitationRequest,
    InvitationListResponse,
    OrganizationInvitationView,
)
from app.security.rbac import OwnerDep

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.post(
    "/invitations",
    response_model=OrganizationInvitationView,
    status_code=status.HTTP_201_CREATED,
)
def create_invitation(
    body: CreateInvitationRequest,
    tenant: OwnerDep,
    account: AccountServiceDep,
    session: SessionDep,
) -> OrganizationInvitationView:
    result = account.create_invitation(tenant, body)
    session.commit()
    return result


@router.get("/invitations", response_model=InvitationListResponse)
def list_invitations(
    tenant: OwnerDep,
    account: AccountServiceDep,
) -> InvitationListResponse:
    invitations = account.list_invitations(tenant)
    return InvitationListResponse(invitations=invitations)


@router.post(
    "/invitations/{invitation_id}/accept",
    response_model=OrganizationInvitationView,
)
def accept_invitation(
    invitation_id: uuid.UUID,
    body: AcceptInvitationRequest,
    tenant: TenantDep,
    account: AccountServiceDep,
    session: SessionDep,
) -> OrganizationInvitationView:
    result = account.accept_invitation(tenant, invitation_id, body)
    session.commit()
    return result


@router.post(
    "/invitations/{invitation_id}/revoke",
    response_model=OrganizationInvitationView,
)
def revoke_invitation(
    invitation_id: uuid.UUID,
    tenant: OwnerDep,
    account: AccountServiceDep,
    session: SessionDep,
) -> OrganizationInvitationView:
    result = account.revoke_invitation(tenant, invitation_id)
    session.commit()
    return result
