"""Two-factor authentication (TOTP) enrolment, verification and remembered devices.

Every code check (login second step, disable, regenerate recovery codes, and a
future step-up) goes through ``_verify``. It enforces the per-user lock
(``two_factor_max_failures`` failures within ``two_factor_failure_window_seconds``
lock code checks for ``two_factor_lockout_seconds``), refuses a replayed TOTP
step or a used recovery code, and audits the outcome. Codes, secrets and digests
are never logged or audited.

Step-up hook: a later "require a fresh code before a sensitive action" (for
example arming real trading) should call ``verify_code_for_action`` with a new
``TwoFactorAction`` member; it already applies the lock, replay guard and audit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import structlog
from fastapi import status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, AuthError, ConflictError
from app.db.models import User as UserModel
from app.db.two_factor import (
    TwoFactorRecoveryCodeRow,
    TwoFactorTrustedDeviceRow,
    UserTwoFactorRow,
)
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType, AuditResult, AuditSeverity
from app.schemas.two_factor import (
    TwoFactorRecoveryCodesResponse,
    TwoFactorSetupResponse,
    TwoFactorStatusResponse,
)
from app.security.tokens import create_two_factor_device_token, decode_two_factor_device_token
from app.security.two_factor import (
    TOTP_DIGITS,
    TOTP_PERIOD_SECONDS,
    TwoFactorSecretError,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_code,
    generate_totp_secret,
    hash_recovery_code,
    match_totp_step,
    otpauth_uri,
    qr_svg_data_uri,
)
from app.services.audit_service import AuditService

logger = structlog.get_logger(__name__)

RECOVERY_CODE_COUNT = 10


class TwoFactorAction(StrEnum):
    """Why a code is being checked. Add step-up actions here (e.g. ARM_REAL_TRADING)."""

    LOGIN = "login"
    CONFIRM_SETUP = "confirm_setup"
    DISABLE = "disable"
    REGENERATE_RECOVERY_CODES = "regenerate_recovery_codes"


class TwoFactorCodeError(AppError):
    """Wrong code on an authenticated action (not 401: the session itself is fine)."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "two_factor_code_invalid"


class TwoFactorLockedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "two_factor_locked"


