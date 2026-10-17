"""Agent adapter uses established vector search and revalidates SQL authority."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.models import Chunk, Document, UsageEvent
from app.interactive_agent.knowledge_context import build_knowledge_context
from app.interactive_agent.retrieval import retrieve_knowledge
from app.interactive_agent.vector_adapter import AgentVectorAdapter
from app.providers.embeddings import MockEmbeddingsProvider
from app.providers.qdrant import InMemoryVectorStore, VectorPoint
from app.schemas.common import DocumentSourceType
from app.services.rag_service import RAG_COLLECTION
from app.services.turn_context import turn_context
from tests import test_interactive_agent_foundation as foundation
from tests.test_interactive_agent_foundation import (
    ORG_A,
    ORG_B,
    USER_A,
    USER_A2,
    USER_B,
    _add_chunk,
)


@pytest.fixture
def retrieval_db():
    yield from foundation.agent_db.__wrapped__()


def add(session, *, user=USER_A, org=ORG_A, title="Playbook", text="quasar", **kwargs):
    row = _add_chunk(
        session, organization_id=org, user_id=user, title=title, content=text, **kwargs
    )
    document = session.get(Document, row.document_id)
    row.chunk_metadata = {"title": title, "source_type": document.source_type.value}
    return row


def test_lexical_fallback_finds_relevant_document_after_old_200_sample(retrieval_db):
    factory, _settings = retrieval_db
    with factory() as session:
        for i in range(205):
            add(session, title=f"Unrelated {i}", text="Other subject")
        relevant = add(session, title="Quasar rules", text="Quasar invalidation")
        session.commit()
        first, notes = retrieve_knowledge(
            session, organization_id=ORG_A, user_id=USER_A, query="quasar invalidation"
        )
        second, _ = retrieve_knowledge(
            session, organization_id=ORG_A, user_id=USER_A, query="quasar invalidation"
        )
        assert [h.chunk_id for h in first] == [relevant.id] == [h.chunk_id for h in second]
        assert any("lexical fallback" in n for n in notes)


@pytest.mark.parametrize("schema_only", [False, True])
def test_vector_service_preserves_shared_private_templates_and_full_passages(
    retrieval_db, monkeypatch, schema_only
):
    if schema_only:
        from app.schemas.rag import RagQuery

        class SchemaOnlyQuery(RagQuery):
            include_shared: bool = False

        # Agent 3's schema publication alone must not activate old own-only service filtering.
        monkeypatch.setattr("app.interactive_agent.vector_adapter.RagQuery", SchemaOnlyQuery)
    factory, settings = retrieval_db
    store, embeddings = InMemoryVectorStore(), MockEmbeddingsProvider()
    monkeypatch.setattr(
        "app.interactive_agent.vector_adapter.resolve_providers",
        lambda _: SimpleNamespace(embeddings=embeddings, vector_store=store),
    )
    passage = "quasar " + "reference " * 40 + "Detailed rule: wait for a confirmed reclaim."
    with factory() as session:
        own = add(session, title="My Quasar Playbook v1", text=passage)
        shared = add(
            session,
            user=None,
            title="Shared quasar template",
            source_type=DocumentSourceType.STRATEGY_TEMPLATE,
        )
        private = add(session, user=USER_A2, title="Other user's secret")
        foreign = add(session, org=ORG_B, user=USER_B, title="Other tenant")
        # A chunk claiming this tenant cannot grant access to its foreign parent.
        parent_mismatch = add(session, title="Mismatched parent")
        parent_mismatch.document_id = foreign.document_id
        parent_mismatch.ordinal = 1
        session.commit()
        rows = [own, shared, private, foreign, parent_mismatch]
        vector = embeddings.embed(["quasar"])[0]
        store.upsert(
            RAG_COLLECTION,
            [
                VectorPoint(
                    str(r.id),
                    vector,
                    {
                        "organization_id": str(r.organization_id),
                        "user_id": str(r.user_id) if r.user_id else None,
                        "source_type": r.chunk_metadata["source_type"],
                    },
                )
                for r in rows
            ]
            + [
                VectorPoint(
                    str(uuid4()),
                    vector,
                    {"organization_id": str(ORG_A), "source_type": "trading_playbook"},
                )
            ],
        )
    turn_id = uuid4()
    with turn_context(turn_id, factory):
        adapter = AgentVectorAdapter(settings, factory)
        hits = adapter.search(organization_id=ORG_A, user_id=USER_A, query="quasar", limit=5)
        # Independent adapters in one turn must meter both real embedding calls.
        AgentVectorAdapter(settings, factory).search(
            organization_id=ORG_A, user_id=USER_A, query="quasar", limit=5
        )
    assert {h.chunk_id for h in hits} == {own.id, shared.id}
    with factory() as session:
        verified, _notes = retrieve_knowledge(
            session,
            organization_id=ORG_A,
            user_id=USER_A,
            query="quasar",
            vector_retriever=SimpleNamespace(search=lambda **_: hits, notes=adapter.notes),
        )
        assert {h.chunk_id for h in verified} == {own.id, shared.id}
        assert next(h for h in verified if h.chunk_id == shared.id).user_id is None
        assert (
            next(h for h in verified if h.chunk_id == shared.id).source_type == "strategy_template"
        )
        context = build_knowledge_context(
            session,
            organization_id=ORG_A,
            user_id=USER_A,
            query="Read my Quasar Playbook v1 in full",
            hits=verified,
        )
        assert "Detailed rule: wait for a confirmed reclaim." in context.text
        usage = list(session.scalars(select(UsageEvent)))
        assert len(usage) == 2 and len({r.id for r in usage}) == 2
        assert all(r.user_id == USER_A and r.request_id == str(turn_id) for r in usage)
        # Deleting SQL invalidates a previously valid vector result.
        session.delete(session.get(Chunk, own.id))
        session.commit()
        fabricated = hits[0].model_copy(update={"document_id": uuid4()})
        invalid, notes = retrieve_knowledge(
            session,
            organization_id=ORG_A,
            user_id=USER_A,
            query="quasar",
            vector_retriever=SimpleNamespace(
                search=lambda **_: [fabricated, next(h for h in hits if h.chunk_id == own.id)],
                notes=[],
                allow_sql_fallback=False,
            ),
        )
        assert not invalid and any("outside the tenant" in n for n in notes)
        assert any("refuses degraded" in n for n in notes)
