"""Private verified receipts, not a second budget or guessed model-price catalog."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import Field, ValidationError, model_validator

from app.core.config import Settings
from app.schemas.common import StrictModel


class VoiceUnavailableError(RuntimeError):
    """Safe machine reason only; no provider body, credentials or prompt."""


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
    if settings.provider_mode.strip().lower() == "mock":
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
    maximum_turns: int = Field(default=6, ge=1, le=12)
    maximum_responses: int = Field(default=12, ge=2, le=24)
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
            self.maximum_responses * response
            + orphan_audio
            + self.maximum_turns * 4 * reasoning
            + embedding
        ) / Decimal(1000000)

    @property
    def voice_model(self) -> str:
        return self.realtime.model

    def reserved_tokens(self, settings: Settings) -> int:
        return math.ceil(
            self.maximum_responses
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
    if limits.reservation(settings) > min(limits.session_ceiling_usd, limits.remaining_budget_usd):
        raise VoiceUnavailableError("verified_cap_cannot_cover_session")
    return limits
