"""The committed staging voice receipt must load and fit its own ceilings."""

from __future__ import annotations

import contextlib
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


def _receipt(**changes: object) -> dict:
    import json

    data = json.loads(RECEIPT.read_text())
    for key, value in changes.items():
        if "." in key:
            outer, inner = key.split(".", 1)
            data[outer][inner] = value
        else:
            data[key] = value
    return data


def _load(tmp_path: Path, data: dict) -> str:
    import json

    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(data))
    settings = _settings().model_copy(update={"agent_realtime_limits_file": str(path)})
    try:
        voice_config.load_limits(settings)
    except voice_config.VoiceUnavailableError as exc:
        return str(exc)
    return "loaded"


def test_staging_receipt_loads_end_to_end(tmp_path: Path) -> None:
    assert _load(tmp_path, _receipt()) == "loaded"


def test_expired_receipt_is_refused(tmp_path: Path) -> None:
    # Kills V1: dropping the deadline check must fail this test.
    past = "2026-01-01T00:00:00+00:00"
    early = {"budget_as_of": "2025-12-01T00:00:00+00:00"}
    assert _load(tmp_path, _receipt(valid_until=past, **early)) == (
        "verified_realtime_limits_expired"
    )
    for route in ("realtime", "reasoning", "embedding"):
        assert _load(tmp_path, _receipt(**{f"{route}.valid_until": past})) == (
            "verified_realtime_limits_expired"
        ), route


def test_session_ceiling_above_one_dollar_is_refused(tmp_path: Path) -> None:
    # Kills V5: loosening the $1 session ceiling must fail this test.
    for ceiling in ("1.01", "5", "100"):
        assert _load(tmp_path, _receipt(session_ceiling_usd=ceiling)) == (
            "verified_realtime_limits_invalid"
        ), ceiling
    assert _load(tmp_path, _receipt(session_ceiling_usd="1")) == "loaded"


def test_realtime_model_must_match_published_prices(tmp_path: Path) -> None:
    # A pricier model carrying mini prices is refused, not reserved at mini rates.
    assert _load(tmp_path, _receipt(**{"realtime.model": "gpt-realtime-2.1"})) == (
        "verified_realtime_model_unknown"
    )
    assert _load(tmp_path, _receipt(**{"realtime.audio_input_usd_per_million": "9.99"})) == (
        "verified_realtime_price_below_published"
    )
    assert _load(tmp_path, _receipt(**{"realtime.output_usd_per_million": "2.00"})) == (
        "verified_realtime_price_below_published"
    )
    assert _load(tmp_path, _receipt(**{"realtime.model": "gpt-realtime-2.1-mini"})) == "loaded"


def test_voice_reasoning_route_pins_standard_service_tier() -> None:
    from unittest.mock import patch

    from app.agent_voice import service
    from app.providers.llm import LLMCompletionRequest, LLMMessage, OpenAILLMProvider

    settings = _settings()
    shared = OpenAILLMProvider(api_key="test-key", base_url="https://x.invalid", model="m")
    voice = service.voice_llm(shared, settings)
    assert service.VOICE_SERVICE_TIER == "default"
    seen: list[dict] = []

    def capture(_self: object, _url: str, body: dict, **_kw: object) -> dict:
        seen.append(body)
        raise RuntimeError("stop")

    def request(model: str) -> LLMCompletionRequest:
        return LLMCompletionRequest(
            model=model, messages=[LLMMessage(role="user", content="hi")], max_tokens=64
        )

    with patch.object(OpenAILLMProvider, "_post_json", capture):
        for model in ("gpt-6-luna", "gpt-4o-mini"):  # Responses and Chat Completions paths
            with contextlib.suppress(Exception):
                voice.complete(request(model))
    assert len(seen) == 2, "voice reasoning must reach both provider paths"
    assert {"input" in body for body in seen} == {True, False}
    assert all(body.get("service_tier") == "default" for body in seen)
    # The shared (non-voice) provider keeps the account default.
    seen.clear()
    with patch.object(OpenAILLMProvider, "_post_json", capture), contextlib.suppress(Exception):
        shared.complete(request("gpt-6-luna"))
    assert all("service_tier" not in body for body in seen)
