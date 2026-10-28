"""Voice-scoped admission gate (UI overhaul B7a, Griller condition K2).

Voice no longer requires PROVIDER_MODE=live. It needs its own flag, a key and a
verified receipt, refuses an explicit mock mode, and builds its reasoning and
embedding routes fail-closed, so mock or fallback output never reaches a voice
reply.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent_voice.config import VoiceUnavailableError, load_limits, voice_route_refusal
from app.agent_voice.live_config import load_live_limits
from app.agent_voice.retrieval import VoiceEmbeddingsProvider, embedding_limitation
from app.agent_voice.service import VoiceReasoningProvider, shared_turn, voice_llm
from app.core.config import Settings
from app.interactive_agent.conversation import MODEL_REPLY_UNAVAILABLE
from app.providers.embeddings import MockEmbeddingsProvider
from app.providers.llm import LLMCompletionResult, MockLLMProvider, OpenAILLMProvider
from tests.test_agent_live_integration import live_ledger, live_limits  # noqa: F401
from tests.test_workflows import workflow_db  # noqa: F401


def _settings(**update: object) -> Settings:
    return Settings(_env_file=None).model_copy(update=update)


@pytest.fixture
def receipt(tmp_path, live_limits):  # noqa: F811
    path = tmp_path / "live-limits.json"
    path.write_text(live_limits.model_dump_json())
    return path


def test_live_voice_is_admitted_under_fallback_with_flag_key_and_receipt(receipt, live_limits):  # noqa: F811
    settings = _settings(
        provider_mode="fallback",
        agent_live_enabled=True,
        openai_api_key="fixture",
        agent_live_limits_file=str(receipt),
    )
    assert voice_route_refusal(settings) is None
    assert load_live_limits(settings) == live_limits


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"agent_live_enabled": False}, "gpt_live_not_enabled"),
        ({"openai_api_key": ""}, "provider_credential_unbound"),
        ({"openai_api_key": "   "}, "provider_credential_unbound"),
        ({"provider_mode": "mock"}, "approved_live_route_unavailable"),
        ({"agent_live_limits_file": ""}, "verified_live_limits_unbound"),
        ({"agent_voice_reasoning_model": "other-model"}, "verified_reasoning_route_mismatch"),
    ],
)
def test_live_voice_refuses_without_flag_key_receipt_or_in_mock_mode(receipt, change, reason):
    settings = _settings(
        provider_mode="fallback",
        agent_live_enabled=True,
        openai_api_key="fixture",
        agent_live_limits_file=str(receipt),
    ).model_copy(update=change)
    with pytest.raises(VoiceUnavailableError, match=reason):
        load_live_limits(settings)


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("fallback", "verified_realtime_limits_unbound"),
        ("live", "verified_realtime_limits_unbound"),
        ("mock", "approved_live_route_unavailable"),
    ],
)
def test_realtime_path_uses_the_same_route_gate(mode, reason):
    settings = _settings(agent_realtime_enabled=True, openai_api_key="fixture", provider_mode=mode)
    with pytest.raises(VoiceUnavailableError, match=reason):
        load_limits(settings)


def test_vector_retrieval_needs_its_own_receipt_and_never_runs_in_mock_mode(live_limits):  # noqa: F811
    ledger = SimpleNamespace(
        limits=live_limits, settings=_settings(provider_mode="fallback", openai_api_key="fixture")
    )
    assert embedding_limitation(ledger) is None
    ledger.settings = _settings(provider_mode="mock", openai_api_key="fixture")
    assert embedding_limitation(ledger) == (
        "Vector embedding unavailable: approved live embedding route is unavailable."
    )
    ledger.settings = _settings(provider_mode="fallback", openai_api_key="fixture")
    ledger.limits = live_limits.model_copy(update={"embedding": None})
    assert embedding_limitation(ledger) == (
        "Vector embedding unavailable: no verified embedding spending receipt."
    )


def test_voice_reasoning_route_is_rebuilt_without_a_mock_fallback():
    settings = _settings(provider_mode="fallback", openai_api_key="fixture")
    shared = OpenAILLMProvider(api_key="fixture", base_url="https://example.invalid", model="m")
    assert shared._fallback is not None  # the shared, non-voice route may fall back
    voice = voice_llm(shared, settings)
    assert isinstance(voice, OpenAILLMProvider)
    assert voice._fallback is None and voice._fail_closed is True
    with pytest.raises(VoiceUnavailableError, match="voice_reasoning_mock_refused"):
        voice_llm(MockLLMProvider(), settings)


def test_voice_refuses_mock_providers_and_mock_or_fallback_results(live_limits):  # noqa: F811
    ledger = SimpleNamespace(
        limits=live_limits,
        settings=_settings(),
        reasoning_attempt=lambda _request: 0,
        reasoning_done=lambda *_: None,
    )
    with pytest.raises(VoiceUnavailableError, match="voice_reasoning_mock_refused"):
        VoiceReasoningProvider(MockLLMProvider(), ledger)
    with pytest.raises(VoiceUnavailableError, match="voice_embedding_mock_refused"):
        VoiceEmbeddingsProvider(MockEmbeddingsProvider(dimensions=8), ledger, "key")

    def result(**over: object) -> LLMCompletionResult:
        fields: dict[str, object] = {
            "content": "Synthetic.",
            "model": live_limits.reasoning.model,
            "provider": "synthetic",
            "input_tokens": 1,
            "output_tokens": 1,
        }
        return LLMCompletionResult(**(fields | over))

    for bad in (result(fallback_used=True), result(provider="mock-llm")):
        upstream = SimpleNamespace(name="synthetic", kind="llm", complete=lambda _r, bad=bad: bad)
        provider = VoiceReasoningProvider(upstream, ledger)
        with pytest.raises(VoiceUnavailableError, match="voice_reasoning_response_outside_receipt"):
            provider.complete(SimpleNamespace(model=live_limits.reasoning.model))


def test_fallback_mode_voice_turn_fails_closed_and_never_uses_mock(live_ledger, monkeypatch):  # noqa: F811
    """PROVIDER_MODE=fallback, voice on, real provider factory, upstream down:
    the reply is the unavailable notice, and no mock LLM or mock embedding runs."""
    ledger = live_ledger
    ledger.settings = ledger.settings.model_copy(
        update={"provider_mode": "fallback", "openai_api_key": "fixture", "qdrant_url": ""}
    )
    mock_calls: list[str] = []

    def upstream_down(*_args, **_kwargs):
        raise ConnectionError("synthetic upstream outage")

    class DownClient:
        """Stands in for the OpenAI SDK client: every request fails."""

        def __init__(self, **_kwargs):
            self.embeddings = SimpleNamespace(create=upstream_down)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    # Fail at the transport, so each provider's own fallback logic still runs.
    monkeypatch.setattr(OpenAILLMProvider, "_post_json", upstream_down)
    monkeypatch.setattr("app.providers.embeddings.OpenAI", DownClient)
    monkeypatch.setattr(MockLLMProvider, "complete", lambda self, request: mock_calls.append("llm"))
    monkeypatch.setattr(
        MockEmbeddingsProvider,
        "embed_with_metadata",
        lambda self, texts: mock_calls.append("embeddings"),
    )
    body = {"message": "What is my BTC rule?", "conversation_id": str(ledger.conversation_id)}
    key, _ = ledger.tool("delegation-fallback", body)
    result = shared_turn(ledger, key, body)
    assert result["reply"].startswith(MODEL_REPLY_UNAVAILABLE)
    assert mock_calls == []
    assert result["authority_mutated"] is False and result["execution_attempted"] is False


@pytest.mark.parametrize("mode", ["", "off", "lve", "auto", "staging", "MOCK", " mock "])
def test_voice_route_gate_is_an_allowlist_of_live_and_fallback(mode):
    """Griller C1: any provider_mode other than live/fallback is refused."""
    settings = _settings(provider_mode=mode, openai_api_key="fixture")
    assert voice_route_refusal(settings) == "approved_live_route_unavailable"


@pytest.mark.parametrize("mode", ["live", "fallback", " LIVE ", "Fallback"])
def test_voice_route_gate_admits_live_and_fallback(mode):
    settings = _settings(provider_mode=mode, openai_api_key="fixture")
    assert voice_route_refusal(settings) is None


def test_voice_llm_refuses_unknown_provider_types():
    """Griller C2: an unrecognised provider (e.g. a wrapper) is never passed through."""

    class WrappedProvider:
        name = "wrapper"
        kind = "llm"

    settings = _settings(provider_mode="fallback", openai_api_key="fixture")
    with pytest.raises(VoiceUnavailableError, match="voice_reasoning_provider_unknown"):
        voice_llm(WrappedProvider(), settings)  # type: ignore[arg-type]
