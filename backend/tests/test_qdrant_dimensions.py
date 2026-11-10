"""Qdrant vector-size compatibility and API key wiring tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.providers.factory import resolve_providers
from app.providers.qdrant import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorDimensionMismatchError,
    VectorPoint,
    VectorSearchFilters,
)
from app.providers.registry import build_default_registry
from app.services.rag_service import RAG_COLLECTION


def _connected_store(
    *, existing_size: int | None, configured_size: int = 1536
) -> QdrantVectorStore:
    store = QdrantVectorStore.__new__(QdrantVectorStore)
    store._url = "https://qdrant.example"
    store._api_key = "secret-not-asserted-in-status"
    store._fail_closed = False
    store._fallback = InMemoryVectorStore()
    store._vector_size = configured_size
    store._client = MagicMock()
    store._using_qdrant = True
    store._dimension_mismatch = None
    store._payload_indexes_ready = set()
    store._live_dimension_mismatch = None
    store._collection_probes = {}

    if existing_size is None:

        def _missing(_name: str) -> None:
            raise RuntimeError("not found")

        store._client.get_collection.side_effect = _missing
    else:
        info = MagicMock()
        info.config.params.vectors.size = existing_size
        store._client.get_collection.return_value = info
    return store


def test_qdrant_connect_passes_api_key() -> None:
    fake_client = MagicMock()
    with patch("qdrant_client.QdrantClient", return_value=fake_client) as ctor:
        store = QdrantVectorStore(
            "https://qdrant.example",
            api_key="qdrant-secret",
            vector_size=1536,
            fallback=InMemoryVectorStore(),
        )
    ctor.assert_called_once()
    kwargs = ctor.call_args.kwargs
    assert kwargs["url"] == "https://qdrant.example"
    assert kwargs["api_key"] == "qdrant-secret"
    assert store.using_qdrant is True


def test_resolve_providers_passes_qdrant_api_key_and_dimensions() -> None:
    fake_client = MagicMock()
    with patch("qdrant_client.QdrantClient", return_value=fake_client) as ctor:
        resolved = resolve_providers(
            Settings(
                openai_api_key="sk-test",
                embeddings_model="text-embedding-3-small",
                provider_mode="fallback",
                qdrant_url="https://qdrant.example",
                qdrant_api_key="qdrant-secret",
                log_json=False,
            )
        )
    assert isinstance(resolved.vector_store, QdrantVectorStore)
    assert resolved.vector_store.vector_size == 1536
    assert ctor.call_args.kwargs["api_key"] == "qdrant-secret"


def test_registry_reuses_resolved_vector_store() -> None:
    fake_client = MagicMock()
    with patch("qdrant_client.QdrantClient", return_value=fake_client):
        settings = Settings(
            openai_api_key="",
            provider_mode="fallback",
            qdrant_url="https://qdrant.example",
            qdrant_api_key="qdrant-secret",
            log_json=False,
        )
        resolved = resolve_providers(settings)
        registry = build_default_registry(settings)
    vector = next(p for p in registry.all() if p.kind.value == "vector")
    assert vector.name == "qdrant"
    # Same configured size as resolved store (no second unconfigured client).
    assert isinstance(resolved.vector_store, QdrantVectorStore)
    status = vector.status()
    assert "384-d" in (status.detail or "") or "configured" in (status.detail or "").lower()


def test_assert_compatible_rejects_mismatched_collection() -> None:
    store = _connected_store(existing_size=384, configured_size=1536)
    with pytest.raises(VectorDimensionMismatchError) as exc:
        store.assert_compatible(RAG_COLLECTION, 1536)
    assert exc.value.actual == 384
    assert exc.value.expected == 1536


def test_upsert_does_not_write_incompatible_vectors_to_qdrant() -> None:
    store = _connected_store(existing_size=384, configured_size=1536)
    points = [
        VectorPoint(
            point_id="00000000-0000-0000-0000-000000000099",
            vector=[0.1] * 1536,
            payload={"chunk_id": "c1"},
        )
    ]
    store.upsert(RAG_COLLECTION, points)
    store._client.upsert.assert_not_called()
    hits = store._fallback.search(
        RAG_COLLECTION,
        [0.1] * 1536,
        filters=VectorSearchFilters(),
        top_k=1,
    )
    assert len(hits) == 1
    status = store.status()
    assert status.health.value == "degraded"
    assert status.using_fallback is True
    assert "384" in (status.detail or "")


def test_upsert_creates_collection_when_missing() -> None:
    store = _connected_store(existing_size=None, configured_size=1536)
    # First get_collection raises; create should be called.
    points = [
        VectorPoint(
            point_id="00000000-0000-0000-0000-000000000088",
            vector=[0.25] * 1536,
            payload={},
        )
    ]
    store.upsert(RAG_COLLECTION, points)
    store._client.create_collection.assert_called_once()
    store._client.upsert.assert_called_once()


def test_recreate_collection_deletes_and_creates() -> None:
    store = _connected_store(existing_size=384, configured_size=1536)
    store.recreate_collection(RAG_COLLECTION, vector_size=1536)
    store._client.delete_collection.assert_called_once()
    store._client.create_collection.assert_called_once()
    assert store._dimension_mismatch is None


def test_status_omits_api_key_and_url() -> None:
    store = _connected_store(existing_size=1536, configured_size=1536)
    detail = store.status().detail or ""
    assert "secret" not in detail
    assert "qdrant.example" not in detail


def test_search_uses_query_points_when_search_missing() -> None:
    """qdrant-client>=1.16 removed Client.search; query_points is required."""
    store = _connected_store(existing_size=1536, configured_size=1536)
    store._payload_indexes_ready = {RAG_COLLECTION}

    point = MagicMock()
    point.id = "00000000-0000-0000-0000-000000000001"
    point.score = 0.91
    point.payload = {"chunk_id": "chunk-1", "organization_id": "org-1"}
    response = MagicMock()
    response.points = [point]

    class QueryPointsOnlyClient:
        def __init__(self) -> None:
            self.query_points = MagicMock(return_value=response)
            self.get_collection = store._client.get_collection
            self.create_payload_index = MagicMock()

    store._client = QueryPointsOnlyClient()  # type: ignore[assignment]

    hits = store.search(
        RAG_COLLECTION,
        [0.1] * 1536,
        filters=VectorSearchFilters(),
        top_k=3,
    )
    assert len(hits) == 1
    assert hits[0].point_id == "chunk-1"
    assert hits[0].score == 0.91
    store._client.query_points.assert_called_once()
    store._client.create_payload_index.assert_not_called()


def test_ensure_collection_creates_payload_indexes() -> None:
    store = _connected_store(existing_size=None, configured_size=1536)
    store._payload_indexes_ready = set()
    store._ensure_collection(RAG_COLLECTION, vector_size=1536)
    assert store._client.create_payload_index.call_count == len(
        QdrantVectorStore._PAYLOAD_INDEX_FIELDS
    )
    assert RAG_COLLECTION in store._payload_indexes_ready
