"""RAG foundation tests (Slice 12)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.runtime import AgentRuntime
from app.core.config import Settings
from app.db.base import Base
from app.providers.embeddings import MockEmbeddingsProvider
from app.providers.qdrant import (
    InMemoryVectorStore,
    VectorPoint,
    VectorSearchFilters,
    reset_process_vector_store,
)
from app.rag.text_processing import (
    chunk_text,
    compute_source_hash,
    compute_text_hash,
    normalize_text,
    stable_chunk_id,
)
from app.schemas.common import DocumentSourceType, RiskAction
from app.schemas.rag import Citation, IngestDocumentRequest, RagQuery
from app.services.agent_service import AgentInvokeContext, AgentService
from app.services.rag_service import RagService, build_rag_service
from app.services.risk_service import RiskService
from app.services.strategy_service import StrategyService
from app.strategies.registry import build_default_registry
from app.tools.registry import ToolRegistry
from app.tools.registry import build_default_registry as build_tools
from tests.support.knowledge_indexing import drain_indexing

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000021")


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

        org = Organization(id=ORG_ID, name="RAG Test Org")
        user = User(id=USER_ID, email="rag@test.example", hashed_password="hash")
        session.add_all([org, user])
        session.commit()
        yield session
    reset_process_vector_store()
    engine.dispose()


@pytest.fixture
def rag_service(db_session: Session) -> RagService:
    return build_rag_service(Settings(log_json=False), db_session)


def test_normalize_text_collapses_whitespace() -> None:
    raw = "  Capital   preservation \r\n\r\n first.  \n  Always.  "
    assert normalize_text(raw) == "Capital preservation\n\nfirst. Always."


def test_chunk_text_splits_long_paragraphs() -> None:
    text = "A" * 1200
    chunks = chunk_text(text, chunk_size=400, overlap=40)
    assert len(chunks) >= 2
    assert all(len(chunk) <= 400 for chunk in chunks)


def test_stable_source_hash_is_deterministic() -> None:
    kwargs = {
        "title": "Risk Policy",
        "text": "Require stop loss.",
        "source_type": "risk_policy",
        "organization_id": ORG_ID,
    }
    assert compute_source_hash(**kwargs) == compute_source_hash(**kwargs)


def test_document_ingestion_persists_chunks(rag_service: RagService, db_session: Session) -> None:
    result = rag_service.ingest(
        IngestDocumentRequest(
            organization_id=ORG_ID,
            user_id=USER_ID,
            source_type=DocumentSourceType.RISK_POLICY,
            title="Risk Policy",
            text="Capital preservation first. Every trade requires a stop loss.",
            risk_tag="stop_loss",
        )
    )
    db_session.commit()
    drain_indexing(rag_service)
    assert result.chunk_count >= 1
    assert result.duplicate is False

    chunks, total = rag_service.list_chunks(document_id=result.document_id)
    assert total == result.chunk_count
    assert chunks[0].metadata.risk_tag == "stop_loss"
    assert chunks[0].text_hash == compute_text_hash(chunks[0].content)


def test_duplicate_source_hash_returns_existing(
    rag_service: RagService, db_session: Session
) -> None:
    payload = IngestDocumentRequest(
        organization_id=ORG_ID,
        source_type=DocumentSourceType.RISK_POLICY,
        title="Risk Policy",
        text="Duplicate protection test body.",
    )
    first = rag_service.ingest(payload)
    db_session.commit()
    drain_indexing(rag_service)
    second = rag_service.ingest(payload)
    assert second.duplicate is True
    assert second.document_id == first.document_id


def test_mock_embedding_provider_is_deterministic() -> None:
    provider = MockEmbeddingsProvider(dimensions=8)
    first = provider.embed(["hello world"])
    second = provider.embed(["hello world"])
    assert first == second
    assert len(first[0]) == 8


def test_in_memory_vector_store_upsert_and_search() -> None:
    store = InMemoryVectorStore()
    provider = MockEmbeddingsProvider(dimensions=8)
    vector = provider.embed(["capital preservation"])[0]
    store.upsert(
        "test",
        [
            VectorPoint(
                point_id="p1",
                vector=vector,
                payload={
                    "organization_id": str(ORG_ID),
                    "source_type": DocumentSourceType.RISK_POLICY.value,
                },
            )
        ],
    )
    hits = store.search(
        "test",
        vector,
        filters=VectorSearchFilters(organization_id=ORG_ID),
        top_k=1,
    )
    assert hits
    assert hits[0].point_id == "p1"


def test_retrieval_with_source_type_filter(rag_service: RagService, db_session: Session) -> None:
    rag_service.ingest(
        IngestDocumentRequest(
            organization_id=ORG_ID,
            source_type=DocumentSourceType.RISK_POLICY,
            title="Risk Policy",
            text="Stop loss is mandatory for every trade.",
        )
    )
    rag_service.ingest(
        IngestDocumentRequest(
            organization_id=ORG_ID,
            source_type=DocumentSourceType.TRADING_PLAYBOOK,
            title="Trading Playbook",
            text="Human approval is required before execution.",
        )
    )
    db_session.commit()
    drain_indexing(rag_service)

    result = rag_service.search(
        RagQuery(
            query="stop loss requirement",
            organization_id=ORG_ID,
            source_types=[DocumentSourceType.RISK_POLICY],
            top_k=3,
        )
    )
    assert result.chunks
    assert all(chunk.source_type is DocumentSourceType.RISK_POLICY for chunk in result.chunks)


def test_retrieval_with_strategy_and_risk_tag_filters(
    rag_service: RagService, db_session: Session
) -> None:
    rag_service.ingest(
        IngestDocumentRequest(
            organization_id=ORG_ID,
            source_type=DocumentSourceType.STRATEGY_TEMPLATE,
            title="HTF Pullback",
            text="Pullback entry aligned with higher timeframe trend.",
            strategy_tag="htf_trend_pullback",
        )
    )
    rag_service.ingest(
        IngestDocumentRequest(
            organization_id=ORG_ID,
            source_type=DocumentSourceType.RISK_POLICY,
            title="Risk Policy",
            text="Stop loss required on all trades.",
            risk_tag="stop_loss",
        )
    )
    db_session.commit()
    drain_indexing(rag_service)

    strategy_result = rag_service.search(
        RagQuery(
            query="pullback entry",
            organization_id=ORG_ID,
            strategy_tag="htf_trend_pullback",
            top_k=3,
        )
    )
    assert strategy_result.chunks
    assert strategy_result.chunks[0].source_type is DocumentSourceType.STRATEGY_TEMPLATE

    risk_result = rag_service.search(
        RagQuery(
            query="stop loss",
            organization_id=ORG_ID,
            risk_tag="stop_loss",
            top_k=3,
        )
    )
    assert risk_result.chunks
    assert risk_result.chunks[0].source_type is DocumentSourceType.RISK_POLICY


def test_citation_object_creation(rag_service: RagService, db_session: Session) -> None:
    rag_service.ingest(
        IngestDocumentRequest(
            organization_id=ORG_ID,
            source_type=DocumentSourceType.TRADING_PLAYBOOK,
            title="Trading Playbook",
            text="Capital preservation first. Require human approval.",
        )
    )
    db_session.commit()
    drain_indexing(rag_service)
    result = rag_service.search(
        RagQuery(query="capital preservation", organization_id=ORG_ID, top_k=1)
    )
    assert result.citations
    citation = result.citations[0]
    assert isinstance(citation, Citation)
    assert citation.document_id
    assert citation.chunk_id
    assert citation.title == "Trading Playbook"
    assert citation.chunk_ordinal is not None


def test_rag_tool_calls_rag_service(rag_service: RagService, db_session: Session) -> None:
    rag_service.ingest(
        IngestDocumentRequest(
            organization_id=ORG_ID,
            source_type=DocumentSourceType.RISK_POLICY,
            title="Risk Policy",
            text="Stop loss is required.",
        )
    )
    db_session.commit()
    drain_indexing(rag_service)
    registry: ToolRegistry = build_tools(rag_service=rag_service)
    output = registry.execute(
        "rag_retriever",
        {
            "query": "stop loss",
            "organization_id": str(ORG_ID),
        },
    )
    assert output.success
    assert output.result is not None
    assert output.result.get("not_trading_signal") is True
    assert output.result.get("citations")


def test_agent_context_retrieval_populates_citations(
    rag_service: RagService, db_session: Session
) -> None:
    rag_service.ingest(
        IngestDocumentRequest(
            organization_id=ORG_ID,
            source_type=DocumentSourceType.TRADING_PLAYBOOK,
            title="Trading Playbook",
            text="Capital preservation and human approval are mandatory.",
        )
    )
    db_session.commit()
    drain_indexing(rag_service)

    settings = Settings(log_json=False, execution_mode="paper", enable_real_trading=False)
    runtime = AgentRuntime(
        settings=settings,
        risk_service=RiskService(),
        strategy_service=StrategyService(registry=build_default_registry()),
        tool_registry=build_tools(settings, rag_service=rag_service),
        rag_service=rag_service,
    )
    service = AgentService(runtime=runtime)
    response = service.run(
        "Explain the capital preservation rule",
        AgentInvokeContext(
            request_id="rag-test",
            user_id=USER_ID,
            organization_id=ORG_ID,
        ),
    )
    rag_outputs = [o for o in response.tool_outputs if o.tool_name == "rag_retriever"]
    assert rag_outputs
    assert rag_outputs[0].success
    assert isinstance(response.citations, list)


def test_rag_does_not_bypass_risk_engine_or_create_trading_signal(
    rag_service: RagService, db_session: Session
) -> None:
    rag_service.ingest(
        IngestDocumentRequest(
            organization_id=ORG_ID,
            source_type=DocumentSourceType.TRADING_PLAYBOOK,
            title="Trading Playbook",
            text="Never bypass risk engine. RAG context is not a trading signal.",
        )
    )
    db_session.commit()
    drain_indexing(rag_service)

    settings = Settings(log_json=False, execution_mode="paper", enable_real_trading=False)
    runtime = AgentRuntime(
        settings=settings,
        risk_service=RiskService(),
        strategy_service=StrategyService(registry=build_default_registry()),
        tool_registry=build_tools(settings, rag_service=rag_service),
        rag_service=rag_service,
    )
    service = AgentService(runtime=runtime)
    response = service.run(
        "Plan trade BTC long [test_no_stop]",
        AgentInvokeContext(
            request_id="rag-safety",
            user_id=USER_ID,
            organization_id=ORG_ID,
        ),
        symbol="BTCUSDT",
    )
    assert response.risk_result is not None
    assert response.risk_result.action is RiskAction.BLOCK
    rag_outputs = [o for o in response.tool_outputs if o.tool_name == "rag_retriever"]
    if rag_outputs and rag_outputs[0].result:
        assert rag_outputs[0].result.get("not_trading_signal") is True
        assert "signal" not in rag_outputs[0].result


def test_stable_chunk_id_is_deterministic() -> None:
    doc_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    text_hash = compute_text_hash("sample chunk")
    assert stable_chunk_id(doc_id, 0, text_hash) == stable_chunk_id(doc_id, 0, text_hash)
