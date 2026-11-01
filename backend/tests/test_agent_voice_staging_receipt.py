"""The committed staging voice receipt must load and fit its own ceilings."""

from __future__ import annotations

from pathlib import Path

from app.agent_voice import config as voice_config
from app.core.config import Settings

RECEIPT = (
    Path(__file__).resolve().parents[1]
    / "src/app/agent_voice/receipts/staging_voice_limits_2026-11.json"
)


def _settings() -> Settings:
    return Settings(
        environment="local",
        agent_realtime_enabled=True,
        agent_realtime_limits_file=str(RECEIPT),
        openai_api_key="test-key",
        provider_mode="live",
        agent_voice_reasoning_model="gpt-6-luna",
    )


def test_staging_receipt_fits_session_ceiling_and_budget() -> None:
    settings = _settings()
    limits = voice_config.VoiceLimits.model_validate_json(RECEIPT.read_text())
    bounded = voice_config.apply_turn_setting(limits, settings)
    assert str(limits.remaining_budget_usd) == "10"
    assert bounded.maximum_turns == 4
    assert bounded.reservation(settings) <= limits.session_ceiling_usd
    assert limits.reasoning.model == "gpt-6-luna"
    assert limits.embedding is not None
    assert limits.embedding.model == settings.embeddings_model


def test_staging_receipt_refuses_mismatched_reasoning_model() -> None:
    settings = _settings().model_copy(update={"agent_voice_reasoning_model": "gpt-6.1-sol"})
    try:
        voice_config.load_limits(settings)
    except voice_config.VoiceUnavailableError as exc:
        assert str(exc) == "verified_reasoning_route_mismatch"
    else:  # pragma: no cover
        raise AssertionError("mismatched reasoning model must be refused")
