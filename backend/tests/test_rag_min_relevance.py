"""Minimum relevance score on every retrieval path (review item 3)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.agents.nodes import NO_RELEVANT_SOURCE_NOTE
from app.agents.runtime import AgentRuntime
from app.core.config import Settings
from app.interactive_agent.contracts import NO_RELEVANT_SOURCE_NOTE as INTERACTIVE_NOTE
from app.interactive_agent.retrieval import retrieve_knowledge
from app.providers.embeddings import MockEmbeddingsProvider
from app.providers.qdrant import InMemoryVectorStore
from app.schemas.common import DocumentSourceType
from app.schemas.rag import IngestDocumentRequest, RagQuery
from app.services.agent_service import AgentInvokeContext, AgentService
from app.services.rag_service import RagService
from app.services.risk_service import RiskService
from app.services.strategy_service import StrategyService
from app.strategies.registry import build_default_registry
from app.tools.registry import build_default_registry as build_tools
from tests.support.knowledge_indexing import drain_indexing
from tests.support.rag_sqlite import ORG_ID, USER_ID, sqlite_rag_session

POLICY = "Every trade requires a stop loss before entry."


@pytest.fixture
def session() -> Iterator[Session]:
    with sqlite_rag_session() as db:
        yield db


def _service(session: Session, floor: float) -> RagService:
    settings = Settings(log_json=False, rag_min_relevance_score=floor)
    service = RagService(
        session,
        settings=settings,
        embeddings=MockEmbeddingsProvider(),
        vector_store=InMemoryVectorStore(),
    )
    service.ingest(
        IngestDocumentRequest(
            organization_id=ORG_ID,
            user_id=USER_ID,
            source_type=DocumentSourceType.RISK_POLICY,
            title="Risk Policy",
            text=POLICY,
        )
    )
    drain_indexing(service)
    return service


def _query(text: str, **kwargs: object) -> RagQuery:
    return RagQuery(query=text, organization_id=ORG_ID, user_id=USER_ID, **kwargs)


def test_production_default_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAG_MIN_RELEVANCE_SCORE", raising=False)
    assert Settings(log_json=False).rag_min_relevance_score == 0.55


def test_hits_below_floor_are_dropped_with_explicit_outcome(session: Session) -> None:
    service = _service(session, 0.55)
    # Hash embeddings: unrelated text scores ~0.5 normalized, identical text 1.0.
    result = service.search(_query("weather forecast for the weekend"))
    assert result.chunks == [] and result.citations == []
    assert result.no_relevant_source is True
    assert result.below_min_score == 1
    assert result.min_score == 0.55


def test_hits_above_floor_are_kept(session: Session) -> None:
    result = _service(session, 0.55).search(_query(POLICY))
    assert [c.content for c in result.chunks] == [POLICY]
    assert result.chunks[0].score >= 0.55
    assert result.no_relevant_source is False


def test_request_min_score_can_raise_but_not_lower_the_floor(session: Session) -> None:
    strict_service = _service(session, 0.99)
    lowered = strict_service.search(_query("weather forecast", min_score=0.0))
    assert lowered.min_score == 0.99 and lowered.chunks == []

    with sqlite_rag_session() as other:
        open_service = _service(other, 0.0)
        assert open_service.search(_query("weather forecast")).chunks
        raised = open_service.search(_query("weather forecast", min_score=0.9))
        assert raised.min_score == 0.9 and raised.chunks == []


def test_min_score_is_bounded() -> None:
    with pytest.raises(ValueError):
        _query("x", min_score=1.5)
    with pytest.raises(ValueError):
        Settings(log_json=False, rag_min_relevance_score=-0.1)


def _agent(service: RagService) -> AgentService:
    settings = service._settings
    runtime = AgentRuntime(
        settings=settings,
        risk_service=RiskService(),
        strategy_service=StrategyService(registry=build_default_registry()),
        tool_registry=build_tools(settings, rag_service=service),
        rag_service=service,
    )
    return AgentService(runtime=runtime)


def _ctx() -> AgentInvokeContext:
    return AgentInvokeContext(request_id="floor", user_id=USER_ID, organization_id=ORG_ID)


def test_agent_reports_no_relevant_source_and_cites_nothing(session: Session) -> None:
    service = _service(session, 0.55)
    response = _agent(service).run("Explain the weekend weather outlook", _ctx())
    rag = next(o for o in response.tool_outputs if o.tool_name == "rag_retriever")
    assert rag.success and rag.result["no_relevant_source"] is True
    assert rag.result["citations"] == []
    assert NO_RELEVANT_SOURCE_NOTE in response.reply
    assert response.citations == []


def test_agent_with_relevant_source_has_no_empty_note(session: Session) -> None:
    service = _service(session, 0.0)
    response = _agent(service).run("Explain the stop loss rule", _ctx())
    rag = next(o for o in response.tool_outputs if o.tool_name == "rag_retriever")
    assert rag.result["no_relevant_source"] is False
    assert NO_RELEVANT_SOURCE_NOTE not in response.reply
    # Only retrieved chunks are cited; no session-memory pseudo-citation.
    assert response.citations
    assert {c.title for c in response.citations} == {"Risk Policy"}


class _EmptyRetriever:
    allow_sql_fallback = False
    notes = (INTERACTIVE_NOTE,)

    def search(self, **_: object) -> list:
        return []


def test_interactive_fail_closed_says_no_relevant_source_not_policy(session: Session) -> None:
    hits, limitations = retrieve_knowledge(
        session,
        organization_id=ORG_ID,
        user_id=uuid.uuid4(),
        query="weather",
        vector_retriever=_EmptyRetriever(),
    )
    assert hits == []
    assert INTERACTIVE_NOTE in limitations
    assert not any("refuses degraded retrieval" in note for note in limitations)
