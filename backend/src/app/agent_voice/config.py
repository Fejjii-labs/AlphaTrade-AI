"""Private verified receipts, not a second budget or guessed model-price catalog."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import Field, ValidationError, model_validator

from app.core.config import Settings
from app.schemas.common import StrictModel


class VoiceUnavailableError(RuntimeError):
    """Safe machine reason only; no provider body, credentials or prompt."""


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


class MinuteRealtimePrice(StrictModel):
    """Operator-verified per-minute voice receipt. Not a token price catalog.

    xAI Grok Voice bills session duration at ``usd_per_minute`` and each
    billable ``conversation.item.create`` at ``usd_per_text_input``.
    ``function_call_output`` and audio items are not text inputs.
    """

    model: str = Field(min_length=1, max_length=80)
    source: str = Field(pattern=r"^https://", max_length=500)
    valid_until: datetime
    usd_per_minute: Decimal = Field(gt=0, allow_inf_nan=False)
    usd_per_text_input: Decimal = Field(gt=0, allow_inf_nan=False)


# Pinned Grok Voice release. The floating ``grok-voice-latest`` alias is refused.
GROK_VOICE_MODEL = "grok-voice-3.0"
OPENAI_REALTIME_ORIGIN = "https://api.openai.com/v1"
XAI_REALTIME_ORIGIN = "https://api.x.ai/v1"


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
    realtime: RealtimePrice | MinuteRealtimePrice
    reasoning: RoutePrice
    embedding: EmbeddingPrice | None = None
    # Twelve minutes at $0.08/min is $0.96 and fits the $1 session ceiling.
    # The browser timer still stops at five minutes; this bound is the server hold.
    maximum_seconds: int = Field(default=180, ge=10, le=900)
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

    def context_token_limit(self) -> int | None:
        """Token context cap. Per-minute sessions are bounded by the clock instead."""
        if isinstance(self.realtime, MinuteRealtimePrice):
            return None
        return self.realtime.maximum_input_tokens

    def audio_context_tokens(self, nbytes: int) -> int:
        """Conservative audio-token debit. Zero when the meter is session minutes."""
        price = self.realtime
        if isinstance(price, MinuteRealtimePrice):
            return 0
        return (
            int(Decimal(nbytes) / Decimal(48000) * price.maximum_audio_input_tokens_per_second) + 1
        )

    def reservation(self, settings: Settings) -> Decimal:
        """Reserve full repeated context, audio without replies and two retries/routes.

        Cached input is priced at the full input rate. No cached-price assumptions,
        tokenizer estimates, successful-call refund or unknown-usage refund is used.
        A per-minute receipt reserves the whole configured session, rounded up to
        the next minute, plus no text inputs (tool results are not text inputs).
        """
        realtime = self.realtime
        if isinstance(realtime, MinuteRealtimePrice):
            return minute_session_cost(realtime, self.maximum_seconds, 0)
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

    def receipt_deadline(self) -> datetime:
        return min(
            self.valid_until,
            self.realtime.valid_until,
            self.reasoning.valid_until,
            *([self.embedding.valid_until] if self.embedding else []),
        )


def billed_session_minutes(seconds: int, *, minimum: int = 0) -> int:
    """Whole minutes. Partial minutes round up so the hold cannot under-bill."""
    if seconds <= 0:
        return minimum
    return max(minimum, (seconds + 59) // 60)


def minute_session_cost(
    price: MinuteRealtimePrice, seconds: int, text_inputs: int, *, minimum_minutes: int = 0
) -> Decimal:
    """Session-minute cost plus billable text inputs. Exempt items stay at zero."""
    if text_inputs < 0:
        raise ValueError("text_inputs")
    return (
        Decimal(billed_session_minutes(seconds, minimum=minimum_minutes)) * price.usd_per_minute
        + Decimal(text_inputs) * price.usd_per_text_input
    )


def realtime_endpoint(settings: Settings) -> tuple[str, str, str]:
    """Return provider, key, and the one allowlisted HTTPS origin.

    The key may be empty here. ``load_limits`` rejects an empty key before a
    socket is opened. Any origin other than the two pinned hosts fails closed.
    """
    provider = settings.agent_realtime_provider
    if provider == "openai":
        origin = settings.openai_base_url.rstrip("/")
        if origin != OPENAI_REALTIME_ORIGIN:
            raise VoiceUnavailableError("approved_openai_origin_unavailable")
        return provider, settings.openai_api_key, OPENAI_REALTIME_ORIGIN
    if provider == "xai":
        origin = settings.xai_realtime_base_url.rstrip("/")
        if origin != XAI_REALTIME_ORIGIN:
            raise VoiceUnavailableError("approved_xai_origin_unavailable")
        return provider, settings.xai_api_key, XAI_REALTIME_ORIGIN
    raise VoiceUnavailableError("approved_realtime_provider_unavailable")


def load_limits(settings: Settings) -> VoiceLimits:
    if not getattr(settings, "agent_realtime_enabled", False):
        raise VoiceUnavailableError("realtime_not_enabled")
    provider, key, _origin = realtime_endpoint(settings)
    if not key:
        raise VoiceUnavailableError("provider_credential_unbound")
    if settings.provider_mode != "live":
        raise VoiceUnavailableError("approved_live_route_unavailable")
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
    if limits.reasoning.model != settings.agent_reasoning_model:
        raise VoiceUnavailableError("verified_reasoning_route_mismatch")
    if provider == "xai":
        if (
            not isinstance(limits.realtime, MinuteRealtimePrice)
            or limits.realtime.model != GROK_VOICE_MODEL
        ):
            raise VoiceUnavailableError("verified_realtime_route_mismatch")
    elif not isinstance(limits.realtime, RealtimePrice):
        raise VoiceUnavailableError("verified_realtime_route_mismatch")
    if limits.reservation(settings) > min(limits.session_ceiling_usd, limits.remaining_budget_usd):
        raise VoiceUnavailableError("verified_cap_cannot_cover_session")
    return limits
