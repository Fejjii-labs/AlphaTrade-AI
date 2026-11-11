"""Role-based access control for organization-scoped API routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends

from app.core.auth import TenantDep
from app.core.config import Settings, get_settings
from app.core.errors import ForbiddenError
from app.schemas.common import MembershipRole
from app.security.tenant import TenantContext

MUTATION_ROLES = frozenset({MembershipRole.OWNER, MembershipRole.TRADER})
READ_ROLES = frozenset({MembershipRole.OWNER, MembershipRole.TRADER, MembershipRole.VIEWER})


def require_membership_roles(*roles: MembershipRole) -> Callable[[TenantContext], TenantContext]:
    """Return a dependency that permits only the given membership roles."""

    allowed = frozenset(roles)

    def _dependency(tenant: TenantDep) -> TenantContext:
        if tenant.membership_role not in allowed:
            raise ForbiddenError(
                "You do not have permission to perform this action.",
                details={
                    "role": tenant.membership_role.value,
                    "required_roles": sorted(role.value for role in allowed),
                },
            )
        return tenant

    return _dependency


TraderDep = Annotated[
    TenantContext,
    Depends(require_membership_roles(MembershipRole.OWNER, MembershipRole.TRADER)),
]

OwnerDep = Annotated[
    TenantContext,
    Depends(require_membership_roles(MembershipRole.OWNER)),
]


def require_platform_operator(
    tenant: OwnerDep,
    settings: Settings = Depends(get_settings),
) -> TenantContext:
    """Require tenant ownership and explicit platform operator authorization."""
    if tenant.user_id not in settings.platform_operator_user_ids:
        raise ForbiddenError("Platform operator authorization is required to control the worker.")
    return tenant


PlatformOperatorDep = Annotated[TenantContext, Depends(require_platform_operator)]


ReaderDep = Annotated[
    TenantContext,
    Depends(
        require_membership_roles(
            MembershipRole.OWNER,
            MembershipRole.TRADER,
            MembershipRole.VIEWER,
        )
    ),
]
