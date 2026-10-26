"""Email verification, password reset, and organization invitations."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AuthError, ForbiddenError, NotFoundError, ValidationAppError
from app.db.models import EmailVerificationToken as EmailVerificationTokenModel
from app.db.models import Membership as MembershipModel
from app.db.models import OrganizationInvitation as OrganizationInvitationModel
from app.db.models import PasswordResetToken as PasswordResetTokenModel
from app.db.models import User as UserModel
from app.providers.email.base import EmailMessage, EmailProvider
from app.providers.email.factory import resolve_email_provider
from app.repositories.email_verification_tokens import EmailVerificationTokenRepository
from app.repositories.memberships import MembershipRepository
from app.repositories.organization_invitations import OrganizationInvitationRepository
from app.repositories.password_reset_tokens import PasswordResetTokenRepository
from app.repositories.refresh_tokens import RefreshTokenRepository
from app.repositories.users import UserRepository
from app.schemas.account import (
    AcceptInvitationRequest,
    CreateInvitationRequest,
    OrganizationInvitationView,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    VerifyEmailConfirmRequest,
    VerifyEmailRequest,
)
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    ActorType,
    AuditEventType,
    AuditResult,
    AuditSeverity,
    MembershipRole,
)
from app.security.account_tokens import generate_account_token, hash_account_token
from app.security.passwords import hash_password, validate_password
from app.security.tenant import TenantContext
from app.security.token_denylist import AccessTokenDenylist, get_access_token_denylist
from app.security.tokens import decode_access_token
from app.services.audit_service import AuditService

logger = structlog.get_logger(__name__)

_GENERIC_RESET_MESSAGE = (
    "If an account exists for this email, password reset instructions have been sent."
)
_GENERIC_VERIFY_MESSAGE = (
    "If your account requires verification, a confirmation email has been sent."
)


class AccountService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        audit_service: AuditService | None = None,
        email_provider: EmailProvider | None = None,
        denylist: AccessTokenDenylist | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._users = UserRepository(session)
        self._memberships = MembershipRepository(session)
        self._verification_tokens = EmailVerificationTokenRepository(session)
        self._reset_tokens = PasswordResetTokenRepository(session)
        self._invitations = OrganizationInvitationRepository(session)
        self._refresh_tokens = RefreshTokenRepository(session)
        self._audit = audit_service or AuditService(session)
        self._email = email_provider or resolve_email_provider(settings)
        self._denylist = denylist if denylist is not None else get_access_token_denylist(settings)

    def issue_verification_for_user(self, user: UserModel) -> None:
        """Create a verification token and optionally send email (register/resend)."""
        if user.email_verified:
            return
        self._verification_tokens.invalidate_active_for_user(user.id)
        raw_token = generate_account_token()
        row = EmailVerificationTokenModel(
            user_id=user.id,
            token_hash=hash_account_token(raw_token),
            expires_at=datetime.now(UTC)
            + timedelta(hours=self._settings.email_verification_expire_hours),
        )
        self._verification_tokens.add(row)
        self._send_verification_email(user.email, raw_token)
        self._record_event(
            AuditEventType.AUTH_EMAIL_VERIFICATION_SENT,
            user_id=user.id,
            result=AuditResult.SUCCESS,
            metadata={"recipient": user.email},
        )

    def request_verification(
        self,
        data: VerifyEmailRequest,
        *,
        tenant: TenantContext | None = None,
    ) -> str:
        # A signed-in caller only ever gets mail at their own account email.
        if tenant is not None:
            email: str | None = tenant.email.lower()
        else:
            email = str(data.email).lower() if data.email else None
        if not email:
            raise ValidationAppError("Email is required.")
        user = self._users.get_by_email(email)
        if user is None or user.email_verified:
            return _GENERIC_VERIFY_MESSAGE
        self.issue_verification_for_user(user)
        return _GENERIC_VERIFY_MESSAGE

    def confirm_verification(self, data: VerifyEmailConfirmRequest) -> str:
        token_hash = hash_account_token(data.token)
        row = self._verification_tokens.get_by_hash(token_hash)
        if row is None:
            self._record_event(
                AuditEventType.AUTH_EMAIL_VERIFICATION_FAILED,
                result=AuditResult.FAILURE,
                metadata={"reason": "token_not_found"},
                durable=True,
            )
            raise AuthError("Invalid or expired verification link.")
        if row.consumed_at is not None:
            self._fail_verification(row.user_id, "token_consumed")
            raise AuthError("Invalid or expired verification link.")
        expires_at = self._ensure_aware(row.expires_at)
        if expires_at <= datetime.now(UTC):
            self._fail_verification(row.user_id, "token_expired")
            raise AuthError("Invalid or expired verification link.")
        user = self._users.get(row.user_id)
        if user is None or not user.is_active:
            self._fail_verification(row.user_id, "user_unavailable")
            raise AuthError("Invalid or expired verification link.")
        row.consumed_at = datetime.now(UTC)
        user.email_verified = True
        self._record_event(
            AuditEventType.AUTH_EMAIL_VERIFIED,
            user_id=user.id,
            result=AuditResult.SUCCESS,
        )
        logger.info("email_verified", user_id=str(user.id))
        return "Email address verified successfully."

    def request_password_reset(self, data: PasswordResetRequest) -> str:
        email = str(data.email).lower()
        user = self._users.get_by_email(email)
        if user is None or not user.is_active:
            return _GENERIC_RESET_MESSAGE
        self._reset_tokens.invalidate_active_for_user(user.id)
        raw_token = generate_account_token()
        row = PasswordResetTokenModel(
            user_id=user.id,
            token_hash=hash_account_token(raw_token),
            expires_at=datetime.now(UTC)
            + timedelta(hours=self._settings.password_reset_expire_hours),
        )
        self._reset_tokens.add(row)
        self._send_password_reset_email(user.email, raw_token)
        self._record_event(
            AuditEventType.AUTH_PASSWORD_RESET_REQUESTED,
            user_id=user.id,
            result=AuditResult.SUCCESS,
            metadata={"recipient": user.email},
        )
        return _GENERIC_RESET_MESSAGE

    def confirm_password_reset(
        self,
        data: PasswordResetConfirmRequest,
        *,
        access_token: str | None = None,
    ) -> str:
        validate_password(data.new_password, self._settings)
        token_hash = hash_account_token(data.token)
        row = self._reset_tokens.get_by_hash(token_hash)
        if row is None:
            self._record_event(
                AuditEventType.AUTH_PASSWORD_RESET_FAILED,
                result=AuditResult.FAILURE,
                metadata={"reason": "token_not_found"},
                durable=True,
            )
            raise AuthError("Invalid or expired reset link.")
        if row.consumed_at is not None:
            self._fail_reset(row.user_id, "token_consumed")
            raise AuthError("Invalid or expired reset link.")
        expires_at = self._ensure_aware(row.expires_at)
        if expires_at <= datetime.now(UTC):
            self._fail_reset(row.user_id, "token_expired")
            raise AuthError("Invalid or expired reset link.")
        user = self._users.get(row.user_id)
        if user is None or not user.is_active:
            self._fail_reset(row.user_id, "user_unavailable")
            raise AuthError("Invalid or expired reset link.")
        row.consumed_at = datetime.now(UTC)
        user.hashed_password = hash_password(data.new_password, self._settings)
        revoked = self._refresh_tokens.revoke_all_active_for_user(user.id)
        if access_token:
            self._denylist_access_token(access_token)
        self._record_event(
            AuditEventType.AUTH_PASSWORD_RESET_COMPLETED,
            user_id=user.id,
            result=AuditResult.SUCCESS,
            metadata={"revoked_sessions": revoked},
        )
        logger.info("password_reset_completed", user_id=str(user.id), revoked_sessions=revoked)
        return "Password updated successfully. Please sign in again."

    def create_invitation(
        self,
        tenant: TenantContext,
        data: CreateInvitationRequest,
    ) -> OrganizationInvitationView:
        if data.role == MembershipRole.OWNER:
            raise ValidationAppError("Cannot invite another owner via this endpoint.")
        email = str(data.email).lower()
        existing = self._users.get_by_email(email)
        if existing is not None:
            membership = self._memberships.get_for_user_and_org(existing.id, tenant.organization_id)
            if membership is not None:
                raise ValidationAppError("User is already a member of this organization.")
        raw_token = generate_account_token()
        row = OrganizationInvitationModel(
            organization_id=tenant.organization_id,
            email=email,
            role=data.role,
            invited_by_user_id=tenant.user_id,
            token_hash=hash_account_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(hours=self._settings.invitation_expire_hours),
        )
        self._invitations.add(row)
        self._send_invitation_email(email, raw_token, tenant.organization_id)
        self._record_event(
            AuditEventType.AUTH_INVITE_CREATED,
            user_id=tenant.user_id,
            organization_id=tenant.organization_id,
            result=AuditResult.SUCCESS,
            metadata={"invitee": email, "role": data.role.value},
        )
        return self._invitation_view(row)

    def list_invitations(self, tenant: TenantContext) -> list[OrganizationInvitationView]:
        rows = self._invitations.list_for_organization(tenant.organization_id)
        return [self._invitation_view(row) for row in rows]

    def accept_invitation(
        self,
        tenant: TenantContext,
        invitation_id: uuid.UUID,
        data: AcceptInvitationRequest,
    ) -> OrganizationInvitationView:
        row = self._invitations.get(invitation_id)
        if row is None:
            raise NotFoundError("Invitation not found.")
        if row.revoked_at is not None or row.accepted_at is not None:
            raise ValidationAppError("Invitation is no longer valid.")
        expires_at = self._ensure_aware(row.expires_at)
        if expires_at <= datetime.now(UTC):
            raise ValidationAppError("Invitation has expired.")
        if hash_account_token(data.token) != row.token_hash:
            self._record_event(
                AuditEventType.AUTH_INVALID_TOKEN,
                user_id=tenant.user_id,
                organization_id=row.organization_id,
                result=AuditResult.FAILURE,
                metadata={"reason": "invite_token_mismatch"},
            )
            raise AuthError("Invalid invitation token.")
        user = self._users.get(tenant.user_id)
        if user is None:
            raise AuthError("Account unavailable.")
        if user.email.lower() != row.email.lower():
            raise ForbiddenError("This invitation was sent to a different email address.")
        if self._memberships.get_for_user_and_org(user.id, row.organization_id) is not None:
            raise ValidationAppError("You are already a member of this organization.")
        membership = MembershipModel(
            user_id=user.id,
            organization_id=row.organization_id,
            role=row.role,
        )
        self._memberships.add(membership)
        row.accepted_at = datetime.now(UTC)
        row.accepted_by_user_id = user.id
        self._record_event(
            AuditEventType.AUTH_INVITE_ACCEPTED,
            user_id=user.id,
            organization_id=row.organization_id,
            result=AuditResult.SUCCESS,
            metadata={"role": row.role.value},
        )
        return self._invitation_view(row)

    def revoke_invitation(
        self,
        tenant: TenantContext,
        invitation_id: uuid.UUID,
    ) -> OrganizationInvitationView:
        row = self._invitations.get(invitation_id)
        if row is None or row.organization_id != tenant.organization_id:
            raise NotFoundError("Invitation not found.")
        if row.accepted_at is not None:
            raise ValidationAppError("Cannot revoke an accepted invitation.")
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
        self._record_event(
            AuditEventType.AUTH_INVITE_REVOKED,
            user_id=tenant.user_id,
            organization_id=tenant.organization_id,
            result=AuditResult.SUCCESS,
            metadata={"invitation_id": str(invitation_id)},
        )
        return self._invitation_view(row)

    def _send_verification_email(self, email: str, raw_token: str) -> None:
        link = f"{self._settings.email_base_url.rstrip('/')}/verify-email?token={raw_token}"
        self._deliver(
            EmailMessage(
                to_address=email,
                subject="Verify your AlphaTrade AI email",
                text_body=f"Confirm your email: {link}",
                template="email_verification",
            )
        )

    def _send_password_reset_email(self, email: str, raw_token: str) -> None:
        link = f"{self._settings.email_base_url.rstrip('/')}/reset-password?token={raw_token}"
        self._deliver(
            EmailMessage(
                to_address=email,
                subject="Reset your AlphaTrade AI password",
                text_body=f"Reset your password: {link}",
                template="password_reset",
            )
        )

    def _send_invitation_email(
        self,
        email: str,
        raw_token: str,
        organization_id: uuid.UUID,
    ) -> None:
        link = (
            f"{self._settings.email_base_url.rstrip('/')}/invitations/accept"
            f"?token={raw_token}&organization_id={organization_id}"
        )
        self._deliver(
            EmailMessage(
                to_address=email,
                subject="You are invited to AlphaTrade AI",
                text_body=f"Accept invitation: {link}",
                template="organization_invitation",
            )
        )

    def _deliver(self, message: EmailMessage) -> None:
        if not self._settings.email_send_enabled:
            return
        try:
            self._email.send(message)
        except Exception:
            logger.warning(
                "email_delivery_failed",
                template=message.template,
                error_type="EmailDeliveryError",
            )

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

    def _fail_verification(self, user_id: uuid.UUID, reason: str) -> None:
        self._record_event(
            AuditEventType.AUTH_EMAIL_VERIFICATION_FAILED,
            user_id=user_id,
            result=AuditResult.FAILURE,
            metadata={"reason": reason},
            durable=True,
        )

    def _fail_reset(self, user_id: uuid.UUID, reason: str) -> None:
        self._record_event(
            AuditEventType.AUTH_PASSWORD_RESET_FAILED,
            user_id=user_id,
            result=AuditResult.FAILURE,
            metadata={"reason": reason},
            durable=True,
        )

    def _record_event(
        self,
        event_type: AuditEventType,
        *,
        user_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        result: AuditResult = AuditResult.BLOCKED,
        metadata: dict[str, object] | None = None,
        severity: AuditSeverity = AuditSeverity.MEDIUM,
        durable: bool = False,
    ) -> None:
        payload = AuditRecordCreate(
            request_id="account",
            trace_id="account",
            event_type=event_type,
            resource_type="account",
            actor_type=ActorType.SYSTEM,
            user_id=user_id,
            organization_id=organization_id,
            result=result,
            severity=severity,
            metadata=metadata or {},
        )
        if durable:
            self._audit.record_durable_isolated(payload)
        else:
            self._audit.record(payload)

    @staticmethod
    def _ensure_aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @staticmethod
    def _invitation_view(row: OrganizationInvitationModel) -> OrganizationInvitationView:
        pending = (
            row.accepted_at is None
            and row.revoked_at is None
            and AccountService._ensure_aware(row.expires_at) > datetime.now(UTC)
        )
        return OrganizationInvitationView(
            id=row.id,
            organization_id=row.organization_id,
            email=row.email,
            role=row.role,
            invited_by_user_id=row.invited_by_user_id,
            expires_at=row.expires_at,
            accepted_at=row.accepted_at,
            revoked_at=row.revoked_at,
            created_at=row.created_at,
            is_pending=pending,
        )
