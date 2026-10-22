"""Live duration receipts reuse the existing organization voice budget and quotas."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from app.agent_voice.config import EmbeddingPrice, RoutePrice, VoiceUnavailableError
from app.core.config import Settings
from app.schemas.common import StrictModel

CLOSE_SECONDS = 10
CLEANUP_SECONDS = 4  # bounded authorization wait, guard tick and transport close


class LivePrice(StrictModel):
    model: Literal["gpt-live-1"] = "gpt-live-1"
    source: str = Field(pattern=r"^https://", max_length=500)
    valid_until: datetime
    usd_per_second: Decimal = Field(gt=0, allow_inf_nan=False)


class LiveLimits(StrictModel):
    organization_id: str = Field(min_length=1, max_length=36)
    budget_reference: str = Field(min_length=1, max_length=128)
    budget_as_of: datetime
    valid_until: datetime
    remaining_budget_usd: Decimal = Field(gt=0, allow_inf_nan=False)
    session_ceiling_usd: Decimal = Field(gt=0, le=1, allow_inf_nan=False)
    live: LivePrice
    reasoning: RoutePrice
    embedding: EmbeddingPrice | None = None
    maximum_seconds: int = Field(default=180, ge=10, le=300)
    maximum_audio_seconds: int = Field(default=180, ge=1, le=300)
    maximum_turns: int = Field(default=6, ge=1, le=12)

    @model_validator(mode="after")
    def valid_receipt(self) -> LiveLimits:
        for value in (
            self.budget_as_of,
            self.valid_until,
            self.live.valid_until,
            self.reasoning.valid_until,
            *([self.embedding.valid_until] if self.embedding else []),
        ):
            if value.tzinfo is None:
                raise ValueError("Receipt timestamps require an explicit timezone.")
        if self.maximum_audio_seconds > self.maximum_seconds:
            raise ValueError("Audio duration cannot exceed the session duration.")
        return self

    @property
    def voice_model(self) -> str:
        return self.live.model

    def reservation(self, settings: Settings) -> Decimal:
        # Live bills elapsed session seconds, including silence, independently of
        # the existing Agent's reply/capture attempts and optional embedding call.
        secondary = (
            self.maximum_turns
            * 4
            * (
                self.reasoning.maximum_input_tokens * self.reasoning.input_usd_per_million
                + settings.agent_max_output_tokens * self.reasoning.output_usd_per_million
            )
        )
        if self.embedding:
            secondary += (
                self.maximum_turns
                * self.embedding.maximum_input_tokens
                * self.embedding.input_usd_per_million
            )
        return (
            self.maximum_seconds + CLOSE_SECONDS + CLEANUP_SECONDS
        ) * self.live.usd_per_second + secondary / Decimal(1000000)

    def reserved_tokens(self, settings: Settings) -> int:
        # Live reports seconds, never invented voice tokens. Only token-based
        # secondary routes consume this hold against the existing monthly quota.
        return self.maximum_turns * 4 * (
            self.reasoning.maximum_input_tokens + settings.agent_max_output_tokens
        ) + (self.maximum_turns * self.embedding.maximum_input_tokens if self.embedding else 0)

    def receipt_deadline(self) -> datetime:
        return min(
            self.valid_until,
            self.live.valid_until,
            self.reasoning.valid_until,
            *([self.embedding.valid_until] if self.embedding else []),
        )


def load_live_limits(settings: Settings) -> LiveLimits:
    if not settings.agent_live_enabled:
        raise VoiceUnavailableError("gpt_live_not_enabled")
    if not settings.openai_api_key:
        raise VoiceUnavailableError("provider_credential_unbound")
    if settings.provider_mode != "live":
        raise VoiceUnavailableError("approved_live_route_unavailable")
    if not settings.agent_live_limits_file:
        raise VoiceUnavailableError("verified_live_limits_unbound")
    try:
        limits = LiveLimits.model_validate(
            json.loads(Path(settings.agent_live_limits_file).read_text())
        )
    except (OSError, ValueError) as exc:
        raise VoiceUnavailableError("verified_live_limits_invalid") from exc
    now = datetime.now(UTC)
    if limits.budget_as_of > now or limits.receipt_deadline() <= now:
        raise VoiceUnavailableError("verified_live_limits_expired")
    if limits.receipt_deadline() < now + timedelta(
        seconds=limits.maximum_seconds + CLOSE_SECONDS + CLEANUP_SECONDS
    ):
        raise VoiceUnavailableError("verified_live_receipt_cannot_cover_session")
    if limits.reasoning.model != settings.agent_reasoning_model:
        raise VoiceUnavailableError("verified_reasoning_route_mismatch")
    if limits.reservation(settings) > min(limits.session_ceiling_usd, limits.remaining_budget_usd):
        raise VoiceUnavailableError("verified_cap_cannot_cover_session")
    return limits
