"""Decode the apply proof. It is a separate token, not the access token and not the body."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import jwt

from app.core.config import Settings
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.governance import HumanApplyProof


def decode_apply_proof(
    token: str,
    settings: Settings,
    *,
    proposal_id: UUID,
    now: datetime,
) -> HumanApplyProof:
    """Turn a step-up or Telegram proof token into a proof bound to this proposal."""

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise RiskPolicyRejected("apply_proof_missing") from exc
    kind = payload.get("type")
    subject = str(payload.get("sub") or "")
    bound = str(payload.get("proposal_id") or "")
    content_hash = str(payload.get("content_hash") or "")
    expires = _time(payload.get("exp"), "apply_proof_expired")
    if expires <= now:
        raise RiskPolicyRejected("apply_proof_expired")
    if kind == "risk_policy_step_up":
        return HumanApplyProof(
            method="interactive_step_up",
            subject_user_id=subject,
            proposal_id=bound or str(proposal_id),
            content_hash=content_hash,
            expires_at=expires,
            session_id=_text(payload.get("session_id")),
            authenticated_at=_time(payload.get("auth_time"), "apply_proof_not_fresh"),
            step_up_at=_time(payload.get("step_up_at"), "apply_proof_not_fresh"),
        )
    if kind == "risk_policy_telegram":
        return HumanApplyProof(
            method="telegram_owner",
            subject_user_id=subject,
            proposal_id=bound or str(proposal_id),
            content_hash=content_hash,
            expires_at=expires,
            telegram_chat_id=_text(payload.get("chat_id")),
            telegram_confirmation_id=_text(payload.get("confirmation_id")),
        )
    raise RiskPolicyRejected("apply_proof_missing")


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _time(value: object, code: str) -> datetime:
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=UTC)
    raise RiskPolicyRejected(code)
