"""Two-factor authentication request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.auth import AuthResponse, TokenPair
from app.schemas.common import ORMModel, StrictModel

TwoFactorMethod = Literal["totp", "recovery_code"]


def _all_methods() -> list[TwoFactorMethod]:
    return ["totp", "recovery_code"]


class TwoFactorChallengeResponse(ORMModel):
    """Password accepted; a second factor is required to finish signing in."""

    two_factor_required: Literal[True] = True
    pending_token: str = Field(description="Short-lived token for POST /auth/login/two-factor.")
    expires_in: int = Field(gt=0, description="Pending-token lifetime in seconds.")
    methods: list[TwoFactorMethod] = Field(default_factory=_all_methods)


class TwoFactorLoginRequest(StrictModel):
    pending_token: str = Field(min_length=20, max_length=2048)
    code: str | None = Field(default=None, pattern=r"^\s*\d{3}\s?\d{3}\s*$")
    recovery_code: str | None = Field(default=None, min_length=8, max_length=32)
    remember_device: bool = False

    @model_validator(mode="after")
    def _exactly_one_factor(self) -> TwoFactorLoginRequest:
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("Send exactly one of code or recovery_code.")
        return self


class TwoFactorLoginResponse(AuthResponse):
    """Session tokens, plus a remember-device token when one was requested."""

    device_token: str | None = Field(
        default=None,
        description="Send as device_token on later logins to skip the code on this device.",
    )
    device_token_expires_at: datetime | None = None


class TwoFactorStatusResponse(ORMModel):
    enabled: bool
    enrolment_available: bool = Field(
        default=True,
        description="False when the server refuses new enrolment (no encryption key set).",
    )
    enabled_at: datetime | None = None
    pending_setup: bool = False
    recovery_codes_remaining: int = 0
    remembered_devices: int = 0
    locked_until: datetime | None = None


class TwoFactorSetupResponse(ORMModel):
    """Shown once. The manual key and QR carry the TOTP secret."""

    manual_key: str
    otpauth_uri: str
    qr_svg_data_uri: str
    digits: int
    period_seconds: int


class TwoFactorCodeRequest(StrictModel):
    code: str = Field(pattern=r"^\s*\d{3}\s?\d{3}\s*$", description="6-digit authenticator code.")


class TwoFactorRecoveryCodesResponse(ORMModel):
    """Shown once. Only digests are stored."""

    recovery_codes: list[str]


_PASSWORD = Field(min_length=1, max_length=1024, description="The account's current password.")
_CODE = Field(default=None, pattern=r"^\s*\d{3}\s?\d{3}\s*$")


class TwoFactorSetupRequest(StrictModel):
    """Starting setup reveals a secret, so it needs the password, not only a session."""

    password: str = _PASSWORD


class TwoFactorDisableRequest(StrictModel):
    """Password plus a current authenticator code, or one recovery code (lost phone)."""

    password: str = _PASSWORD
    code: str | None = _CODE
    recovery_code: str | None = Field(default=None, min_length=8, max_length=32)

    @model_validator(mode="after")
    def _exactly_one_factor(self) -> TwoFactorDisableRequest:
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("Send exactly one of code or recovery_code.")
        return self


class TwoFactorConfirmResponse(TwoFactorRecoveryCodesResponse):
    """2FA is on. Every other session was signed out; ``tokens`` replaces this one."""

    tokens: TokenPair
    revoked_sessions: int = Field(ge=0)
