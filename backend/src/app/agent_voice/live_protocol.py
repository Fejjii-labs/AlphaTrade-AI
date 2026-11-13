"""GPT Live primary WebSocket contract, verified against official SDK 3.28.0.

https://developers.openai.com/api/docs/guides/voice-websockets?api=live
https://developers.openai.com/api/docs/guides/live-delegation
"""

from typing import Any

from app.agent_voice.protocol import GROUNDED_FACTS, TRADE_REFUSAL
from app.core.prompt_registry import render_prompt

INSTRUCTIONS = render_prompt(
    "voice_live_instructions",
    grounded_facts=GROUNDED_FACTS.replace("an alpha_trade_turn result", "a backend result").replace(
        "call alpha_trade_turn", "delegate it"
    ),
    trade_refusal=TRADE_REFUSAL,
)


def session_start(history: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "session.start",
        "session": {
            "model": "gpt-live-1",
            "instructions": INSTRUCTIONS,
            "audio": {"format": {"type": "audio/pcm", "rate": 24000}, "output": {"voice": "marin"}},
            "delegation": {"type": "client"},
            "input": history,
            "store": False,
        },
    }


def context_parts(content: str) -> list[str]:
    # UTF-8 bytes bound text tokens conservatively. Each append is <=500 tokens;
    # preserve content verbatim across commands rather than lose limitations.
    parts, current = [], ""
    for character in content:
        if len((current + character).encode()) > 480:
            parts.append(current)
            current = ""
        current += character
    if current:
        parts.append(current)
    return parts
