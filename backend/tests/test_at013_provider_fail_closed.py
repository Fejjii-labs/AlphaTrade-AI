"""AT-013: fail-closed RAG / provider behavior (staging vs local)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.deployment_safety import validate_deployment_settings
from app.core.errors import ServiceUnavailableError
from app.core.provider_policy import (
    provider_fail_closed,
    requires_authoritative_qdrant,
    requires_configured_openai,
)
from app.db.base import Base
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.providers.embeddings import (
    EmbeddingResult,
    MockEmbeddingsProvider,
    OpenAIEmbeddingsProvider,
)
from app.providers.factory import resolve_providers
from app.providers.llm import (
    LLMCompletionRequest,
    LLMMessage,
    MockLLMProvider,
    OpenAILLMProvider,
)
from app.providers.qdrant import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorPoint,
    VectorSearchFilters,
    reset_process_vector_store,
)
from app.schemas.common import DocumentSourceType
from app.schemas.rag import IngestDocumentRequest, RagQuery
from app.services.rag_service import RagService
from tests.support.knowledge_indexing import drain_indexing

ORG_ID = UUID("00000000-0000-0000-0000-000000000130")
USER_ID = UUID("00000000-0000-0000-0000-000000000131")

_STAGING_SAFE: dict[str, Any] = {
    "environment": "staging",
    "jwt_secret": "x" * 32,
    "database_url": "postgresql+psycopg://user:pass@db.example.com:5432/alphatrade",
    "redis_url": "redis://redis.example.com:6379/0",
    "qdrant_url": "https://qdrant.example.com",
    "openai_api_key": "sk-test-not-a-real-key",
    "cors_origins": "https://app.example.com",
    "auth_refresh_cookie_enabled": True,
    "auth_cookie_secure": True,
    "auth_cookie_samesite": "none",
    "enable_real_trading": False,
    "execution_mode": "paper",
    "exchange_mode": "paper_internal",
    "rate_limit_use_redis": True,
    "rate_limit_allow_in_memory_fallback": False,
    "trusted_proxy_hops": 1,
    "debug": False,
    "provider_mode": "fallback",
    "log_json": False,
}


def _local(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "local",
        "execution_mode": "paper",
        "enable_real_trading": False,
        "exchange_mode": "paper_internal",
        "provider_mode": "mock",
        "openai_api_key": "",
        "qdrant_url": "http://localhost:6333",
        "log_json": False,
        "embeddings_dimensions": 8,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def db_session() -> Iterator[Session]:
    reset_process_vector_store()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        from app.db.models import Organization, User

        session.add_all(
            [
                Organization(id=ORG_ID, name="AT-013 Org"),
                User(id=USER_ID, email="at013@test.example", hashed_password="hash"),
            ]
        )
        session.commit()
        yield session
    reset_process_vector_store()
    engine.dispose()


class TestProviderPolicy:
    def test_local_allows_soft_fallback(self) -> None:
        s = _local()
        assert provider_fail_closed(s) is False
        assert requires_configured_openai(s) is False
        assert requires_authoritative_qdrant(s) is False

    def test_staging_requires_real_providers(self) -> None:
        s = Settings(**_STAGING_SAFE)
        assert provider_fail_closed(s) is True
        assert requires_configured_openai(s) is True
        assert requires_authoritative_qdrant(s) is True


class TestDeploymentSafetyOpenAI:
    def test_staging_missing_openai_key_fails(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="openai_api_key"):
            Settings(**{**_STAGING_SAFE, "openai_api_key": ""})

    def test_staging_rejects_provider_mode_mock(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="provider_mode=mock"):
            Settings(**{**_STAGING_SAFE, "provider_mode": "mock"})

    def test_local_missing_openai_ok(self) -> None:
        s = _local(openai_api_key="")
        validate_deployment_settings(s)


class TestLLMFailClosed:
    def _request(self) -> LLMCompletionRequest:
        return LLMCompletionRequest(
            messages=[LLMMessage(role="user", content="hi")],
            model="gpt-4o-mini",
        )

    def test_openai_missing_key_raises_in_fail_closed(self) -> None:
        p = OpenAILLMProvider(
            api_key="",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            fail_closed=True,
        )
        with pytest.raises(ServiceUnavailableError, match="unavailable"):
            p.complete(self._request())

    def test_openai_outage_raises_without_mock_when_fail_closed(self) -> None:
        p = OpenAILLMProvider(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            fail_closed=True,
        )
        with patch("app.providers.llm.OpenAI") as client_cls:
            client = MagicMock()
            client.__enter__.return_value = client
            client.__exit__.return_value = False
            client.chat.completions.create.side_effect = RuntimeError("connection reset")
            client_cls.return_value = client
            with pytest.raises(ServiceUnavailableError, match="unavailable"):
                p.complete(self._request())

    def test_local_openai_falls_back_to_mock(self) -> None:
        p = OpenAILLMProvider(
            api_key="",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            fail_closed=False,
        )
        result = p.complete(self._request())
        assert result.fallback_used is True

    def test_factory_staging_uses_fail_closed_openai(self) -> None:
        resolved = resolve_providers(Settings(**_STAGING_SAFE))
        assert isinstance(resolved.llm, OpenAILLMProvider)
        assert resolved.llm._fail_closed is True
        assert isinstance(resolved.embeddings, OpenAIEmbeddingsProvider)
        assert resolved.embeddings._fail_closed is True
        assert isinstance(resolved.vector_store, QdrantVectorStore)
        assert resolved.vector_store._fail_closed is True

    def test_factory_local_mock_mode(self) -> None:
        resolved = resolve_providers(_local(provider_mode="mock", openai_api_key=""))
        assert isinstance(resolved.llm, MockLLMProvider)
        assert isinstance(resolved.embeddings, MockEmbeddingsProvider)


class TestEmbeddingsFailClosed:
    def test_missing_key_raises_in_fail_closed(self) -> None:
        p = OpenAIEmbeddingsProvider(
            model="text-embedding-3-small",
            api_key="",
            base_url="https://api.openai.com/v1",
            dimensions=8,
            fail_closed=True,
        )
        with pytest.raises(ServiceUnavailableError, match="unavailable"):
            p.embed(["hello"])

    def test_embedding_api_failure_raises_when_fail_closed(self) -> None:
        p = OpenAIEmbeddingsProvider(
            model="text-embedding-3-small",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            dimensions=8,
            fail_closed=True,
        )
        with patch("app.providers.embeddings.OpenAI") as client_cls:
            client = MagicMock()
            client.__enter__.return_value = client
            client.__exit__.return_value = False
            client.embeddings.create.side_effect = RuntimeError("openai 500")
            client_cls.return_value = client
            with pytest.raises(ServiceUnavailableError, match="unavailable"):
                p.embed(["hello"])

    def test_local_falls_back_to_mock_vectors(self) -> None:
        p = OpenAIEmbeddingsProvider(
            model="text-embedding-3-small",
            api_key="",
            base_url="https://api.openai.com/v1",
            dimensions=8,
            fail_closed=False,
        )
        result = p.embed_with_metadata(["hello"])
        assert result.fallback_used is True
        assert len(result.vectors[0]) == 8

    def test_status_unavailable_when_fail_closed_no_key(self) -> None:
        p = OpenAIEmbeddingsProvider(
            model="text-embedding-3-small",
            api_key="",
            base_url="https://api.openai.com/v1",
            dimensions=8,
            fail_closed=True,
        )
        st = p.status()
        assert st.health == ProviderHealth.UNAVAILABLE
        assert st.using_fallback is False


class TestQdrantFailClosed:
    def test_upsert_refuses_memory_when_fail_closed(self) -> None:
        store = QdrantVectorStore(
            "https://qdrant.example.com",
            vector_size=8,
            fail_closed=True,
        )
        store._client = None
        store._using_qdrant = False
        with pytest.raises(ServiceUnavailableError, match="unavailable"):
            store.upsert(
                "c",
                [VectorPoint(point_id=str(uuid4()), vector=[0.1] * 8, payload={})],
            )

    def test_search_refuses_memory_when_fail_closed(self) -> None:
        store = QdrantVectorStore(
            "https://qdrant.example.com",
            vector_size=8,
            fail_closed=True,
        )
        store._client = None
        store._using_qdrant = False
        with pytest.raises(ServiceUnavailableError, match="unavailable"):
            store.search(
                "c",
                [0.1] * 8,
                filters=VectorSearchFilters(),
                top_k=3,
            )

    def test_auth_failure_status_unavailable_fail_closed(self) -> None:
        with patch("qdrant_client.QdrantClient") as client_cls:
            client_cls.side_effect = RuntimeError("Unauthorized")
            store = QdrantVectorStore(
                "https://qdrant.example.com",
                api_key="bad-key",
                vector_size=8,
                fail_closed=True,
            )
        st = store.status()
        assert st.health == ProviderHealth.UNAVAILABLE
        assert st.using_fallback is False
        assert "unavailable" in (st.detail or "").lower()

    def test_dimension_mismatch_on_upsert(self) -> None:
        store = QdrantVectorStore(
            "https://qdrant.example.com",
            vector_size=8,
            fail_closed=True,
        )
        store._client = MagicMock()
        store._using_qdrant = True
        with pytest.raises(ServiceUnavailableError) as exc_info:
            store.upsert(
                "c",
                [VectorPoint(point_id=str(uuid4()), vector=[0.1] * 4, payload={})],
            )
        assert exc_info.value.details.get("reason") == "embedding_dimension_mismatch"

    def test_local_allows_memory_fallback(self) -> None:
        store = QdrantVectorStore(
            "https://qdrant.example.com",
            vector_size=8,
            fail_closed=False,
        )
        store._client = None
        store._using_qdrant = False
        store.upsert(
            "c",
            [VectorPoint(point_id=str(uuid4()), vector=[0.1] * 8, payload={})],
        )
        st = store.status()
        assert st.using_fallback is True


class TestRagIngestFailClosed:
    def _staging_settings(self) -> Settings:
        return Settings(**{**_STAGING_SAFE, "embeddings_dimensions": 8})

    def test_worker_refuses_embedding_fallback_after_pending_storage(
        self, db_session: Session
    ) -> None:
        embeddings = MagicMock()
        embeddings.embed_with_metadata.return_value = EmbeddingResult(
            vectors=[[0.1] * 8],
            model="mock-embeddings",
            provider="openai-embeddings",
            dimensions=8,
            fallback_used=True,
        )
        embeddings.dimensions = 8
        qdrant = MagicMock(spec=QdrantVectorStore)
        qdrant.name = "qdrant"
        qdrant.using_qdrant = True
        qdrant.status.return_value = ProviderStatus(
            name="qdrant",
            kind=ProviderKind.VECTOR,
            health=ProviderHealth.HEALTHY,
            using_fallback=False,
        )
        svc = RagService(
            db_session,
            embeddings=embeddings,
            vector_store=qdrant,
            settings=self._staging_settings(),
        )
        result = svc.ingest(
            IngestDocumentRequest(
                organization_id=ORG_ID,
                user_id=USER_ID,
                source_type=DocumentSourceType.RISK_POLICY,
                title="Policy",
                text="Capital preservation requires a stop loss on every trade.",
            )
        )
        assert result.vector_index_status == "pending"
        assert drain_indexing(svc) == ["pending"]
        qdrant.upsert.assert_not_called()

    def test_worker_refuses_memory_after_pending_storage(self, db_session: Session) -> None:
        embeddings = MagicMock()
        embeddings.embed_with_metadata.return_value = EmbeddingResult(
            vectors=[[0.1] * 8],
            model="text-embedding-3-small",
            provider="openai-embeddings",
            dimensions=8,
            fallback_used=False,
        )
        embeddings.dimensions = 8
        memory = InMemoryVectorStore()
        svc = RagService(
            db_session,
            embeddings=embeddings,
            vector_store=memory,
            settings=self._staging_settings(),
        )
        result = svc.ingest(
            IngestDocumentRequest(
                organization_id=ORG_ID,
                user_id=USER_ID,
                source_type=DocumentSourceType.RISK_POLICY,
                title="Policy",
                text="Capital preservation requires a stop loss on every trade.",
            )
        )
        assert result.vector_index_status == "pending"
        assert drain_indexing(svc) == ["pending"]

    def test_worker_upsert_failure_retains_pending_content(self, db_session: Session) -> None:
        embeddings = MagicMock()
        embeddings.embed_with_metadata.return_value = EmbeddingResult(
            vectors=[[0.1] * 8],
            model="text-embedding-3-small",
            provider="openai-embeddings",
            dimensions=8,
            fallback_used=False,
        )
        embeddings.dimensions = 8
        qdrant = MagicMock(spec=QdrantVectorStore)
        qdrant.name = "qdrant"
        qdrant.using_qdrant = True
        qdrant.status.return_value = ProviderStatus(
            name="qdrant",
            kind=ProviderKind.VECTOR,
            health=ProviderHealth.HEALTHY,
            using_fallback=False,
        )
        qdrant.upsert.side_effect = ServiceUnavailableError(
            "Vector store is unavailable.",
            details={"reason": "qdrant_upsert_failed"},
        )
        svc = RagService(
            db_session,
            embeddings=embeddings,
            vector_store=qdrant,
            settings=self._staging_settings(),
        )
        result = svc.ingest(
            IngestDocumentRequest(
                organization_id=ORG_ID,
                user_id=USER_ID,
                source_type=DocumentSourceType.RISK_POLICY,
                title="Policy",
                text="Capital preservation requires a stop loss on every trade.",
            )
        )
        assert result.vector_index_status == "pending"
        assert drain_indexing(svc) == ["pending"]
        from app.db.models import Document

        assert db_session.query(Document).count() == 1

    def test_search_retrieval_failure_fail_closed(self, db_session: Session) -> None:
        embeddings = MagicMock()
        embeddings.embed_with_metadata.return_value = EmbeddingResult(
            vectors=[[0.1] * 8],
            model="text-embedding-3-small",
            provider="openai-embeddings",
            dimensions=8,
            fallback_used=False,
        )
        qdrant = MagicMock(spec=QdrantVectorStore)
        qdrant.name = "qdrant"
        qdrant.using_qdrant = True
        qdrant.search.side_effect = ServiceUnavailableError(
            "Vector store is unavailable.",
            details={"reason": "qdrant_search_failed"},
        )
        qdrant.status.return_value = ProviderStatus(
            name="qdrant",
            kind=ProviderKind.VECTOR,
            health=ProviderHealth.UNAVAILABLE,
        )
        svc = RagService(
            db_session,
            embeddings=embeddings,
            vector_store=qdrant,
            settings=self._staging_settings(),
        )
        with pytest.raises(ServiceUnavailableError):
            svc.search(
                RagQuery(
                    query="stop loss policy",
                    organization_id=ORG_ID,
                    user_id=USER_ID,
                    top_k=3,
                )
            )

    def test_local_mock_ingest_allowed(self, db_session: Session) -> None:
        embeddings = MockEmbeddingsProvider(dimensions=8)
        memory = InMemoryVectorStore()
        svc = RagService(
            db_session,
            embeddings=embeddings,
            vector_store=memory,
            settings=_local(embeddings_dimensions=8),
        )
        result = svc.ingest(
            IngestDocumentRequest(
                organization_id=ORG_ID,
                user_id=USER_ID,
                source_type=DocumentSourceType.RISK_POLICY,
                title="Policy",
                text="Capital preservation requires a stop loss on every trade.",
            )
        )
        assert result.chunk_count >= 1
        assert result.vector_index_status == "pending"
        assert result.vector_backend is None
        assert drain_indexing(svc) == ["ready"]
        from app.db.models import Document

        duplicate = svc._duplicate_result(db_session.get(Document, result.document_id))
        assert duplicate.fallback_used is True and duplicate.vector_backend == memory.name


class TestReadinessFailClosed:
    def test_critical_fallback_and_mock_count_as_unavailable(self) -> None:
        from app.api.routes import health as health_mod

        statuses = [
            ProviderStatus(
                name="openai-embeddings",
                kind=ProviderKind.EMBEDDINGS,
                health=ProviderHealth.DEGRADED,
                using_fallback=True,
            ),
            ProviderStatus(
                name="mock-llm",
                kind=ProviderKind.LLM,
                health=ProviderHealth.HEALTHY,
                is_mock=True,
            ),
            ProviderStatus(
                name="market-data",
                kind=ProviderKind.MARKET_DATA,
                health=ProviderHealth.HEALTHY,
                is_mock=True,
            ),
        ]
        settings = Settings(**_STAGING_SAFE)
        registry = MagicMock()
        registry.statuses.return_value = statuses

        import asyncio

        result = asyncio.run(health_mod.readiness(registry=registry, settings=settings))
        assert result.ready is False
        assert result.providers_unavailable >= 2

    def test_healthy_authoritative_providers_ready(self) -> None:
        import asyncio

        from app.api.routes import health as health_mod

        statuses = [
            ProviderStatus(
                name="openai-llm",
                kind=ProviderKind.LLM,
                health=ProviderHealth.HEALTHY,
            ),
            ProviderStatus(
                name="openai-embeddings",
                kind=ProviderKind.EMBEDDINGS,
                health=ProviderHealth.HEALTHY,
            ),
            ProviderStatus(
                name="qdrant",
                kind=ProviderKind.VECTOR,
                health=ProviderHealth.HEALTHY,
            ),
            ProviderStatus(
                name="market-data",
                kind=ProviderKind.MARKET_DATA,
                health=ProviderHealth.HEALTHY,
                is_mock=True,
            ),
        ]
        settings = Settings(**_STAGING_SAFE)
        registry = MagicMock()
        registry.statuses.return_value = statuses
        result = asyncio.run(health_mod.readiness(registry=registry, settings=settings))
        assert result.ready is True
        assert result.providers_unavailable == 0


class TestRecoveryAfterRestore:
    def test_openai_recovers_when_client_works(self) -> None:
        p = OpenAILLMProvider(
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            fail_closed=True,
        )
        with patch("app.providers.llm.OpenAI") as client_cls:
            client = MagicMock()
            client.__enter__.return_value = client
            client.__exit__.return_value = False
            response = MagicMock()
            response.raise_for_status = MagicMock()
            response.model_dump.return_value = {
                "model": "gpt-4o-mini",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
            client.chat.completions.create.return_value = response
            client_cls.return_value = client
            result = p.complete(
                LLMCompletionRequest(
                    messages=[LLMMessage(role="user", content="hi")],
                    model="gpt-4o-mini",
                )
            )
        assert result.fallback_used is False
        assert result.content == "ok"

    def test_qdrant_recovers_when_client_present(self) -> None:
        store = QdrantVectorStore(
            "https://qdrant.example.com",
            vector_size=8,
            fail_closed=True,
        )
        client = MagicMock()
        client.get_collection.side_effect = Exception("missing")
        client.create_collection = MagicMock()
        client.create_payload_index = MagicMock()
        client.upsert = MagicMock()
        client.query_points.return_value = MagicMock(
            points=[
                MagicMock(
                    id="1",
                    score=0.9,
                    payload={"chunk_id": str(uuid4())},
                )
            ]
        )
        store._client = client
        store._using_qdrant = True
        point_id = str(uuid4())
        store.upsert(
            "c",
            [VectorPoint(point_id=point_id, vector=[0.1] * 8, payload={})],
        )
        hits = store.search(
            "c",
            [0.1] * 8,
            filters=VectorSearchFilters(),
            top_k=1,
        )
        assert len(hits) == 1
        client.upsert.assert_called()
