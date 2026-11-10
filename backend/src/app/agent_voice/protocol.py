"""Pinned OpenAI SDK3.28 realtime signatures; no provider-supplied execution tool.

Hands-free: server VAD creates and interrupts responses itself, and the model may answer
simple turns directly (``tool_choice: auto``). The only tool is the read/propose Agent
delegate; no voice-callable tool can execute, confirm or cancel a trade.

Official source: openai/types/realtime/{session_update_event_param,
realtime_session_create_request_param,realtime_audio_formats,
realtime_audio_input_turn_detection_param,response_create_event_param,
conversation_item_create_event_param,conversation_item_truncate_event_param}.py.
Guides: https://developers.openai.com/api/docs/guides/realtime-websocket
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from app.agent_voice.config import VoiceLimits, VoiceUnavailableError
from app.core.prompt_registry import load_prompt_record, render_prompt

TOOL_NAME = "alpha_trade_turn"
# Prompt text lives in backend/prompts (versioned); the exported values are unchanged.
TRADE_REFUSAL = load_prompt_record("voice_trade_refusal").text
GROUNDED_FACTS = load_prompt_record("voice_grounded_facts").text
INSTRUCTIONS = render_prompt(
    "voice_realtime_instructions", grounded_facts=GROUNDED_FACTS, trade_refusal=TRADE_REFUSAL
)
TOOL = {
    "type": "function",
    "name": TOOL_NAME,
    "description": "Run the authenticated shared Agent turn; read scoped facts or propose changes.",
    "parameters": {
        "type": "object",
        "properties": {"message": {"type": "string", "minLength": 1, "maxLength": 8000}},
        "required": ["message"],
        "additionalProperties": False,
    },
}


def turn_detection(*, create_response: bool) -> dict[str, Any]:
    return {
        "type": "server_vad",
        "create_response": create_response,
        "interrupt_response": True,
        "silence_duration_ms": 500,
    }


def auto_responses_off() -> dict[str, Any]:
    """Stop server VAD from creating responses; barge-in still interrupts playback."""
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "audio": {"input": {"turn_detection": turn_detection(create_response=False)}},
        },
    }


def session_update(limits: VoiceLimits) -> dict[str, Any]:
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": limits.realtime.model,
            "instructions": INSTRUCTIONS,
            "output_modalities": ["audio"],
            "max_output_tokens": limits.maximum_output_tokens,
            "tools": [TOOL],
            "tool_choice": "auto",
            # No separately billed transcription route is activated.
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "turn_detection": turn_detection(create_response=True),
                },
                "output": {"format": {"type": "audio/pcm", "rate": 24000}, "voice": "marin"},
            },
            "truncation": "disabled",
        },
    }


def create_response(limits: VoiceLimits, *, grounded: bool = False) -> dict[str, Any]:
    return {
        "type": "response.create",
        "response": {
            "output_modalities": ["audio"],
            "max_output_tokens": limits.maximum_output_tokens,
            "tool_choice": "none" if grounded else "auto",
            **(
                {
                    "instructions": "Speak the tool's canonical reply faithfully; "
                    "do not add facts or approvals."
                }
                if grounded
                else {}
            ),
        },
    }


def checked_audio(encoded: str, remaining_bytes: int, *, maximum_bytes: int = 16384) -> bytes:
    try:
        audio = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VoiceUnavailableError("invalid_pcm_audio") from exc
    if not audio or len(audio) % 2 or len(audio) > min(maximum_bytes, remaining_bytes):
        raise VoiceUnavailableError("audio_limit_or_format")
    return audio
