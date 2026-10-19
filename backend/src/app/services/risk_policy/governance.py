"""Who may change a risk profile, and when the change takes effect.

Agents, bots, and service tokens may not apply. A missing actor claim is not
a human. Apply requires a human-bound proof for the configured owner user id:
a fresh interactive session with step-up or re-auth, or a confirmation from
the pinned Telegram owner chat. The confirmation is that proof, not a boolean
on the apply body.

Tightening takes effect immediately. Loosening starts at the later of the next
UTC midnight and 24 hours after apply, and is refused while a breaker is
active. A proposal older than 72 hours cannot be applied. Applied versions
are immutable; this module only decides, it does not update an existing version.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.services.risk_policy.clock import loosening_effective_at
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.profile import (
    ChangeClass,
    RiskPolicyProfile,
    classify_change,
    profile_diff,
    validate_profile,
)

PROPOSAL_TTL = timedelta(hours=72)
FRESH_PROOF = timedelta(minutes=15)
TELEGRAM_PROOF_MAX_AGE = timedelta(minutes=5)


class PolicyChannel(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    BOT = "bot"
    SERVICE = "service"
    UNDECLARED = "undeclared"


@dataclass(frozen=True)
class PolicyActor:
    actor_id: str
    channel: PolicyChannel
    authenticated: bool


@dataclass(frozen=True)
class HumanApplyProof:
    """Proof that is not the apply request body.

    ``interactive_step_up`` needs a session plus a step-up or re-auth time.
    ``telegram_owner`` needs the pinned owner chat and a confirmation id.
    Both are bound to one proposal hash.
    """

    method: str
    subject_user_id: str
    proposal_id: str
    content_hash: str
    expires_at: datetime
    issued_at: datetime | None = None
    session_id: str | None = None
    authenticated_at: datetime | None = None
    step_up_at: datetime | None = None
    telegram_chat_id: str | None = None
    telegram_confirmation_id: str | None = None


@dataclass(frozen=True)
class ApplyDecision:
    change_class: ChangeClass
    effective_at: datetime
    diff: dict[str, dict[str, str]]
    applies_to_new_trades_only: bool = True


def channel_from_access_payload(payload: dict[str, object]) -> PolicyChannel:
    """Map a decoded access token to a policy channel.

    A missing ``actor`` claim is undeclared, not human. Unknown actors fail closed.
    """

    if payload.get("type") != "access":
        raise RiskPolicyRejected("token_not_access")
    actor = payload.get("actor")
    if actor is None or actor == "":
        return PolicyChannel.UNDECLARED
    if actor == "user":
        return PolicyChannel.HUMAN
    if actor == "agent":
        return PolicyChannel.AGENT
    if actor == "bot":
        return PolicyChannel.BOT
    if actor == "service":
        return PolicyChannel.SERVICE
    raise RiskPolicyRejected("actor_channel_unknown")


def assert_can_propose(actor: PolicyActor) -> None:
    if not actor.authenticated or not actor.actor_id:
        raise RiskPolicyRejected("actor_unauthenticated")
    if actor.channel is PolicyChannel.SERVICE:
        raise RiskPolicyRejected("service_token_refused")
    if actor.channel is PolicyChannel.UNDECLARED:
        raise RiskPolicyRejected("actor_undeclared")
    if actor.channel not in {
        PolicyChannel.HUMAN,
        PolicyChannel.AGENT,
        PolicyChannel.BOT,
    }:
        raise RiskPolicyRejected("actor_channel_unknown")


def assert_can_apply(
    actor: PolicyActor,
    proof: HumanApplyProof | None,
    *,
    owner_user_id: str,
    pinned_telegram_chat_id: str,
    proposal_id: str,
    content_hash: str,
    now: datetime,
) -> None:
    """Apply is the configured owner plus a fresh proof. Roles are not enough."""

    if actor.channel is PolicyChannel.AGENT:
        raise RiskPolicyRejected("apply_refuses_agent_token")
    if actor.channel is PolicyChannel.SERVICE:
        raise RiskPolicyRejected("apply_refuses_service_token")
    if actor.channel is PolicyChannel.BOT:
        raise RiskPolicyRejected("apply_refuses_bot_token")
    if not actor.authenticated or not actor.actor_id:
        raise RiskPolicyRejected("apply_requires_human_proof")
    if actor.channel not in {PolicyChannel.HUMAN, PolicyChannel.UNDECLARED}:
        raise RiskPolicyRejected("apply_requires_human_proof")
    if not owner_user_id or actor.actor_id != owner_user_id:
        raise RiskPolicyRejected("apply_owner_mismatch")
    if proof is None:
        raise RiskPolicyRejected("apply_proof_missing")
    if now.tzinfo is None or proof.expires_at.tzinfo is None:
        raise RiskPolicyRejected("timestamp_must_be_aware")
    if proof.subject_user_id != owner_user_id:
        raise RiskPolicyRejected("apply_owner_mismatch")
    if proof.expires_at <= now:
        raise RiskPolicyRejected("apply_proof_expired")
    if proof.proposal_id != proposal_id or proof.content_hash != content_hash:
        raise RiskPolicyRejected("apply_proof_proposal_mismatch")
    if proof.method == "interactive_step_up":
        _require_fresh_step_up(proof, now)
        return
    if proof.method == "telegram_owner":
        if not pinned_telegram_chat_id or proof.telegram_chat_id != pinned_telegram_chat_id:
            raise RiskPolicyRejected("apply_proof_chat_mismatch")
        if not proof.telegram_confirmation_id:
            raise RiskPolicyRejected("apply_proof_chat_mismatch")
        _require_fresh_telegram(proof, now)
        return
    raise RiskPolicyRejected("apply_proof_missing")


def proposal_is_expired(created_at: datetime, now: datetime) -> bool:
    if created_at.tzinfo is None or now.tzinfo is None:
        raise RiskPolicyRejected("timestamp_must_be_aware")
    return now - created_at > PROPOSAL_TTL


def decide_apply(
    *,
    actor: PolicyActor,
    previous: RiskPolicyProfile | None,
    proposed: RiskPolicyProfile,
    now: datetime,
    breaker_active: bool,
    first_store: bool = False,
) -> ApplyDecision:
    """Decide the effect of an apply that has already passed the human proof.

    The first stored profile is classified against the live placeholder.
    A looser result is a loosening: it waits for the cooldown and still
    needs the human proof. An unchanged placeholder stays an initial apply.
    """

    if actor.channel is PolicyChannel.AGENT:
        raise RiskPolicyRejected("apply_refuses_agent_token")
    if actor.channel is PolicyChannel.SERVICE:
        raise RiskPolicyRejected("apply_refuses_service_token")
    if actor.channel is PolicyChannel.BOT:
        raise RiskPolicyRejected("apply_refuses_bot_token")
    validate_profile(proposed)
    if previous is not None and previous.kind is not proposed.kind:
        raise RiskPolicyRejected("kind_mismatch")
    change = classify_change(
        previous,
        proposed,
        unchanged=ChangeClass.INITIAL if first_store else None,
    )
    if now.tzinfo is None:
        raise RiskPolicyRejected("timestamp_must_be_aware")
    if change is ChangeClass.LOOSENING:
        if breaker_active:
            raise RiskPolicyRejected("loosening_blocked_by_breaker")
        effective_at = loosening_effective_at(now)
    else:
        effective_at = now
    return ApplyDecision(
        change_class=change,
        effective_at=effective_at,
        diff=profile_diff(previous, proposed),
    )


def _require_fresh_step_up(proof: HumanApplyProof, now: datetime) -> None:
    if not proof.session_id or proof.authenticated_at is None or proof.step_up_at is None:
        raise RiskPolicyRejected("apply_proof_not_fresh")
    if proof.authenticated_at.tzinfo is None or proof.step_up_at.tzinfo is None:
        raise RiskPolicyRejected("timestamp_must_be_aware")
    if proof.step_up_at < proof.authenticated_at:
        raise RiskPolicyRejected("apply_proof_not_fresh")
    if now - proof.authenticated_at > FRESH_PROOF or now - proof.step_up_at > FRESH_PROOF:
        raise RiskPolicyRejected("apply_proof_not_fresh")


def _require_fresh_telegram(proof: HumanApplyProof, now: datetime) -> None:
    """Cap a Telegram proof from its issued time, whatever ``exp`` says.

    A proof issued in the future, or older than five minutes, is rejected.
    """

    issued = proof.issued_at
    if issued is None:
        raise RiskPolicyRejected("apply_proof_not_fresh")
    if now.tzinfo is None or issued.tzinfo is None:
        raise RiskPolicyRejected("timestamp_must_be_aware")
    if issued > now or now - issued > TELEGRAM_PROOF_MAX_AGE:
        raise RiskPolicyRejected("apply_proof_not_fresh")
