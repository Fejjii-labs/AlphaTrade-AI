"""Authenticated start of a paper Telegram enrollment challenge.

The one-time token is returned to the caller and is not logged. Completion
happens when the Telegram process polls a private message. This route does
not send Telegram HTTP, mint a Candidate, or place an order.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.core.config import Environment
from app.core.dependencies import SettingsDep
from app.core.errors import ConflictError
from app.db.session import get_session_factory
from app.persistence.composition import build_postgres_telegram_security_store
from app.security.rate_limit import tenant_rate_limit_dependency
from app.security.rbac import TraderDep
from app.telegram_activation.policy import PAPER_ACTIVATION_BACKOFF
from app.telegram_activation.runtime import _NoSend
from app.telegram_security.clock import SystemClock
from app.telegram_security.protocol import TelegramSecurityProtocol

router = APIRouter(prefix="/telegram-paper", tags=["telegram-paper"])

# The protocol's own limiter is rebuilt per request; this shared one holds across
# requests and instances (L16).
_ENROLLMENT_START_LIMIT = Depends(
    tenant_rate_limit_dependency(
        "telegram:enrollment:start", limit=10, window_seconds=600, ip_limit=30, user_limit=10
    )
)


class EnrollmentStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: UUID
    expires_at: datetime
    token: str
    bot_id: str


@router.post(
    "/enrollment/start",
    response_model=EnrollmentStartResponse,
    dependencies=[_ENROLLMENT_START_LIMIT],
)
def start_telegram_enrollment(
    tenant: TraderDep,
    settings: SettingsDep,
) -> EnrollmentStartResponse:
    """Issue one enrollment token for an OWNER or TRADER. Does not log the token."""

    if settings.environment is Environment.PRODUCTION and not settings.production_demo_active:
        raise ConflictError(
            "Telegram enrollment is not available in production.",
            code="telegram_enrollment_disabled",
        )
    if settings.enable_real_trading or settings.real_trading_enabled:
        raise ConflictError(
            "Telegram enrollment requires paper execution.",
            code="telegram_enrollment_disabled",
        )
    if settings.execution_mode.value != "paper":
        raise ConflictError(
            "Telegram enrollment requires paper execution.",
            code="telegram_enrollment_disabled",
        )
    bot_id = settings.telegram_bot_id.strip()
    if not bot_id:
        raise ConflictError(
            "Telegram bot is not configured.",
            code="telegram_bot_unconfigured",
        )
    from app.telegram_activation.identity import bot_identity_mismatch

    if bot_identity_mismatch(bot_id=bot_id, token=settings.telegram_bot_token):
        raise ConflictError(
            "Telegram bot id does not match the bot token.",
            code="telegram_bot_identity_mismatch",
        )
    protocol = TelegramSecurityProtocol(
        store=build_postgres_telegram_security_store(get_session_factory()),
        transport=_NoSend(),
        clock=SystemClock(),
        enabled=True,
        retry_backoff=PAPER_ACTIVATION_BACKOFF,
    )
    started = protocol.start_enrollment(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        bot_id=bot_id,
    )
    return EnrollmentStartResponse(
        challenge_id=started.challenge.challenge_id,
        expires_at=started.challenge.expires_at,
        token=started.token,
        bot_id=bot_id,
    )
