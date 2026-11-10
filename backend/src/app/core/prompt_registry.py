"""Versioned system prompts stored as files under ``backend/prompts``.

Every prompt file starts with a small header, then a ``---`` line, then the
prompt body. The body is used verbatim (no trailing-newline normalization):

    # prompt-id: interactive_agent_system
    # version: 1
    ---
    You are ...

``prompt-id`` must equal the file stem. Bump ``version`` whenever the body (or a
fragment composed into it) changes; model-call telemetry records the id, the
version and the SHA-256 of the exact rendered text sent to the provider.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
_HEADER_END = "\n---\n"


class PromptFormatError(ValueError):
    """A prompt file is missing its header or the header is inconsistent."""


def prompt_sha256(text: str) -> str:
    """SHA-256 hex digest of the exact UTF-8 prompt text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PromptRecord:
    id: str
    version: int
    text: str
    sha256: str


def parse_prompt(name: str, raw: str) -> PromptRecord:
    """Split the header from the body. The body is returned byte-for-byte."""
    header, separator, body = raw.partition(_HEADER_END)
    if not separator or not header.startswith("# prompt-id:"):
        raise PromptFormatError(f"Prompt {name!r} has no '# prompt-id' header.")
    fields: dict[str, str] = {}
    for line in header.split("\n"):
        key, colon, value = line.removeprefix("# ").partition(":")
        if not line.startswith("# ") or not colon:
            raise PromptFormatError(f"Prompt {name!r} has a malformed header line.")
        fields[key.strip()] = value.strip()
    if fields.get("prompt-id") != name:
        raise PromptFormatError(f"Prompt {name!r} header id does not match its file name.")
    version = fields.get("version", "")
    if not version.isdigit() or int(version) < 1:
        raise PromptFormatError(f"Prompt {name!r} needs a positive integer '# version'.")
    return PromptRecord(id=name, version=int(version), text=body, sha256=prompt_sha256(body))


@cache
def load_prompt_record(name: str) -> PromptRecord:
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt not found: {path}")
    # newline="" keeps the bytes as stored; no universal-newline translation.
    with path.open(encoding="utf-8", newline="") as handle:
        return parse_prompt(name, handle.read())


def render_prompt(name: str, **values: str) -> str:
    """Substitute each ``{{key}}`` placeholder, which must occur exactly once."""
    text = load_prompt_record(name).text
    for key, value in values.items():
        placeholder = "{{" + key + "}}"
        if text.count(placeholder) != 1:
            raise PromptFormatError(f"Prompt {name!r} must contain {placeholder} exactly once.")
        text = text.replace(placeholder, value)
    return text
