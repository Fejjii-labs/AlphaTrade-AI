"""JWT access token helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import JWT_ALGORITHM, Environment, Settings, jwt_secret_is_weak
from app.core.errors import AuthError

_ACCESS_ACTORS = frozenset({"user", "agent", "bot", "service"})


def _signing_secret(settings: Settings) -> str:
    """Return the signing key, refusing weak/placeholder keys outside local.

    Settings validation already refuses these for the API; this keeps any
    process that was allowed to boot with the local default (a disarmed
    worker) from ever issuing or accepting a token signed with it.
    """
    if settings.environment is not Environment.LOCAL and jwt_secret_is_weak(settings.jwt_secret):
        raise AuthError("Access tokens are unavailable: signing key is not configured.")
    return settings.jwt_secret


def create_access_token(
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    email: str,
    settings: Settings,
    actor: str = "user",
) -> tuple[str, int]:
    """Issue an access token.

    Human login tokens are stamped ``actor=user``. Agent, bot, and service
    callers pass their own actor. A missing actor is not a valid issuance.
    """

    if actor not in _ACCESS_ACTORS:
        raise ValueError("access token actor must be user, agent, bot, or service")
    expires_in = settings.access_token_expire_minutes * 60
    jti = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "org_id": str(organization_id),
        "email": email,
        "type": "access",
        "actor": actor,
        "jti": jti,
        "exp": datetime.now(UTC) + timedelta(seconds=expires_in),
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(payload, _signing_secret(settings), algorithm=JWT_ALGORITHM)
    return token, expires_in


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Decode an access token.

    ``exp`` and ``iat`` are required, so a missing lifetime cannot be treated
    as never-expiring and a future ``iat`` is rejected by verification.
    ``sub`` and ``org_id`` are required strings. A present ``actor`` must be
    one of the issued actors; an omitted actor is left for the caller.
    """

    secret = _signing_secret(settings)
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub", "org_id", "type"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid or expired access token.") from exc
    if payload.get("type") != "access":
        raise AuthError("Invalid access token type.")
    subject = payload.get("sub")
    organization = payload.get("org_id")
    if not isinstance(subject, str) or not subject.strip():
        raise AuthError("Invalid or expired access token.")
    if not isinstance(organization, str) or not organization.strip():
        raise AuthError("Invalid or expired access token.")
    actor = payload.get("actor", None)
    if "actor" in payload and (not isinstance(actor, str) or actor not in _ACCESS_ACTORS):
        raise AuthError("Invalid access token type.")
    return payload


_TWO_FACTOR_PENDING = "two_factor_pending"
_TWO_FACTOR_DEVICE = "two_factor_device"


def _encode_purpose_token(
    *, subject: uuid.UUID, purpose: str, ttl: timedelta, settings: Settings, extra: dict[str, Any]
) -> tuple[str, str]:
    jti = str(uuid.uuid4())
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": purpose,
        "jti": jti,
        "iat": now,
        "exp": now + ttl,
        **extra,
    }
    return jwt.encode(payload, _signing_secret(settings), algorithm=JWT_ALGORITHM), jti


def _decode_purpose_token(token: str, *, purpose: str, settings: Settings) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            _signing_secret(settings),
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub", "type", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid or expired two-factor token.") from exc
    if payload.get("type") != purpose:
        raise AuthError("Invalid two-factor token type.")
    try:
        uuid.UUID(str(payload["sub"]))
    except ValueError as exc:
        raise AuthError("Invalid or expired two-factor token.") from exc
    return payload


def create_two_factor_pending_token(
    *, user_id: uuid.UUID, organization_id: uuid.UUID, settings: Settings
) -> tuple[str, int]:
    """Token proving the password step passed. It is not an access token.

    ``decode_access_token`` refuses it (its type is not ``access``), so it can
    only be redeemed at the second login step, once (its jti is denylisted).
    """
    ttl_seconds = settings.two_factor_pending_token_seconds
    token, _ = _encode_purpose_token(
        subject=user_id,
        purpose=_TWO_FACTOR_PENDING,
        ttl=timedelta(seconds=ttl_seconds),
        settings=settings,
        extra={"org_id": str(organization_id)},
    )
    return token, ttl_seconds


def decode_two_factor_pending_token(token: str, settings: Settings) -> dict[str, Any]:
    payload = _decode_purpose_token(token, purpose=_TWO_FACTOR_PENDING, settings=settings)
    try:
        uuid.UUID(str(payload.get("org_id")))
    except ValueError as exc:
        raise AuthError("Invalid or expired two-factor token.") from exc
    return payload


def create_two_factor_device_token(
    *, user_id: uuid.UUID, device_id: uuid.UUID, expires_at: datetime, settings: Settings
) -> str:
    """Signed remember-device token naming a revocable ``two_factor_trusted_devices`` row."""
    token, _ = _encode_purpose_token(
        subject=user_id,
        purpose=_TWO_FACTOR_DEVICE,
        ttl=expires_at - datetime.now(UTC),
        settings=settings,
        extra={"did": str(device_id)},
    )
    return token


def decode_two_factor_device_token(token: str, settings: Settings) -> tuple[uuid.UUID, uuid.UUID]:
    """Return ``(user_id, device_id)`` from a valid remember-device token."""
    payload = _decode_purpose_token(token, purpose=_TWO_FACTOR_DEVICE, settings=settings)
    try:
        return uuid.UUID(str(payload["sub"])), uuid.UUID(str(payload.get("did")))
    except ValueError as exc:
        raise AuthError("Invalid or expired two-factor token.") from exc
