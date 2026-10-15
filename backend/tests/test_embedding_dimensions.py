"""Embedding dimension resolution and provider alignment tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.providers.embedding_dimensions import (
    MOCK_EMBEDDINGS_DIMENSIONS,
    model_supports_dimensions_param,
    resolve_embeddings_dimensions,
)
from app.providers.embeddings import MockEmbeddingsProvider, OpenAIEmbeddingsProvider
from app.providers.factory import resolve_providers


def test_mock_default_dimensions() -> None:
    settings = Settings(openai_api_key="", log_json=False)
    assert resolve_embeddings_dimensions(settings) == MOCK_EMBEDDINGS_DIMENSIONS
    resolved = resolve_providers(settings)
    assert resolved.embeddings_dimensions == MOCK_EMBEDDINGS_DIMENSIONS
    assert len(resolved.embeddings.embed(["x"])[0]) == MOCK_EMBEDDINGS_DIMENSIONS


def test_openai_key_selects_model_native_dimensions() -> None:
    settings = Settings(
        openai_api_key="sk-test",
        embeddings_model="text-embedding-3-small",
        log_json=False,
        qdrant_url="",
        provider_mode="fallback",
    )
    assert resolve_embeddings_dimensions(settings) == 1536
    resolved = resolve_providers(settings)
    assert resolved.embeddings.name == "openai-embeddings"
    assert resolved.embeddings.dimensions == 1536


def test_mock_mode_with_key_keeps_mock_dimensions() -> None:
    settings = Settings(
        openai_api_key="sk-test",
        embeddings_model="text-embedding-3-large",
        log_json=False,
        provider_mode="mock",
    )
    assert resolve_embeddings_dimensions(settings) == MOCK_EMBEDDINGS_DIMENSIONS
    resolved = resolve_providers(settings)
    assert resolved.embeddings.name == "mock-embeddings"
    assert resolved.embeddings_dimensions == MOCK_EMBEDDINGS_DIMENSIONS


def test_explicit_embeddings_dimensions_override() -> None:
    settings = Settings(
        openai_api_key="sk-test",
        embeddings_model="text-embedding-3-small",
        embeddings_dimensions=512,
        log_json=False,
    )
    assert resolve_embeddings_dimensions(settings) == 512


def test_openai_fallback_mock_matches_dimensions() -> None:
    provider = OpenAIEmbeddingsProvider(
        model="text-embedding-3-small",
        api_key="",
        base_url="https://api.openai.com/v1",
        dimensions=1536,
    )
    result = provider.embed_with_metadata(["alpha"])
    assert result.fallback_used is True
    assert result.dimensions == 1536
    assert len(result.vectors[0]) == 1536


def _sdk(monkeypatch, payload):
    client = MagicMock()
    client.__enter__.return_value = client
    client.embeddings.create.return_value.model_dump.return_value = payload
    monkeypatch.setattr("app.providers.embeddings.OpenAI", lambda **kwargs: client)
    return client


def test_openai_request_includes_dimensions_for_v3_models(monkeypatch):
    client = _sdk(
        monkeypatch,
        {
            "model": "text-embedding-3-small",
            "data": [{"index": 0, "embedding": [0.1] * 512}],
            "usage": {"prompt_tokens": 3},
        },
    )
    provider = OpenAIEmbeddingsProvider(
        model="text-embedding-3-small",
        api_key="fixture",
        base_url="https://fixture.invalid/v1",
        dimensions=512,
    )
    result = provider.embed_with_metadata(["hello"])
    assert not result.fallback_used
    assert client.embeddings.create.call_args.kwargs == {
        "model": "text-embedding-3-small",
        "input": ["hello"],
        "dimensions": 512,
    }
    assert len(result.vectors[0]) == 512


def test_openai_dimension_mismatch_falls_back(monkeypatch):
    _sdk(monkeypatch, {"data": [{"index": 0, "embedding": [0.1] * 8}]})
    provider = OpenAIEmbeddingsProvider(
        model="text-embedding-3-small",
        api_key="fixture",
        base_url="https://fixture.invalid/v1",
        dimensions=1536,
    )
    result = provider.embed_with_metadata(["hello"])
    assert result.fallback_used and len(result.vectors[0]) == 1536


def test_model_supports_dimensions_param() -> None:
    assert model_supports_dimensions_param("text-embedding-3-small") is True
    assert model_supports_dimensions_param("text-embedding-ada-002") is False


def test_mock_provider_rejects_invalid_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        MockEmbeddingsProvider(dimensions=0)


def test_fallback_dimensions_must_align() -> None:
    with pytest.raises(ValueError, match="fallback dimensions"):
        OpenAIEmbeddingsProvider(
            model="text-embedding-3-small",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            dimensions=1536,
            fallback=MockEmbeddingsProvider(dimensions=384),
        )


def test_embedding_result_serializes_dimensions() -> None:
    result = MockEmbeddingsProvider(dimensions=8).embed_with_metadata(["z"])
    payload = json.loads(
        json.dumps(
            {
                "dimensions": result.dimensions,
                "provider": result.provider,
                "fallback_used": result.fallback_used,
            }
        )
    )
    assert payload["dimensions"] == 8
