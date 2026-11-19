"""Account lifecycle schemas: verification, password reset, invitations."""

from __future__ import annotations

from pydantic import EmailStr, Field

from app.schemas.common import ORMModel, StrictModel


class MessageResponse(ORMModel):
    message: str


class VerifyEmailRequest(StrictModel):
    """Optional email for unauthenticated resend; authenticated users use profile email."""

    email: EmailStr | None = None


class VerifyEmailConfirmRequest(StrictModel):
    token: str = Field(min_length=20, max_length=512)


class PasswordResetRequest(StrictModel):
    email: EmailStr


class PasswordResetConfirmRequest(StrictModel):
    token: str = Field(min_length=20, max_length=512)
    new_password: str = Field(min_length=12, max_length=128)
