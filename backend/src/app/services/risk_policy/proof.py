"""Decode the apply proof. It is a separate token, not the access token and not the body."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import jwt

from app.core.config import Settings
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.governance import (
    TELEGRAM_PROOF_MAX_AGE,
    HumanApplyProof,
    _require_fresh_step_up_times,
)


def decode_apply_proof(
    token: str,
    settings: Settings,
    *,
    proposal_id: UUID,
    now: datetime,
) -> HumanApplyProof:
    """Turn a step-up or Telegram proof token into a proof bound to this proposal.

    Expiry, Telegram age, and step-up times use ``now``. The library clock is
    not the authority. A step-up time later than ``now`` is rejected.
    """

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False, "verify_iat": False},
        )
    except jwt.PyJWTError as exc:
        raise RiskPolicyRejected("apply_proof_missing") from exc
    if now.tzinfo is None:
        raise RiskPolicyRejected("timestamp_must_be_aware")
    kind = payload.get("type")
    if not isinstance(kind, str) or kind not in {"risk_policy_step_up", "risk_policy_telegram"}:
        raise RiskPolicyRejected("apply_proof_missing")
    subject = _required_claim(payload.get("sub"), "apply_proof_missing")
    bound = _required_proposal_id(payload.get("proposal_id"), proposal_id)
    content_hash = _required_claim(payload.get("content_hash"), "apply_proof_missing")
    expires = _time(payload.get("exp"), "apply_proof_expired")
    if expires <= now:
        raise RiskPolicyRejected("apply_proof_expired")
    if kind == "risk_policy_step_up":
        authenticated_at = _time(payload.get("auth_time"), "apply_proof_not_fresh")
        step_up_at = _time(payload.get("step_up_at"), "apply_proof_not_fresh")
        _require_fresh_step_up_times(authenticated_at, step_up_at, now)
        return HumanApplyProof(
            method="interactive_step_up",
            subject_user_id=subject,
            proposal_id=bound,
            content_hash=content_hash,
            expires_at=expires,
            session_id=_required_claim(payload.get("session_id"), "apply_proof_not_fresh"),
            authenticated_at=authenticated_at,
            step_up_at=step_up_at,
        )
    return HumanApplyProof(
        method="telegram_owner",
        subject_user_id=subject,
        proposal_id=bound,
        content_hash=content_hash,
        expires_at=expires,
        issued_at=_fresh_issued_at(payload.get("iat"), now),
        telegram_chat_id=_required_claim(payload.get("chat_id"), "apply_proof_chat_mismatch"),
        telegram_confirmation_id=_required_claim(
            payload.get("confirmation_id"), "apply_proof_chat_mismatch"
        ),
    )


def _required_proposal_id(raw: object, proposal_id: UUID) -> str:
    """Use only the claim. A missing claim is not filled from the request."""

    bound = _required_claim(raw, "apply_proof_missing")
    if bound != str(proposal_id):
        raise RiskPolicyRejected("apply_proof_proposal_mismatch")
    return bound


def _required_claim(raw: object, code: str) -> str:
    """A binding claim is a non-empty string. Other types are not coerced."""

    if not isinstance(raw, str):
        raise RiskPolicyRejected(code)
    text = raw.strip()
    if not text:
        raise RiskPolicyRejected(code)
    return text


def _fresh_issued_at(raw: object, now: datetime) -> datetime:
    """Telegram freshness is five minutes from ``iat``, not the token ``exp``."""

    if raw is None:
        raise RiskPolicyRejected("apply_proof_not_fresh")
    issued = _time(raw, "apply_proof_not_fresh")
    if issued > now or now - issued > TELEGRAM_PROOF_MAX_AGE:
        raise RiskPolicyRejected("apply_proof_not_fresh")
    return issued


def _time(value: object, code: str) -> datetime:
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise RiskPolicyRejected(code)
    if isinstance(value, float) and not math.isfinite(value):
        raise RiskPolicyRejected(code)
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise RiskPolicyRejected(code) from exc
