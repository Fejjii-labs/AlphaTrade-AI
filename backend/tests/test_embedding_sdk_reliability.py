"""Embedding validation and connectivity versus proven generation."""

from unittest.mock import MagicMock, patch

import httpx2
import pytest
from openai import OpenAI

from app.core.errors import ServiceUnavailableError
from app.providers.base import ProviderHealth
from app.providers.embeddings import OpenAIEmbeddingsProvider


def provider():
    return OpenAIEmbeddingsProvider(
        model="text-embedding-3-small",
        api_key="fixture-key",
        base_url="https://fixture.invalid/v1",
        dimensions=2,
        fail_closed=True,
    )


@pytest.fixture
def sdk():
    client = MagicMock()
    client.__enter__.return_value = client
    with patch("app.providers.embeddings.OpenAI", return_value=client) as constructor:
        yield client, constructor


def test_batch_order_and_exact_dimensions(sdk):
    client, constructor = sdk
    client.embeddings.create.return_value.model_dump.return_value = {
        "data": [{"index": 1, "embedding": [3, 4]}, {"index": 0, "embedding": [1, 2]}],
        "usage": {"prompt_tokens": 5},
        "model": "text-embedding-3-small",
    }
    result = provider().embed_with_metadata(["a", "b"])
    assert result.vectors == [[1.0, 2.0], [3.0, 4.0]] and result.input_tokens == 5
    assert constructor.call_args.kwargs["max_retries"] == 0


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [{"index": 0, "embedding": [1]}],
        [{"index": 0, "embedding": [1, float("nan")]}],
        [{"index": 0, "embedding": [True, 1]}],
        [{"index": 0, "embedding": [1, 2]}, {"index": 0, "embedding": [1, 2]}],
        [{"index": 4, "embedding": [1, 2]}],
    ],
)
def test_bad_batches_fail_closed(sdk, rows):
    client, _ = sdk
    client.embeddings.create.return_value.model_dump.return_value = {"data": rows}
    with pytest.raises(ServiceUnavailableError):
        provider().embed(["a"])


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_health_rejects_all_unsuccessful_status_without_paid_probe(status):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx2.Response(status, json={"error": {"message": "private fixture-key"}})

    def sdk(**kwargs):
        return OpenAI(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))

    with patch("app.providers.embeddings.OpenAI", side_effect=sdk):
        result = provider().status()
    assert result.health == ProviderHealth.UNAVAILABLE
    assert len(calls) == 1 and calls[0].url.path.endswith("/models")
    assert "fixture-key" not in (result.detail or "")


@pytest.mark.parametrize("payload", [{}, {"data": []}, {"data": [{}]}, {"data": "wrong"}])
def test_malformed_health_is_unhealthy(sdk, payload):
    client, _ = sdk
    client.models.list.return_value.model_dump.return_value = payload
    assert provider().status().health == ProviderHealth.UNAVAILABLE


def test_health_lists_models_but_does_not_claim_generation(sdk):
    client, _ = sdk
    client.models.list.return_value.model_dump.return_value = {"data": [{"id": "a"}]}
    item = provider()
    assert item.status().health == ProviderHealth.DEGRADED
    client.embeddings.create.assert_not_called()
    client.embeddings.create.return_value.model_dump.return_value = {
        "data": [{"index": 0, "embedding": [1, 2]}],
        "usage": {},
    }
    item.embed(["a"])
    assert item.status().health == ProviderHealth.HEALTHY
    assert client.embeddings.create.call_count == 1
