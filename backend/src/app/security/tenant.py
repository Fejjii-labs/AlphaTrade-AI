"""Tenant context resolved from authenticated requests."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.errors import NotFoundError
from app.schemas.common import MembershipRole


@dataclass(frozen=True, slots=True)
class TenantContext:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    membership_role: MembershipRole


def ensure_same_organization(
    resource_org_id: uuid.UUID,
    tenant: TenantContext,
    *,
    not_found_message: str = "Resource not found.",
) -> None:
    """Answer another organization's resource exactly like a missing one (I10).

    Pass the caller's own not-found message so status, code and text all match.
    """
    if resource_org_id != tenant.organization_id:
        raise NotFoundError(not_found_message)
