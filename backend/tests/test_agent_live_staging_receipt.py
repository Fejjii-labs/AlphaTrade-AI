"""The committed staging GPT Live receipt must load with gpt-6-luna and fit its ceiling."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from app.agent_voice import live_config
from app.agent_voice.live_config import LiveLimits, VoiceUnavailableError, load_live_limits
from app.core.config import Settings

RECEIPT = (
    Path(__file__).resolve().parents[1]
    / "src/app/agent_voice/receipts/staging_live_limits_2026-11.json"
)
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def _settings(**changes: object) -> Settings:
    base = {
        "_env_file": None,
        "environment": "local",
        "agent_live_enabled": True,
        "agent_live_limits_file": str(RECEIPT),
        "openai_api_key": "test-key",
        "provider_mode": "live",
        "agent_voice_reasoning_model": "gpt-6-luna",
    }
    base.update(changes)
    return Settings(**base)


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return NOW


def _load(settings: Settings) -> LiveLimits:
    with patch.object(live_config, "datetime", _Clock):
        return load_live_limits(settings)


@pytest.mark.parametrize("output_tokens", [4000, 25000, 65536])
def test_live_receipt_loads_and_fits_ceiling(output_tokens: int) -> None:
    settings = _settings(agent_max_output_tokens=output_tokens)
    limits = _load(settings)
    assert limits.reasoning.model == "gpt-6-luna"
    assert limits.reasoning.input_usd_per_million == Decimal("0.10")
    assert limits.reasoning.output_usd_per_million == Decimal("0.50")
    assert limits.live.model == "gpt-live-1"
    assert limits.remaining_budget_usd == Decimal("5")
    assert limits.reservation(settings) <= limits.session_ceiling_usd
    assert limits.maximum_session_seconds(settings) >= limits.maximum_seconds


def test_live_receipt_refuses_sol_route() -> None:
    with pytest.raises(VoiceUnavailableError, match="verified_reasoning_route_mismatch"):
        _load(_settings(agent_voice_reasoning_model="gpt-6.1-sol"))


def test_live_receipt_has_no_secrets() -> None:
    text = RECEIPT.read_text().lower()
    data = json.loads(text)
    assert "sk-" not in text and "key" not in " ".join(data)
