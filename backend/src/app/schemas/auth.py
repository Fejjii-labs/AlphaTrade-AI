"""User, organization, membership, and authentication schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field

from app.schemas.common import (
    MembershipRole,
    ORMModel,
    RiskProfile,
    StrictModel,
    UserRole,
)


class Organization(ORMModel):
    """A tenant that owns trading resources."""

    id: UUID
    name: str
    created_at: datetime


class User(ORMModel):
    """An authenticated principal. Secrets are never included here."""

    id: UUID
    email: EmailStr
    role: UserRole = UserRole.TRADER
    risk_profile: RiskProfile = RiskProfile.MODERATE
    timezone: str = "UTC"
    is_active: bool = True
    email_verified: bool = False
    created_at: datetime


class Membership(ORMModel):
    """Association of a user to an organization with a role."""

    id: UUID
    user_id: UUID
    organization_id: UUID
    role: MembershipRole = MembershipRole.TRADER
    created_at: datetime


class RegisterRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    organization_name: str = Field(min_length=2, max_length=120)


class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(StrictModel):
    refresh_token: str | None = Field(default=None, min_length=20, max_length=512)


class LogoutRequest(StrictModel):
    refresh_token: str | None = Field(default=None, min_length=20, max_length=512)


class MeResponse(ORMModel):
    user: User
    organization: Organization
    membership_role: MembershipRole | None = None


class TokenPair(ORMModel):
    """Short-lived access token plus rotating refresh token."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(gt=0, description="Access-token lifetime in seconds.")


class AuthResponse(ORMModel):
    user: User
    organization: Organization
    membership_role: MembershipRole | None = None
    tokens: TokenPair
