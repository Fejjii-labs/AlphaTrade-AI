"""FastAPI authentication dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AuthError
from app.db.session import get_session
from app.security.tenant import TenantContext
from app.services.account_service import AccountService
from app.services.auth_service import AuthService

_bearer = HTTPBearer(auto_error=False)


def optional_bearer(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> HTTPAuthorizationCredentials | None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    return credentials


def get_auth_service(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    from app.services.audit_service import AuditService

    return AuthService(session, settings, audit_service=AuditService(session))


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_account_service(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AccountService:
    from app.services.audit_service import AuditService

    return AccountService(session, settings, audit_service=AuditService(session))


AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]


def get_current_tenant(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    auth_service: AuthService = Depends(get_auth_service),
) -> TenantContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthError("Missing bearer token.")
    return auth_service.resolve_tenant(credentials.credentials)


TenantDep = Annotated[TenantContext, Depends(get_current_tenant)]


def get_current_tenant_allow_unverified(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    auth_service: AuthService = Depends(get_auth_service),
) -> TenantContext:
    """Like ``get_current_tenant`` but tolerates an unverified email (``/auth/me`` only)."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthError("Missing bearer token.")
    return auth_service.resolve_tenant(credentials.credentials, allow_unverified=True)


UnverifiedTenantDep = Annotated[TenantContext, Depends(get_current_tenant_allow_unverified)]


def get_optional_tenant(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    auth_service: AuthService = Depends(get_auth_service),
) -> TenantContext | None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    # Only the verification-request route uses this; it must work before verification.
    return auth_service.resolve_tenant(credentials.credentials, allow_unverified=True)


OptionalTenantDep = Annotated[TenantContext | None, Depends(get_optional_tenant)]
