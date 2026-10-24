"""Authentication and token lifecycle service."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AuthError, ValidationAppError
from app.db.models import Membership as MembershipModel
from app.db.models import Organization as OrganizationModel
from app.db.models import RefreshToken as RefreshTokenModel
from app.db.models import User as UserModel
from app.repositories.memberships import MembershipRepository
from app.repositories.organizations import OrganizationRepository
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.schemas.audit import AuditRecordCreate
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, TokenPair
from app.schemas.common import ActorType, AuditEventType, AuditResult, AuditSeverity, MembershipRole
from app.security.passwords import hash_password, validate_password, verify_password
from app.security.tenant import TenantContext
from app.security.token_denylist import AccessTokenDenylist, get_access_token_denylist
from app.security.tokens import create_access_token, decode_access_token
from app.services.account_service import AccountService
from app.services.audit_service import AuditService

logger = structlog.get_logger(__name__)


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        audit_service: AuditService | None = None,
        denylist: AccessTokenDenylist | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._users = UserRepository(session)
        self._orgs = OrganizationRepository(session)
        self._memberships = MembershipRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)
        self._audit = audit_service or AuditService(session)
        self._denylist = denylist if denylist is not None else get_access_token_denylist(settings)

    def register(self, data: RegisterRequest) -> AuthResponse:
        validate_password(data.password, self._settings)
        if self._users.get_by_email(str(data.email)) is not None:
            raise ValidationAppError("An account with this email already exists.")

        org = OrganizationModel(name=data.organization_name)
        self._orgs.add(org)
        user = UserModel(
            email=str(data.email).lower(),
            hashed_password=hash_password(data.password, self._settings),
        )
        self._users.add(user)
        membership = MembershipModel(
            user_id=user.id,
            organization_id=org.id,
            role=MembershipRole.OWNER,
        )
        self._memberships.add(membership)
        account = AccountService(self._session, self._settings, audit_service=self._audit)
        account.issue_verification_for_user(user)
        if self._settings.email_auto_verify_local and self._settings.environment.value == "local":
            user.email_verified = True
        tokens = self._issue_tokens(user=user, organization_id=org.id)
        logger.info("user_registered", user_id=str(user.id), organization_id=str(org.id))
        return self._build_auth_response(user, org, tokens, MembershipRole.OWNER)

    def login(self, data: LoginRequest) -> AuthResponse:
        user = self._users.get_by_email(str(data.email).lower())
        if user is None or not verify_password(data.password, user.hashed_password, self._settings):
            raise AuthError("Invalid email or password.")
        if not user.is_active:
            raise AuthError("Account is inactive.")
        if self._settings.must_verify_email and not user.email_verified:
            raise AuthError("Email address is not verified.")
        membership = self._memberships.get_primary_for_user(user.id)
        if membership is None:
            raise AuthError("No organization membership found for this user.")
        org = self._orgs.get(membership.organization_id)
        if org is None:
            raise AuthError("Organization not found.")
        tokens = self._issue_tokens(user=user, organization_id=org.id)
        logger.info("user_login", user_id=str(user.id), organization_id=str(org.id))
        return self._build_auth_response(user, org, tokens, membership.role)

    def refresh(self, refresh_token: str) -> TokenPair:
        token_hash = _hash_refresh_token(refresh_token)
        row = self._refresh_tokens.get_by_hash(token_hash)
        if row is None:
            self._record_auth_event(
                AuditEventType.AUTH_INVALID_TOKEN,
                metadata={"reason": "refresh_not_found"},
            )
            raise AuthError("Invalid refresh token.")
        if row.revoked_at is not None:
            if row.replaced_by_id is not None:
                membership = self._memberships.get_primary_for_user(row.user_id)
                revoked_count = self._refresh_tokens.revoke_all_active_for_user(row.user_id)
                # Persist revokes before raise; audit is durable-isolated.
                self._session.commit()
                self._record_auth_event(
                    AuditEventType.AUTH_REFRESH_REUSE,
                    user_id=row.user_id,
                    organization_id=membership.organization_id if membership else None,
                    metadata={"revoked_sessions": revoked_count},
                    severity=AuditSeverity.HIGH,
                )
                logger.warning(
                    "refresh_token_reuse_detected",
                    user_id=str(row.user_id),
                    revoked_sessions=revoked_count,
                )
                raise AuthError("Refresh token reuse detected.")
            self._record_auth_event(
                AuditEventType.AUTH_INVALID_TOKEN,
                user_id=row.user_id,
                metadata={"reason": "refresh_revoked"},
            )
            raise AuthError("Invalid refresh token.")
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise AuthError("Refresh token expired.")
        user = self._users.get(row.user_id)
        if user is None or not user.is_active:
            raise AuthError("Account is unavailable.")
        membership = self._memberships.get_primary_for_user(user.id)
        if membership is None:
            raise AuthError("No organization membership found for this user.")
        new_refresh = secrets.token_urlsafe(48)
        new_row = RefreshTokenModel(
            user_id=user.id,
            token_hash=_hash_refresh_token(new_refresh),
            expires_at=datetime.now(UTC) + timedelta(days=self._settings.refresh_token_expire_days),
        )
        self._refresh_tokens.add(new_row)
        self._refresh_tokens.revoke(row, replaced_by_id=new_row.id)
        access_token, expires_in = create_access_token(
            user_id=user.id,
            organization_id=membership.organization_id,
            email=user.email,
            settings=self._settings,
            actor="user",
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=new_refresh,
            expires_in=expires_in,
        )

    def logout(
        self,
        refresh_token: str | None,
        *,
        access_token: str | None = None,
    ) -> None:
        if refresh_token:
            token_hash = _hash_refresh_token(refresh_token)
            row = self._refresh_tokens.get_by_hash(token_hash)
            if row is not None and row.revoked_at is None:
                self._refresh_tokens.revoke(row)
        if access_token:
            self._denylist_access_token(access_token)
        logger.info("user_logout")

    def resolve_tenant(self, access_token: str) -> TenantContext:
        payload = decode_access_token(access_token, self._settings)
        jti = payload.get("jti")
        if jti and self._denylist.is_denied(str(jti)):
            self._record_auth_event(
                AuditEventType.AUTH_ACCESS_REVOKED,
                user_id=uuid.UUID(str(payload["sub"])) if payload.get("sub") else None,
                metadata={"reason": "access_denylisted"},
            )
            raise AuthError("Access token has been revoked.")
        user_id = uuid.UUID(str(payload["sub"]))
        organization_id = uuid.UUID(str(payload["org_id"]))
        user = self._users.get(user_id)
        if user is None or not user.is_active:
            raise AuthError("Account is unavailable.")
        membership = self._memberships.get_primary_for_user(user_id)
        if membership is None or membership.organization_id != organization_id:
            raise AuthError("Organization membership not valid for this token.")
        return TenantContext(
            user_id=user.id,
            organization_id=organization_id,
            email=user.email,
            membership_role=membership.role,
        )

    def get_me(self, tenant: TenantContext) -> AuthResponse:
        user = self._users.get(tenant.user_id)
        org = self._orgs.get(tenant.organization_id)
        if user is None or org is None:
            raise AuthError("Account context is invalid.")
        return self._build_auth_response(
            user,
            org,
            TokenPair(access_token="", refresh_token="", expires_in=1),
            tenant.membership_role,
        )

    def sanitize_token_response(self, tokens: TokenPair) -> TokenPair:
        """Omit refresh token from JSON when httpOnly cookie mode is active."""
        cookie_mode = self._settings.auth_refresh_cookie_enabled
        omit_body = self._settings.auth_omit_refresh_from_body
        if cookie_mode and omit_body:
            return TokenPair(
                access_token=tokens.access_token,
                refresh_token="",
                expires_in=tokens.expires_in,
            )
        return tokens

    def _denylist_access_token(self, access_token: str) -> None:
        if not self._settings.access_token_denylist_enabled:
            return
        try:
            payload = decode_access_token(access_token, self._settings)
        except AuthError:
            return
        jti = payload.get("jti")
        exp = payload.get("exp")
        if not jti or not exp:
            return
        now = datetime.now(UTC).timestamp()
        ttl = max(int(exp - now), 1)
        self._denylist.add(str(jti), ttl_seconds=ttl)

    def _record_auth_event(
        self,
        event_type: AuditEventType,
        *,
        user_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        metadata: dict[str, object] | None = None,
        severity: AuditSeverity = AuditSeverity.MEDIUM,
    ) -> None:
        self._audit.record_durable_isolated(
            AuditRecordCreate(
                request_id="auth",
                trace_id="auth",
                event_type=event_type,
                resource_type="auth",
                actor_type=ActorType.SYSTEM,
                user_id=user_id,
                organization_id=organization_id,
                result=AuditResult.BLOCKED,
                severity=severity,
                metadata=metadata or {},
            )
        )

    def _issue_tokens(self, *, user: UserModel, organization_id: uuid.UUID) -> TokenPair:
        access_token, expires_in = create_access_token(
            user_id=user.id,
            organization_id=organization_id,
            email=user.email,
            settings=self._settings,
            actor="user",
        )
        refresh_token = secrets.token_urlsafe(48)
        self._refresh_tokens.add(
            RefreshTokenModel(
                user_id=user.id,
                token_hash=_hash_refresh_token(refresh_token),
                expires_at=datetime.now(UTC)
                + timedelta(days=self._settings.refresh_token_expire_days),
            )
        )
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

    @staticmethod
    def _build_auth_response(
        user: UserModel,
        org: OrganizationModel,
        tokens: TokenPair,
        membership_role: MembershipRole | None = None,
    ) -> AuthResponse:
        from app.schemas.auth import Organization, User

        return AuthResponse(
            membership_role=membership_role,
            user=User(
                id=user.id,
                email=user.email,
                role=user.role,
                risk_profile=user.risk_profile,
                timezone=user.timezone,
                is_active=user.is_active,
                email_verified=user.email_verified,
                created_at=user.created_at,
            ),
            organization=Organization(id=org.id, name=org.name, created_at=org.created_at),
            tokens=tokens,
        )
