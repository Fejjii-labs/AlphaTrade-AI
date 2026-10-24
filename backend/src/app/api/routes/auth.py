"""Authentication API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials

from app.core.auth import (
    AccountServiceDep,
    AuthServiceDep,
    OptionalTenantDep,
    TenantDep,
    optional_bearer,
)
from app.core.config import Settings, get_settings
from app.core.dependencies import SessionDep
from app.core.errors import AuthError
from app.schemas.account import (
    MessageResponse,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    VerifyEmailConfirmRequest,
    VerifyEmailRequest,
)
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
)
from app.security.cookies import clear_refresh_cookie, set_refresh_cookie
from app.security.rate_limit import auth_register_rate_limit, public_rate_limit_dependency

router = APIRouter(prefix="/auth", tags=["auth"])

_AUTH_REGISTER_LIMIT = Depends(auth_register_rate_limit)
_AUTH_LOGIN_LIMIT = Depends(
    public_rate_limit_dependency("auth:login", limit=20, window_seconds=3600, ip_limit=20)
)
_AUTH_REFRESH_LIMIT = Depends(
    public_rate_limit_dependency("auth:refresh", limit=60, window_seconds=3600, ip_limit=60)
)
_VERIFY_EMAIL_LIMIT = Depends(
    public_rate_limit_dependency(
        "auth:verify-email:request", limit=5, window_seconds=3600, ip_limit=5
    )
)
_PASSWORD_RESET_LIMIT = Depends(
    public_rate_limit_dependency(
        "auth:password-reset:request", limit=5, window_seconds=3600, ip_limit=5
    )
)


def _resolve_refresh_token(
    request: Request,
    body_token: str | None,
    settings: Settings,
) -> str:
    if body_token:
        return body_token
    if settings.auth_refresh_cookie_enabled:
        cookie_token = request.cookies.get(settings.auth_refresh_cookie_name)
        if cookie_token:
            return cookie_token
    raise AuthError("Missing refresh token.")


def _apply_auth_cookies(
    response: Response,
    tokens: TokenPair,
    settings: Settings,
    auth_service: AuthServiceDep,
) -> TokenPair:
    if settings.auth_refresh_cookie_enabled and tokens.refresh_token:
        set_refresh_cookie(response, tokens.refresh_token, settings)
    return auth_service.sanitize_token_response(tokens)


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_AUTH_REGISTER_LIMIT],
)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    session: SessionDep,
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    result = auth_service.register(body)
    session.commit()
    result.tokens = _apply_auth_cookies(response, result.tokens, settings, auth_service)
    return result


@router.post(
    "/login",
    response_model=AuthResponse,
    dependencies=[_AUTH_LOGIN_LIMIT],
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    session: SessionDep,
    settings: Settings = Depends(get_settings),
) -> AuthResponse:
    result = auth_service.login(body)
    session.commit()
    result.tokens = _apply_auth_cookies(response, result.tokens, settings, auth_service)
    return result


@router.post(
    "/refresh",
    response_model=TokenPair,
    dependencies=[_AUTH_REFRESH_LIMIT],
)
async def refresh(
    body: RefreshRequest,
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    session: SessionDep,
    settings: Settings = Depends(get_settings),
) -> TokenPair:
    refresh_token = _resolve_refresh_token(request, body.refresh_token, settings)
    result = auth_service.refresh(refresh_token)
    session.commit()
    return _apply_auth_cookies(response, result, settings, auth_service)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    body: LogoutRequest,
    request: Request,
    response: Response,
    auth_service: AuthServiceDep,
    session: SessionDep,
    settings: Settings = Depends(get_settings),
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
) -> MessageResponse:
    refresh_token: str | None = None
    try:
        refresh_token = _resolve_refresh_token(request, body.refresh_token, settings)
    except AuthError:
        refresh_token = body.refresh_token
    access_token = credentials.credentials if credentials else None
    auth_service.logout(refresh_token, access_token=access_token)
    session.commit()
    clear_refresh_cookie(response, settings)
    return MessageResponse(message="Logged out.")


@router.get("/me", response_model=MeResponse)
async def me(tenant: TenantDep, auth_service: AuthServiceDep) -> MeResponse:
    response = auth_service.get_me(tenant)
    return MeResponse(
        user=response.user,
        organization=response.organization,
        membership_role=response.membership_role,
    )


@router.post(
    "/verify-email/request",
    response_model=MessageResponse,
    dependencies=[_VERIFY_EMAIL_LIMIT],
)
async def request_verify_email(
    body: VerifyEmailRequest,
    account: AccountServiceDep,
    session: SessionDep,
    tenant: OptionalTenantDep = None,
) -> MessageResponse:
    message = account.request_verification(body, tenant=tenant)
    session.commit()
    return MessageResponse(message=message)


@router.post("/verify-email/confirm", response_model=MessageResponse)
async def confirm_verify_email(
    body: VerifyEmailConfirmRequest,
    account: AccountServiceDep,
    session: SessionDep,
) -> MessageResponse:
    message = account.confirm_verification(body)
    session.commit()
    return MessageResponse(message=message)


@router.post(
    "/password-reset/request",
    response_model=MessageResponse,
    dependencies=[_PASSWORD_RESET_LIMIT],
)
async def request_password_reset(
    body: PasswordResetRequest,
    account: AccountServiceDep,
    session: SessionDep,
) -> MessageResponse:
    message = account.request_password_reset(body)
    session.commit()
    return MessageResponse(message=message)


@router.post("/password-reset/confirm", response_model=MessageResponse)
async def confirm_password_reset(
    body: PasswordResetConfirmRequest,
    request: Request,
    account: AccountServiceDep,
    session: SessionDep,
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_bearer),
) -> MessageResponse:
    access_token = credentials.credentials if credentials else None
    message = account.confirm_password_reset(body, access_token=access_token)
    session.commit()
    return MessageResponse(message=message)
