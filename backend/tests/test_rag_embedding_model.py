"""Vectors carry `embedding_model`; search refuses mixed embedding spaces (review item 8)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ServiceUnavailableError
from app.providers.embeddings import (
    MOCK_EMBEDDINGS_MODEL,
    EmbeddingResult,
    MockEmbeddingsProvider,
    vector_space_model,
)
from app.providers.qdrant import InMemoryVectorStore, QdrantVectorStore
from app.rag.indexing import COLLECTION
from app.schemas.common import DocumentSourceType
from app.schemas.rag import IngestDocumentRequest, RagQuery
from app.services.rag_service import RagService
from tests.support.knowledge_indexing import drain_indexing
from tests.support.rag_sqlite import ORG_ID, sqlite_rag_session

TEXTS = ("Every trade requires a stop loss.", "Human approval precedes paper orders.")


@pytest.fixture
def session() -> Iterator[Session]:
    with sqlite_rag_session() as db:
        yield db


def _service(session: Session, *, strict: bool = False) -> tuple[RagService, InMemoryVectorStore]:
    store = InMemoryVectorStore()
    service = RagService(
        session,
        settings=Settings(log_json=False, rag_embedding_model_strict=strict),
        embeddings=MockEmbeddingsProvider(),
        vector_store=store,
    )
    for index, text in enumerate(TEXTS):
        service.ingest(
            IngestDocumentRequest(
                organization_id=ORG_ID,
                source_type=DocumentSourceType.RISK_POLICY,
                title=f"Policy {index}",
                text=text,
            )
        )
    drain_indexing(service)
    return service, store


def _points(store: InMemoryVectorStore) -> list:
    return list(store._collections[COLLECTION].values())


def _query(text: str) -> RagQuery:
    return RagQuery(query=text, organization_id=ORG_ID, top_k=5)


def test_indexing_writes_embedding_model_on_every_point(session: Session) -> None:
    _, store = _service(session)
    points = _points(store)
    assert len(points) == len(TEXTS)
    assert {p.payload["embedding_model"] for p in points} == {MOCK_EMBEDDINGS_MODEL}


def test_fallback_vectors_are_labelled_with_the_mock_model() -> None:
    fallback = EmbeddingResult(
        vectors=[], model="text-embedding-3-small", provider="openai", fallback_used=True
    )
    real = EmbeddingResult(vectors=[], model="text-embedding-3-small", provider="openai")
    assert vector_space_model(fallback) == MOCK_EMBEDDINGS_MODEL
    assert vector_space_model(real) == "text-embedding-3-small"


def test_matching_model_hits_are_returned(session: Session) -> None:
    service, _ = _service(session)
    result = service.search(_query(TEXTS[0]))
    assert result.chunks and result.chunks[0].content == TEXTS[0]
    assert "embedding_model_mismatch" not in (result.detail or "")


def test_all_hits_from_another_model_refuse_with_typed_reason(session: Session) -> None:
    service, store = _service(session)
    for point in _points(store):
        point.payload["embedding_model"] = "text-embedding-3-large"
    with pytest.raises(ServiceUnavailableError) as exc:
        service.search(_query(TEXTS[0]))
    assert exc.value.details["reason"] == "embedding_model_mismatch"


def test_partial_mismatch_withholds_foreign_hits_and_degrades(session: Session) -> None:
    service, store = _service(session)
    foreign = next(p for p in _points(store) if p.payload["title"] == "Policy 1")
    foreign.payload["embedding_model"] = "text-embedding-3-large"
    result = service.search(_query(TEXTS[0]))
    assert [c.title for c in result.chunks] == ["Policy 0"]
    assert result.degraded is True
    assert (result.detail or "").startswith("embedding_model_mismatch")


def test_legacy_points_allowed_by_default(session: Session) -> None:
    service, store = _service(session)
    for point in _points(store):
        del point.payload["embedding_model"]
    result = service.search(_query(TEXTS[0]))
    assert result.chunks


def test_legacy_points_refused_when_strict(session: Session) -> None:
    service, store = _service(session, strict=True)
    for point in _points(store):
        del point.payload["embedding_model"]
    with pytest.raises(ServiceUnavailableError) as exc:
        service.search(_query(TEXTS[0]))
    assert exc.value.details["reason"] == "embedding_model_mismatch"


def test_embedding_model_has_a_qdrant_payload_index() -> None:
    assert "embedding_model" in QdrantVectorStore._PAYLOAD_INDEX_FIELDS
