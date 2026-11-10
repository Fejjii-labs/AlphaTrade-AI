"""Live collection vector size is checked by /health/ready and at startup (review item 10)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.routes.health import probe_vector_collections
from app.core.config import Settings
from app.main import create_app
from app.providers import qdrant as qdrant_module
from app.providers.base import ProviderHealth, ProviderKind
from app.providers.qdrant import KNOWLEDGE_COLLECTION, InMemoryVectorStore, QdrantVectorStore
from app.providers.registry import ProviderRegistry, _VectorStoreAdapter


class _NotFoundError(Exception):
    status_code = 404


def _store(*, live_size: int | None, configured: int = 1536, error: Exception | None = None):
    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store._url = "https://qdrant.internal.example"
    store._api_key = None
    store._fail_closed = False
    store._fallback = InMemoryVectorStore()
    store._vector_size = configured
    store._client = MagicMock()
    store._using_qdrant = True
    store._dimension_mismatch = None
    store._live_dimension_mismatch = None
    store._collection_probes = {}
    store._payload_indexes_ready = set()
    if error is not None:
        store._client.get_collection.side_effect = error
    else:
        info = MagicMock()
        info.config.params.vectors.size = live_size
        store._client.get_collection.return_value = info
    return store


def test_probe_reports_mismatch_and_degrades_status_without_url() -> None:
    store = _store(live_size=3072)
    assert store.probe_collection_compatibility() == "mismatch"
    store._client.get_collection.assert_called_once_with(KNOWLEDGE_COLLECTION)
    status = store.status()
    assert status.health is ProviderHealth.DEGRADED
    assert "3072" in (status.detail or "") and "1536" in (status.detail or "")
    assert "qdrant.internal.example" not in (status.detail or "")


def test_probe_compatible_and_missing_are_fine() -> None:
    assert _store(live_size=1536).probe_collection_compatibility() == "compatible"
    missing = _store(live_size=None, error=_NotFoundError("Not found: Collection"))
    assert missing.probe_collection_compatibility() == "missing"
    assert missing.status().health is ProviderHealth.HEALTHY


def test_probe_unreachable_does_not_claim_mismatch() -> None:
    store = _store(live_size=None, error=TimeoutError("timed out"))
    assert store.probe_collection_compatibility() == "unreachable"
    assert store.status().health is ProviderHealth.HEALTHY


def test_probe_is_cached_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(qdrant_module.time, "monotonic", lambda: clock[0])
    store = _store(live_size=1536)
    store.probe_collection_compatibility()
    store.probe_collection_compatibility()
    assert store._client.get_collection.call_count == 1
    clock[0] += qdrant_module.COLLECTION_PROBE_TTL_SECONDS + 1
    store.probe_collection_compatibility()
    assert store._client.get_collection.call_count == 2


def test_later_compatible_probe_clears_live_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr(qdrant_module.time, "monotonic", lambda: clock[0])
    store = _store(live_size=3072)
    store.probe_collection_compatibility()
    store._client.get_collection.return_value.config.params.vectors.size = 1536
    clock[0] += qdrant_module.COLLECTION_PROBE_TTL_SECONDS + 1
    assert store.probe_collection_compatibility() == "compatible"
    assert store.status().health is ProviderHealth.HEALTHY


def test_probe_vector_collections_only_probes_vector_providers() -> None:
    registry = ProviderRegistry()
    registry.register(_VectorStoreAdapter(_store(live_size=3072)))
    assert probe_vector_collections(registry) == {"qdrant": "mismatch"}
    # In-memory stores have no live collection to check.
    other = ProviderRegistry()
    other.register(_VectorStoreAdapter(InMemoryVectorStore()))
    assert probe_vector_collections(other) == {}


def _client_with_vector(settings: Settings, store: QdrantVectorStore) -> TestClient:
    app = create_app(settings=settings)
    registry = ProviderRegistry()
    adapter = _VectorStoreAdapter(store)
    assert adapter.kind is ProviderKind.VECTOR
    registry.register(adapter)
    app.state.provider_registry = registry
    return TestClient(app)


def test_ready_endpoint_not_ready_on_mismatch_but_liveness_stays_ok(settings: Settings) -> None:
    with _client_with_vector(settings, _store(live_size=3072)) as client:
        ready = client.get("/health/ready").json()
        assert ready["ready"] is False
        assert ready["status"] == "degraded"
        assert ready["degraded_providers"] == ["qdrant"]
        assert client.get("/health").status_code == 200
        providers = client.get("/providers/status").json()
        text = str(providers)
        assert "qdrant.internal.example" not in text


def test_ready_endpoint_ready_when_collection_missing(settings: Settings) -> None:
    store = _store(live_size=None, error=_NotFoundError("Not found"))
    with _client_with_vector(settings, store) as client:
        ready = client.get("/health/ready").json()
        assert ready["ready"] is True
        assert ready["degraded_providers"] == []
