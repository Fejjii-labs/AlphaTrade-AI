"""Who may change a risk profile, and when the change takes effect.

Only an authenticated human can apply. Agents and bot tokens may propose.
Tightening takes effect immediately. Loosening needs an explicit confirmation,
starts at the next UTC day, and is refused while a breaker is active.
Applied versions are immutable; this module only decides, it does not update
an existing version.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.services.risk_policy.clock import next_utc_day
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.profile import (
    ChangeClass,
    RiskPolicyProfile,
    classify_change,
    profile_diff,
    validate_profile,
)


class PolicyChannel(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    BOT = "bot"


@dataclass(frozen=True)
class PolicyActor:
    actor_id: str
    channel: PolicyChannel
    authenticated: bool


@dataclass(frozen=True)
class ApplyDecision:
    change_class: ChangeClass
    effective_at: datetime
    diff: dict[str, dict[str, str]]
    applies_to_new_trades_only: bool = True


def channel_from_access_payload(payload: dict[str, object]) -> PolicyChannel:
    """Map a decoded access token to a policy channel. Unknown actors fail closed."""

    if payload.get("type") != "access":
        raise RiskPolicyRejected("token_not_access")
    actor = payload.get("actor", "user")
    if actor == "user":
        return PolicyChannel.HUMAN
    if actor == "agent":
        return PolicyChannel.AGENT
    if actor == "bot":
        return PolicyChannel.BOT
    raise RiskPolicyRejected("actor_channel_unknown")


def assert_can_propose(actor: PolicyActor) -> None:
    if not actor.authenticated or not actor.actor_id:
        raise RiskPolicyRejected("actor_unauthenticated")
    if actor.channel not in {PolicyChannel.HUMAN, PolicyChannel.AGENT, PolicyChannel.BOT}:
        raise RiskPolicyRejected("actor_channel_unknown")


def assert_can_apply(actor: PolicyActor) -> None:
    assert_can_propose(actor)
    if actor.channel is not PolicyChannel.HUMAN:
        raise RiskPolicyRejected("apply_requires_human")


def decide_apply(
    *,
    actor: PolicyActor,
    previous: RiskPolicyProfile | None,
    proposed: RiskPolicyProfile,
    now: datetime,
    confirmed: bool,
    breaker_active: bool,
) -> ApplyDecision:
    """Decide the effect of an apply. Does not write.

    ``confirmed`` is the human's explicit confirmation. It is required only
    for a loosening. ``breaker_active`` must be the stored breaker state.
    """

    assert_can_apply(actor)
    validate_profile(proposed)
    if previous is not None and previous.kind is not proposed.kind:
        raise RiskPolicyRejected("kind_mismatch")
    change = classify_change(previous, proposed)
    if now.tzinfo is None:
        raise RiskPolicyRejected("timestamp_must_be_aware")
    if change is ChangeClass.LOOSENING:
        if breaker_active:
            raise RiskPolicyRejected("loosening_blocked_by_breaker")
        if not confirmed:
            raise RiskPolicyRejected("loosening_requires_confirmation")
        effective_at = next_utc_day(now)
    else:
        effective_at = now
    return ApplyDecision(
        change_class=change,
        effective_at=effective_at,
        diff=profile_diff(previous, proposed),
    )