@dataclass(frozen=True)
class _Actor:
    user_id: uuid.UUID
    organization_id: uuid.UUID | None


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class TwoFactorService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        audit_service: AuditService | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._audit = audit_service or AuditService(session)

    # ------------------------------------------------------------------ status

    def is_enabled(self, user_id: uuid.UUID) -> bool:
        row = self._session.get(UserTwoFactorRow, user_id)
        return row is not None and row.enabled_at is not None

    def status(self, user_id: uuid.UUID) -> TwoFactorStatusResponse:
        row = self._session.get(UserTwoFactorRow, user_id)
        if row is None:
            return TwoFactorStatusResponse(enabled=False)
        now = datetime.now(UTC)
        codes = self._session.scalar(
            select(func.count())
            .select_from(TwoFactorRecoveryCodeRow)
            .where(
                TwoFactorRecoveryCodeRow.user_id == user_id,
                TwoFactorRecoveryCodeRow.used_at.is_(None),
            )
        )
        devices = self._session.scalar(
            select(func.count())
            .select_from(TwoFactorTrustedDeviceRow)
            .where(
                TwoFactorTrustedDeviceRow.user_id == user_id,
                TwoFactorTrustedDeviceRow.revoked_at.is_(None),
                TwoFactorTrustedDeviceRow.expires_at > now,
            )
        )
        locked_until = _aware(row.locked_until)
        return TwoFactorStatusResponse(
            enabled=row.enabled_at is not None,
            enabled_at=_aware(row.enabled_at),
            pending_setup=row.enabled_at is None,
            recovery_codes_remaining=int(codes or 0) if row.enabled_at else 0,
            remembered_devices=int(devices or 0) if row.enabled_at else 0,
            locked_until=locked_until if locked_until and locked_until > now else None,
        )

    # --------------------------------------------------------------- enrolment

    def start_setup(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> TwoFactorSetupResponse:
        """Create (or replace) a pending secret. Refused while 2FA is on."""
        user = self._require_user(user_id)
        row = self._session.get(UserTwoFactorRow, user_id, with_for_update=True)
        if row is not None and row.enabled_at is not None:
            raise ConflictError("Two-factor authentication is already on.")
        secret = generate_totp_secret()
        ciphertext = encrypt_totp_secret(secret, user_id=user_id, settings=self._settings)
        if row is None:
            row = UserTwoFactorRow(user_id=user_id, secret_ciphertext=ciphertext)
            self._session.add(row)
        else:
            row.secret_ciphertext = ciphertext
            row.last_used_step = None
        self._session.flush()
        self._record(
            AuditEventType.AUTH_TWO_FACTOR_SETUP_STARTED,
            _Actor(user_id, organization_id),
            result=AuditResult.SUCCESS,
        )
        uri = otpauth_uri(
            secret_b32=secret, account_name=user.email, issuer=self._settings.two_factor_issuer
        )
        return TwoFactorSetupResponse(
            manual_key=secret,
            otpauth_uri=uri,
            qr_svg_data_uri=qr_svg_data_uri(uri),
            digits=TOTP_DIGITS,
            period_seconds=TOTP_PERIOD_SECONDS,
        )

    def confirm_setup(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID, code: str
    ) -> TwoFactorRecoveryCodesResponse:
        row = self._session.get(UserTwoFactorRow, user_id, with_for_update=True)
        if row is None or row.enabled_at is not None:
            raise ConflictError("Start two-factor setup first.")
        actor = _Actor(user_id, organization_id)
        self._verify(
            row, actor, code=code, recovery_code=None, action=TwoFactorAction.CONFIRM_SETUP
        )
        row.enabled_at = datetime.now(UTC)
        codes = self._replace_recovery_codes(user_id)
        self._session.flush()
        self._record(AuditEventType.AUTH_TWO_FACTOR_ENABLED, actor, result=AuditResult.SUCCESS)
        logger.info("two_factor_enabled", user_id=str(user_id))
        return TwoFactorRecoveryCodesResponse(recovery_codes=codes)

    def disable(self, *, user_id: uuid.UUID, organization_id: uuid.UUID, code: str) -> None:
        row = self._require_enabled(user_id)
        actor = _Actor(user_id, organization_id)
        self._verify(row, actor, code=code, recovery_code=None, action=TwoFactorAction.DISABLE)
        self._session.query(TwoFactorRecoveryCodeRow).filter(
            TwoFactorRecoveryCodeRow.user_id == user_id
        ).delete(synchronize_session=False)
        self._revoke_devices(user_id)
        self._session.delete(row)
        self._session.flush()
        self._record(
            AuditEventType.AUTH_TWO_FACTOR_DISABLED,
            actor,
            result=AuditResult.SUCCESS,
            severity=AuditSeverity.HIGH,
        )
        logger.info("two_factor_disabled", user_id=str(user_id))

    def regenerate_recovery_codes(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID, code: str
    ) -> TwoFactorRecoveryCodesResponse:
        row = self._require_enabled(user_id)
        actor = _Actor(user_id, organization_id)
        self._verify(
            row,
            actor,
            code=code,
            recovery_code=None,
            action=TwoFactorAction.REGENERATE_RECOVERY_CODES,
        )
        codes = self._replace_recovery_codes(user_id)
        self._session.flush()
        self._record(
            AuditEventType.AUTH_TWO_FACTOR_CODES_REGENERATED, actor, result=AuditResult.SUCCESS
        )
        return TwoFactorRecoveryCodesResponse(recovery_codes=codes)

    def revoke_devices(self, *, user_id: uuid.UUID, organization_id: uuid.UUID) -> int:
        revoked = self._revoke_devices(user_id)
        self._session.flush()
        self._record(
            AuditEventType.AUTH_TWO_FACTOR_DEVICES_REVOKED,
            _Actor(user_id, organization_id),
            result=AuditResult.SUCCESS,
            metadata={"revoked_devices": revoked},
        )
        return revoked

    # ------------------------------------------------------------------- login

    def verify_login(
        self,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        code: str | None,
        recovery_code: str | None,
    ) -> str:
        """Second login step. Returns the method used (``totp`` / ``recovery_code``)."""
        row = self._session.get(UserTwoFactorRow, user_id, with_for_update=True)
        if row is None or row.enabled_at is None:
            raise AuthError("Two-factor authentication is not on for this account.")
        return self._verify(
            row,
            _Actor(user_id, organization_id),
            code=code,
            recovery_code=recovery_code,
            action=TwoFactorAction.LOGIN,
        )

    def verify_code_for_action(
        self,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        code: str,
        action: TwoFactorAction,
    ) -> None:
        """Step-up hook: require a current TOTP code (no recovery codes) for ``action``."""
        row = self._require_enabled(user_id)
        self._verify(
            row, _Actor(user_id, organization_id), code=code, recovery_code=None, action=action
        )

    def remembered_device_valid(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID, device_token: str | None
    ) -> bool:
        """True when ``device_token`` is a live, unrevoked grant for this user."""
        if not device_token:
            return False
        try:
            token_user, device_id = decode_two_factor_device_token(device_token, self._settings)
        except AuthError:
            return False
        if token_user != user_id:
            return False
        device = self._session.get(TwoFactorTrustedDeviceRow, device_id)
        now = datetime.now(UTC)
        if (
            device is None
            or device.user_id != user_id
            or device.revoked_at is not None
            or (_aware(device.expires_at) or now) <= now
        ):
            return False
        device.last_used_at = now
        self._record(
            AuditEventType.AUTH_TWO_FACTOR_VERIFIED,
            _Actor(user_id, organization_id),
            result=AuditResult.SUCCESS,
            metadata={"action": TwoFactorAction.LOGIN.value, "method": "remembered_device"},
        )
        return True

    def remember_device(self, *, user_id: uuid.UUID) -> tuple[str, datetime]:
        expires_at = datetime.now(UTC) + timedelta(
            days=self._settings.two_factor_remember_device_days
        )
        device = TwoFactorTrustedDeviceRow(user_id=user_id, expires_at=expires_at)
        self._session.add(device)
        self._session.flush()
        token = create_two_factor_device_token(
            user_id=user_id, device_id=device.id, expires_at=expires_at, settings=self._settings
        )
        return token, expires_at

    # ---------------------------------------------------------------- internals

    def _require_user(self, user_id: uuid.UUID) -> UserModel:
        user = self._session.get(UserModel, user_id)
        if user is None:
            raise AuthError("Account is unavailable.")
        return user

    def _require_enabled(self, user_id: uuid.UUID) -> UserTwoFactorRow:
        row = self._session.get(UserTwoFactorRow, user_id, with_for_update=True)
        if row is None or row.enabled_at is None:
            raise ConflictError("Two-factor authentication is not on.")
        return row

    def _replace_recovery_codes(self, user_id: uuid.UUID) -> list[str]:
        self._session.query(TwoFactorRecoveryCodeRow).filter(
            TwoFactorRecoveryCodeRow.user_id == user_id
        ).delete(synchronize_session=False)
        codes = [generate_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
        for code in codes:
            self._session.add(
                TwoFactorRecoveryCodeRow(
                    user_id=user_id,
                    code_hash=hash_recovery_code(code, user_id=user_id, settings=self._settings),
                )
            )
        return codes

    def _revoke_devices(self, user_id: uuid.UUID) -> int:
        result = self._session.execute(
            update(TwoFactorTrustedDeviceRow)
            .where(
                TwoFactorTrustedDeviceRow.user_id == user_id,
                TwoFactorTrustedDeviceRow.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        return int(getattr(result, "rowcount", 0) or 0)

    def _verify(
        self,
        row: UserTwoFactorRow,
        actor: _Actor,
        *,
        code: str | None,
        recovery_code: str | None,
        action: TwoFactorAction,
    ) -> str:
        now = datetime.now(UTC)
        locked_until = _aware(row.locked_until)
        if locked_until is not None and locked_until > now:
            self._record(
                AuditEventType.AUTH_TWO_FACTOR_LOCKED,
                actor,
                result=AuditResult.BLOCKED,
                severity=AuditSeverity.HIGH,
                metadata={"action": action.value, "reason": "locked"},
            )
            raise TwoFactorLockedError(
                "Too many wrong codes. Try again later.",
                details={"retry_after_seconds": int((locked_until - now).total_seconds()) + 1},
            )
        method = "totp" if code is not None else "recovery_code"
        accepted = (
            self._accept_totp(row, actor.user_id, code)
            if code is not None
            else self._accept_recovery_code(actor.user_id, recovery_code or "", now)
        )
        if not accepted:
            self._register_failure(row, actor, action=action, method=method, now=now)
        row.failed_attempts = 0
        row.failure_window_started_at = None
        row.locked_until = None
        self._record(
            AuditEventType.AUTH_TWO_FACTOR_VERIFIED,
            actor,
            result=AuditResult.SUCCESS,
            metadata={"action": action.value, "method": method},
        )
        return method

    def _accept_totp(self, row: UserTwoFactorRow, user_id: uuid.UUID, code: str) -> bool:
        try:
            secret = decrypt_totp_secret(
                row.secret_ciphertext, user_id=user_id, settings=self._settings
            )
        except TwoFactorSecretError:
            logger.error("two_factor_secret_unreadable", user_id=str(user_id))
            return False
        step = match_totp_step(secret, code)
        if step is None or (row.last_used_step is not None and step <= row.last_used_step):
            return False
        row.last_used_step = step
        return True

    def _accept_recovery_code(self, user_id: uuid.UUID, recovery_code: str, now: datetime) -> bool:
        digest = hash_recovery_code(recovery_code, user_id=user_id, settings=self._settings)
        match = self._session.scalars(
            select(TwoFactorRecoveryCodeRow)
            .where(
                TwoFactorRecoveryCodeRow.user_id == user_id,
                TwoFactorRecoveryCodeRow.code_hash == digest,
                TwoFactorRecoveryCodeRow.used_at.is_(None),
            )
            .with_for_update()
        ).first()
        if match is None:
            return False
        match.used_at = now
        return True

    def _register_failure(
        self,
        row: UserTwoFactorRow,
        actor: _Actor,
        *,
        action: TwoFactorAction,
        method: str,
        now: datetime,
    ) -> None:
        """Count the failure durably (commit), maybe lock, audit, then raise."""
        window = timedelta(seconds=self._settings.two_factor_failure_window_seconds)
        started = _aware(row.failure_window_started_at)
        if started is None or now - started > window:
            row.failed_attempts = 0
            row.failure_window_started_at = now
        row.failed_attempts += 1
        locked = row.failed_attempts >= self._settings.two_factor_max_failures
        if locked:
            row.locked_until = now + timedelta(seconds=self._settings.two_factor_lockout_seconds)
            row.failed_attempts = 0
            row.failure_window_started_at = None
        attempts = row.failed_attempts
        # Persist the counter even though the request fails. The caller has
        # made no other change yet: every _verify runs before any mutation.
        self._session.commit()
        self._record(
            AuditEventType.AUTH_TWO_FACTOR_FAILED,
            actor,
            result=AuditResult.FAILURE,
            severity=AuditSeverity.MEDIUM,
            metadata={"action": action.value, "method": method, "locked": locked},
        )
        if locked:
            self._record(
                AuditEventType.AUTH_TWO_FACTOR_LOCKED,
                actor,
                result=AuditResult.BLOCKED,
                severity=AuditSeverity.HIGH,
                metadata={
                    "action": action.value,
                    "lockout_seconds": self._settings.two_factor_lockout_seconds,
                },
            )
            logger.warning("two_factor_locked", user_id=str(actor.user_id), action=action.value)
            raise TwoFactorLockedError("Too many wrong codes. Try again later.")
        logger.info(
            "two_factor_failed", user_id=str(actor.user_id), action=action.value, attempts=attempts
        )
        if action is TwoFactorAction.LOGIN:
            raise AuthError("Invalid authentication code.")
        raise TwoFactorCodeError("Invalid authentication code.")

    def _record(
        self,
        event_type: AuditEventType,
        actor: _Actor,
        *,
        result: AuditResult,
        severity: AuditSeverity = AuditSeverity.INFO,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Successes commit with the request; failures and locks commit on their own."""
        record = (
            self._audit.record
            if result is AuditResult.SUCCESS
            else self._audit.record_durable_isolated
        )
        record(
            AuditRecordCreate(
                request_id="auth",
                trace_id="auth",
                event_type=event_type,
                resource_type="auth_two_factor",
                resource_id=str(actor.user_id),
                actor_type=ActorType.USER,
                user_id=actor.user_id,
                organization_id=actor.organization_id,
                result=result,
                severity=severity,
                metadata=metadata or {},
            )
        )
