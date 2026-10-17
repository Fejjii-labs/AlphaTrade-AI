"""Application voice boundary. Provider control and credentials are never client input."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import StrictModel


class VoiceContext(StrictModel):
    symbol: str | None = Field(default=None, max_length=30)
    strategy_id: UUID | None = None


class VoiceStart(StrictModel):
    type: Literal["session.start"]
    access_token: str = Field(min_length=1, max_length=8192, repr=False)
    conversation_id: UUID
    context: VoiceContext = Field(default_factory=VoiceContext)


class VoiceConfig(StrictModel):
    available: bool
    reason: str | None = None
    transport: Literal["websocket_pcm24"] = "websocket_pcm24"
    sample_rate: Literal[24000] = 24000
    maximum_seconds: int | None = None
    maximum_turns: int | None = None


class VoiceToolInput(StrictModel):
    message: str = Field(min_length=1, max_length=8000)


class VoiceAudio(StrictModel):
    type: Literal["audio.append"]
    audio: str = Field(min_length=1, max_length=32768)


class VoicePlaybackPosition(StrictModel):
    item_id: str | None = Field(default=None, max_length=128)
    audio_end_ms: int = Field(default=0, ge=0, le=300000)


class VoiceInterrupt(VoicePlaybackPosition):
    type: Literal["response.interrupt"]


class VoiceCommit(StrictModel):
    type: Literal["audio.commit"]


class VoiceTruncate(VoicePlaybackPosition):
    type: Literal["playback.truncate"]


class VoiceEnd(StrictModel):
    type: Literal["session.end"]
