"""`.env.example` model ids must mirror the `Settings` defaults (single source of truth)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings

_ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def _env_example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


@pytest.mark.parametrize(
    "env_key",
    [
        "LLM_MODEL",
        "LLM_TIER_A_MODEL",
        "LLM_TIER_B_MODEL",
        "AGENT_REASONING_MODEL",
        "EMBEDDINGS_MODEL",
    ],
)
def test_env_example_model_matches_settings_default(env_key: str) -> None:
    default = Settings.model_fields[env_key.lower()].default
    assert _env_example_values()[env_key] == default
