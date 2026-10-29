"""Private verified receipts, not a second budget or guessed model-price catalog."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field, ValidationError, model_validator

from app.core.config import Settings
from app.schemas.common import StrictModel


class VoiceUnavailableError(RuntimeError):
    """Safe machine reason only; no provider body, credentials or prompt."""


VOICE_PROVIDER_MODES = frozenset({"live", "fallback"})

# Receipt default before AGENT_VOICE_MAXIMUM_TURNS; a hands-free session that cannot
# reserve at least this many turns fails closed exactly as it did before.
LEGACY_DEFAULT_TURNS = 6
# Hard upper bound for delegated voice turns, whatever the setting or receipt says.
MAXIMUM_VOICE_TURNS = 24
# Server VAD creates responses itself, so one may already be in flight when the cap is
# reached (e.g. a barge-in while a delegated turn runs). That response is recorded and
# may be billed; the reservation always covers it, so it can never breach the ceiling.
RESPONSE_SLACK = 1
# Hands-free keeps the microphone open, so submitted audio tracks wall-clock time. A
# receipt that relies on the audio default gets up to its session length, if that fits.
MAXIMUM_HANDS_FREE_AUDIO_SECONDS = 180


def voice_route_refusal(settings: Settings) -> str | None:
    """Voice-scoped route gate (UI overhaul B7a).

    Voice does not follow the global ``provider_mode``: it needs a bound key and
    refuses an explicit mock mode. Every voice turn still requires its own flag
    and a verified, unexpired limits receipt (checked by the callers), and the
    voice reasoning and embedding routes are rebuilt fail-closed, so a mock or
    fallback provider can never produce a voice reply.
    """
    if not settings.openai_api_key.strip():
        return "provider_credential_unbound"
    if settings.provider_mode.strip().lower() not in VOICE_PROVIDER_MODES:
        return "approved_live_route_unavailable"
    return None


class RoutePrice(StrictModel):
    model: str = Field(min_length=1, max_length=80)
    source: str = Field(pattern=r"^https://", max_length=500)
    valid_until: datetime
    maximum_input_tokens: int = Field(gt=0, le=1000000)
    input_usd_per_million: Decimal = Field(gt=0, allow_inf_nan=False)
    output_usd_per_million: Decimal = Field(gt=0, allow_inf_nan=False)


class RealtimePrice(RoutePrice):
    audio_input_usd_per_million: Decimal = Field(gt=0, allow_inf_nan=False)
    audio_output_usd_per_million: Decimal = Field(gt=0, allow_inf_nan=False)
    # Approved upper bound for all submitted audio, including audio for which no
    # response.done arrives. A provider receipt must establish this bound.
    maximum_audio_input_tokens_per_second: Decimal = Field(gt=0, allow_inf_nan=False)


class EmbeddingPrice(StrictModel):
    """Optional operator-verified receipt for the existing embedding route."""

    model: str = Field(min_length=1, max_length=80)
    source: str = Field(pattern=r"^https://", max_length=500)
    valid_until: datetime
    maximum_input_tokens: int = Field(gt=0, le=1000000)
    input_usd_per_million: Decimal = Field(gt=0, allow_inf_nan=False)


class VoiceLimits(StrictModel):
    organization_id: str = Field(min_length=1, max_length=36)
    budget_reference: str = Field(min_length=1, max_length=128)
    budget_as_of: datetime
    valid_until: datetime
    remaining_budget_usd: Decimal = Field(gt=0, allow_inf_nan=False)
    session_ceiling_usd: Decimal = Field(gt=0, le=1, allow_inf_nan=False)
    realtime: RealtimePrice
    reasoning: RoutePrice
    embedding: EmbeddingPrice | None = None
    maximum_seconds: int = Field(default=180, ge=10, le=300)
    maximum_audio_seconds: int = Field(default=90, ge=1, le=180)
    maximum_turns: int = Field(default=LEGACY_DEFAULT_TURNS, ge=1, le=MAXIMUM_VOICE_TURNS)
    # Every provider response counts, including server-VAD auto responses and
    # interrupted ones; a delegated turn needs two (tool call + spoken reply).
    maximum_responses: int = Field(default=12, ge=2, le=2 * MAXIMUM_VOICE_TURNS)
    maximum_output_tokens: int = Field(default=1024, ge=64, le=4096)

    @model_validator(mode="after")
    def valid_receipt(self) -> VoiceLimits:
        for value in (
            self.budget_as_of,
            self.valid_until,
            self.realtime.valid_until,
            self.reasoning.valid_until,
            *([self.embedding.valid_until] if self.embedding else []),
        ):
            if value.tzinfo is None:
                raise ValueError("Receipt timestamps require an explicit timezone.")
        if self.maximum_responses < 2 * self.maximum_turns:
            raise ValueError("Each turn requires grounded delegation and spoken response.")
        return self

    def reservation(self, settings: Settings) -> Decimal:
        """Reserve full repeated context, audio without replies and two retries/routes.

        Cached input is priced at the full input rate. No cached-price assumptions,
        tokenizer estimates, successful-call refund or unknown-usage refund is used.
        """
        realtime = self.realtime
        response = realtime.maximum_input_tokens * max(
            realtime.input_usd_per_million, realtime.audio_input_usd_per_million
        ) + self.maximum_output_tokens * max(
            realtime.output_usd_per_million, realtime.audio_output_usd_per_million
        )
        orphan_audio = (
            self.maximum_audio_seconds
            * realtime.maximum_audio_input_tokens_per_second
            * realtime.audio_input_usd_per_million
        )
        reasoning = (
            self.reasoning.maximum_input_tokens * self.reasoning.input_usd_per_million
            + settings.agent_max_output_tokens * self.reasoning.output_usd_per_million
        )
        # Reply + auto capture, at most two router attempts each, for every turn.
        embedding = (
            self.maximum_turns
            * self.embedding.maximum_input_tokens
            * self.embedding.input_usd_per_million
            if self.embedding
            else Decimal(0)
        )
        return (
            (self.maximum_responses + RESPONSE_SLACK) * response
            + orphan_audio
            + self.maximum_turns * 4 * reasoning
            + embedding
        ) / Decimal(1000000)

    @property
    def voice_model(self) -> str:
        return self.realtime.model

    def reserved_tokens(self, settings: Settings) -> int:
        return math.ceil(
            (self.maximum_responses + RESPONSE_SLACK)
            * (self.realtime.maximum_input_tokens + self.maximum_output_tokens)
            + self.maximum_audio_seconds * self.realtime.maximum_audio_input_tokens_per_second
            + self.maximum_turns
            * 4
            * (self.reasoning.maximum_input_tokens + settings.agent_max_output_tokens)
            + (self.maximum_turns * self.embedding.maximum_input_tokens if self.embedding else 0)
        )

    def receipt_deadline(self) -> datetime:
        return min(
            self.valid_until,
            self.realtime.valid_until,
            self.reasoning.valid_until,
            *([self.embedding.valid_until] if self.embedding else []),
        )


class _TurnLimits(Protocol):
    maximum_turns: int
    session_ceiling_usd: Decimal
    remaining_budget_usd: Decimal

    @property
    def model_fields_set(self) -> set[str]: ...

    def model_dump(self) -> dict[str, Any]: ...

    def reservation(self, settings: Settings) -> Decimal: ...


def apply_turn_setting[T: _TurnLimits](limits: T, settings: Settings) -> T:
    """Effective ``maximum_turns`` from ``AGENT_VOICE_MAXIMUM_TURNS``.

    An explicit receipt value (and an explicit ``maximum_responses``) stays an upper
    bound. A receipt that relies on the default gets the largest value between the
    legacy default and the setting whose whole reservation fits the verified ceiling.
    A realtime receipt that relies on the audio default first tries submitted audio up to
    its session length (continuous mic); if no turn count fits that, the receipt value
    stays. Nothing that was refused before is admitted: the floor is the legacy default and
    the caller still runs its unchanged ceiling check.
    """
    explicit = "maximum_turns" in limits.model_fields_set
    requested = min(settings.agent_voice_maximum_turns, MAXIMUM_VOICE_TURNS)
    if explicit:
        requested = min(requested, limits.maximum_turns)
    paired = isinstance(limits, VoiceLimits)
    responses_explicit = False
    if isinstance(limits, VoiceLimits) and "maximum_responses" in limits.model_fields_set:
        responses_explicit = True
        requested = min(requested, limits.maximum_responses // 2)
    floor = requested if explicit else min(requested, limits.maximum_turns)
    ceiling = min(limits.session_ceiling_usd, limits.remaining_budget_usd)

    def bounded(turns: int, **extra: int) -> T:
        update: dict[str, Any] = {"maximum_turns": turns, **extra}
        if paired and not responses_explicit:
            update["maximum_responses"] = 2 * turns
        return type(limits).model_validate(limits.model_dump() | update)  # type: ignore[attr-defined,no-any-return]

    if isinstance(limits, VoiceLimits) and "maximum_audio_seconds" not in limits.model_fields_set:
        audio = min(limits.maximum_seconds, MAXIMUM_HANDS_FREE_AUDIO_SECONDS)
        if audio > limits.maximum_audio_seconds:
            for turns in range(requested, floor - 1, -1):
                candidate = bounded(turns, maximum_audio_seconds=audio)
                if candidate.reservation(settings) <= ceiling:
                    return candidate
    for turns in range(requested, floor, -1):
        candidate = bounded(turns)
        if candidate.reservation(settings) <= ceiling:
            return candidate
    return bounded(floor)


def load_limits(settings: Settings) -> VoiceLimits:
    if not getattr(settings, "agent_realtime_enabled", False):
        raise VoiceUnavailableError("realtime_not_enabled")
    if refusal := voice_route_refusal(settings):
        raise VoiceUnavailableError(refusal)
    filename = getattr(settings, "agent_realtime_limits_file", "")
    if not filename:
        raise VoiceUnavailableError("verified_realtime_limits_unbound")
    try:
        limits = VoiceLimits.model_validate(json.loads(Path(filename).read_text()))
    except (OSError, ValueError, ValidationError) as exc:
        raise VoiceUnavailableError("verified_realtime_limits_invalid") from exc
    now = datetime.now(UTC)
    if limits.budget_as_of > now or limits.receipt_deadline() <= now:
        raise VoiceUnavailableError("verified_realtime_limits_expired")
    if limits.reasoning.model != settings.agent_voice_reasoning_model:
        raise VoiceUnavailableError("verified_reasoning_route_mismatch")
    if limits.embedding and limits.embedding.model != settings.embeddings_model:
        raise VoiceUnavailableError("verified_embedding_route_mismatch")
    try:
        limits = apply_turn_setting(limits, settings)
    except ValidationError as exc:
        raise VoiceUnavailableError("verified_realtime_limits_invalid") from exc
    if limits.reservation(settings) > min(limits.session_ceiling_usd, limits.remaining_budget_usd):
        raise VoiceUnavailableError("verified_cap_cannot_cover_session")
    return limits
