"""Versioned prompt files: header parsing and byte-identical move of inline prompts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.core.prompt_registry import (
    PROMPTS_DIR,
    PromptFormatError,
    load_prompt_record,
    parse_prompt,
    render_prompt,
)

LEGACY = Path(__file__).parent / "fixtures" / "prompts_legacy"


def _legacy(name: str) -> str:
    # Captured from the inline constants / prompt files before the move.
    return (LEGACY / f"{name}.v1.txt").read_bytes().decode("utf-8")


def _rendered() -> dict[str, str]:
    from app.agent_capture.model import SYSTEM
    from app.agent_voice import live_protocol, protocol
    from app.interactive_agent.conversation import _SYSTEM
    from app.services.narrative_service import load_prompt

    return {
        "interactive_agent_system": _SYSTEM,
        "agent_capture_system": SYSTEM,
        "voice_trade_refusal": protocol.TRADE_REFUSAL,
        "voice_grounded_facts": protocol.GROUNDED_FACTS,
        "voice_realtime_instructions": protocol.INSTRUCTIONS,
        "voice_live_instructions": live_protocol.INSTRUCTIONS,
        "journal_review_coach": load_prompt("journal_review_coach"),
        "risk_explanation": load_prompt("risk_explanation"),
        "trading_analysis_narrative": load_prompt("trading_analysis_narrative"),
    }


@pytest.mark.parametrize("name", sorted(p.name.removesuffix(".v1.txt") for p in LEGACY.iterdir()))
def test_rendered_prompt_is_byte_identical_to_legacy_while_version_is_1(name: str) -> None:
    version = load_prompt_record(name).version
    if version > 1:
        pytest.skip(f"{name} was deliberately bumped to v{version}; v1 snapshot no longer applies")
    assert _rendered()[name].encode("utf-8") == _legacy(name).encode("utf-8")


def test_every_prompt_file_has_a_valid_header() -> None:
    names = sorted(path.stem for path in PROMPTS_DIR.glob("*.txt"))
    assert names == sorted(p.name.removesuffix(".v1.txt") for p in LEGACY.iterdir())
    for name in names:
        record = load_prompt_record(name)
        assert record.id == name
        assert record.version >= 1
        assert not record.text.startswith("# prompt-id")
        assert record.sha256 == hashlib.sha256(record.text.encode("utf-8")).hexdigest()


def test_header_is_stripped_and_body_kept_verbatim() -> None:
    record = parse_prompt("demo", "# prompt-id: demo\n# version: 3\n---\nLine one\n\n")
    assert (record.id, record.version, record.text) == ("demo", 3, "Line one\n\n")


@pytest.mark.parametrize(
    "raw",
    [
        "No header at all",
        "# prompt-id: demo\n# version: 1\nbody without separator",
        "# prompt-id: other\n# version: 1\n---\nbody",
        "# prompt-id: demo\n# version: zero\n---\nbody",
        "# prompt-id: demo\n# version: 0\n---\nbody",
        "# prompt-id: demo\nversion 1\n---\nbody",
    ],
)
def test_malformed_headers_are_rejected(raw: str) -> None:
    with pytest.raises(PromptFormatError):
        parse_prompt("demo", raw)


def test_render_requires_each_placeholder_exactly_once() -> None:
    with pytest.raises(PromptFormatError):
        render_prompt("voice_realtime_instructions", missing="x")
