"""Combined reviewer boundaries, with real PostgreSQL transaction interleavings."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import wraps
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.errors import ConflictError, QuotaExceededError, ServiceUnavailableError
from app.db.models import (
    ApprovalRequest,
    Chunk,
    ConversationMessage,
    Document,
    KnowledgeIndexingJob,
    ModelCallAttempt,
    Organization,
    TradeProposal,
    UsageEvent,
    User,
    UserStrategy,
)
from app.providers.llm import LLMCompletionResult, LLMMessage
from app.schemas.chat import ChatMessageRequest
from app.schemas.model_routing import ModelRoutingPurpose
from app.schemas.usage import OrganizationQuotaUpdate
from app.services.chat_turn_runtime import run_chat
from app.services.model_call_telemetry import ModelCallTelemetryService
from app.services.model_router import ModelRouter
from app.services.quota_service import QuotaService
from app.services.turn_context import turn_context
from app.services.turn_coordinator import TurnCoordinator
from tests import test_knowledge_indexing as indexing_fixtures
from tests import test_turn_coordinator_postgres as turn_fixtures
from tests.support.postgres_persistence import requires_postgres
from tests.test_interactive_agent_foundation import ORG_A, USER_A, _settings
from tests.test_knowledge_indexing import runner, store_document
from tests.test_phase2_model_router import _request
from tests.test_turn_coordinator_postgres import reserve

pytestmark = requires_postgres


@pytest.fixture
def turns_db():
    yield from turn_fixtures.turns_db.__wrapped__()


@pytest.fixture
def world():
    return indexing_fixtures.world.__wrapped__()


@pytest.mark.parametrize(
    "message",
    [
        "I confirm create strategy card for BTC pullback.",
        "Analyze BTCUSDT on 1h",
        "Prepare paper trade symbol=BTCUSDT direction=long",
    ],
)
@pytest.mark.parametrize("supersede", [False, True])
def test_superseded_retrieval_cannot_commit_domain_mutations(
    turns_db, monkeypatch, message, supersede
):
    from app.agents import nodes

    entered, release = Event(), Event()
    original = nodes.context_retrieval

    @wraps(original)
    def stalled(state, runtime):
        entered.set()
        assert release.wait(10)
        return original(state, runtime)

    monkeypatch.setattr(nodes, "context_retrieval", stalled)
    coordinator = TurnCoordinator(turns_db)
    body = ChatMessageRequest(message=message)
    reservation, _ = reserve(coordinator, channel="chat", body=body.model_dump(mode="json"))

    def work():
        with turn_context(reservation.turn_id, turns_db):
            return run_chat(coordinator, _settings(), reservation, body, None, None)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(work)
        assert entered.wait(8)
        try:
            with turns_db.begin() as session:
                row = session.get(ConversationMessage, reservation.turn_id)
                row.created_at = datetime.now(UTC) - timedelta(seconds=361)
            if supersede:
                reserve(TurnCoordinator(turns_db), conversation_id=reservation.conversation_id)
        finally:
            release.set()
        with pytest.raises(ConflictError):
            future.result(10)
    with turns_db() as session:
        for model in (UserStrategy, ApprovalRequest, TradeProposal):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        for name, table in ConversationMessage.metadata.tables.items():
            if name.startswith("agent_paper_"):
                assert session.scalar(select(func.count()).select_from(table)) == 0


def test_last_admitted_request_retry_capture_and_replay_are_one_request(turns_db):
    calls = []

    class Provider:
        name = "fixture"

        def complete(self, request):
            calls.append(request)
            if len(calls) == 1:
                raise ServiceUnavailableError("Rejected", details={"http_status": 429})
            return LLMCompletionResult(
                content="reply",
                model="unknown-model",
                provider=self.name,
                input_tokens=7,
                output_tokens=5,
            )

    with turns_db.begin() as session:
        QuotaService(session).update_quota(
            ORG_A, OrganizationQuotaUpdate(daily_request_limit=1, limit_agent_chat=1)
        )
    coordinator = TurnCoordinator(turns_db)
    key = str(uuid4())

    def work(reservation):
        router = ModelRouter(
            Provider(),
            tier_a_model="unknown-model",
            tier_b_model="unknown-model",
            telemetry=ModelCallTelemetryService(None),
        )
        for purpose in (
            ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS,
            ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS,
        ):
            request = _request(
                purpose,
                org=ORG_A,
                caller_org=ORG_A,
                user=USER_A,
                caller_user=USER_A,
                resource_id=reservation.conversation_id,
                caller_resource_id=reservation.conversation_id,
                correlation_id=str(reservation.turn_id),
            )
            router.complete(request, [LLMMessage("user", "hello")])
        return {"reply": "saved"}

    args = {
        "channel": "interactive",
        "key": key,
        "body": {"message": "hello"},
        "organization_id": ORG_A,
        "user_id": USER_A,
        "conversation_id": None,
        "work": work,
    }
    assert coordinator.run(**args) == {"reply": "saved"}
    assert len(calls) == 3
    assert TurnCoordinator(turns_db).run(**args) == {"reply": "saved"}
    with pytest.raises(QuotaExceededError):
        TurnCoordinator(turns_db).run(**{**args, "key": str(uuid4())})
    assert len(calls) == 3
    with turns_db() as session:
        status = QuotaService(session).get_status(ORG_A)
        assert status.usage.daily_requests_used == 1
        assert status.usage.feature_usage["agent_chat"] == 1
        assert session.scalar(select(func.count()).select_from(ModelCallAttempt)) == 3
        assert session.scalar(select(func.count()).select_from(UsageEvent)) == 4
        assert status.usage.monthly_tokens_used == 24


def test_claim_observation_cannot_overwrite_replacement_ready_generation(world, monkeypatch):
    from app.rag import indexing
    from app.services.rag_service import RagService

    stored = store_document(world)
    with world[0]() as session:
        old_generation = session.get(Document, stored.document_id).indexing_generation
    worker = runner(world)
    entered, release = Event(), Event()
    original = indexing.observation_metadata

    def pause(stored_metadata, job, count, **kwargs):
        value = original(stored_metadata, job, count, **kwargs)
        if job.id == old_generation:
            entered.set()
            assert release.wait(10)
        return value

    monkeypatch.setattr(indexing, "observation_metadata", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.claim)
        assert entered.wait(5)
        try:
            with world[0]() as session:
                replacement = RagService(session, vector_store=world[1]).upsert_linked_document(
                    world[2].model_copy(
                        update={"text": "Replacement rules and new ready generation."}
                    )
                )
            assert runner(world).run_once(max_jobs=1) == ["ready"]
        finally:
            release.set()
        old_claim = future.result(8)
    assert worker.process(old_claim) == "superseded"
    with world[0]() as session:
        document = session.get(Document, stored.document_id)
        new_generation = session.get(Document, replacement.document_id).indexing_generation
        assert new_generation != old_generation
        assert document.indexing_generation == new_generation
        assert document.ingestion_metadata["indexing"]["vector_index_status"] == "ready"
        assert document.ingestion_metadata["indexing"]["job_id"] == str(new_generation)
        assert session.get(KnowledgeIndexingJob, document.indexing_generation).status == "ready"


def test_combined_adapter_uses_only_permitted_ready_generations(world, monkeypatch):
    from app.interactive_agent.vector_adapter import AgentVectorAdapter
    from app.providers.embeddings import MockEmbeddingsProvider
    from app.providers.qdrant import VectorPoint, VectorSearchHit
    from app.rag.indexing import COLLECTION
    from app.schemas.common import DocumentSourceType
    from app.services.rag_service import RagService

    foreign_org, foreign_user = uuid4(), uuid4()
    with world[0].begin() as session:
        session.add(Organization(id=foreign_org, name="Foreign"))
        session.add(
            User(id=foreign_user, email=f"{foreign_user}@example.com", hashed_password="fixture")
        )
    receipts = []
    for label, org, user in [
        ("own", world[2].organization_id, world[2].user_id),
        ("shared", world[2].organization_id, None),
        ("private", world[2].organization_id, world[3]),
        ("foreign", foreign_org, foreign_user),
        ("pending", world[2].organization_id, world[2].user_id),
        ("stale", world[2].organization_id, world[2].user_id),
        ("deleted", world[2].organization_id, world[2].user_id),
    ]:
        payload = world[2].model_copy(
            update={
                "organization_id": org,
                "user_id": user,
                "title": label,
                "source_uri": f"fixture://{label}",
                "text": f"{label} quasar indexed rules",
                "source_type": DocumentSourceType.STRATEGY_TEMPLATE,
            }
        )
        with world[0]() as session:
            receipts.append(RagService(session, vector_store=world[1]).ingest(payload))
    worker = runner(world)
    assert worker.run_once(max_jobs=8) == ["ready"] * 7
    with world[0].begin() as session:
        pending = session.get(Document, receipts[4].document_id)
        pending.ingestion_metadata = {
            **pending.ingestion_metadata,
            "indexing": {
                **pending.ingestion_metadata["indexing"],
                "vector_index_status": "pending",
            },
        }
        stale = session.get(Document, receipts[5].document_id)
        stale.indexing_generation = uuid4()
        deleted = session.get(Document, receipts[6].document_id)
        for chunk in session.scalars(select(Chunk).where(Chunk.document_id == deleted.id)):
            session.delete(chunk)
        session.delete(deleted)
    points = list(world[1]._collections[COLLECTION].values())
    own = next(p for p in points if p.payload["document_id"] == str(receipts[0].document_id))
    mismatched = VectorPoint(
        str(uuid4()),
        own.vector,
        {
            **own.payload,
            "document_id": str(receipts[3].document_id),
        },
    )
    points.append(mismatched)
    # A malicious/outdated store ignores its filters; SQL must still deny these.
    monkeypatch.setattr(
        world[1],
        "search",
        lambda *args, **kwargs: [VectorSearchHit(p.point_id, 0.9, p.payload) for p in points],
    )
    monkeypatch.setattr(
        "app.interactive_agent.vector_adapter.resolve_providers",
        lambda _: SimpleNamespace(
            embeddings=MockEmbeddingsProvider(),
            vector_store=world[1],
        ),
    )
    adapter = AgentVectorAdapter(_settings(), world[0])
    found = adapter.search(
        organization_id=world[2].organization_id,
        user_id=world[2].user_id,
        query="quasar",
        limit=20,
    )
    assert {hit.document_id for hit in found} == {r.document_id for r in receipts[:2]}
    assert all(hit.source_type == "strategy_template" for hit in found)
    assert any("could not be verified" in note for note in adapter.notes)

    original_search = adapter.rag.search

    def change_readiness_after_search(*args, **kwargs):
        result = original_search(*args, **kwargs)
        with world[0].begin() as session:
            document = session.get(Document, receipts[0].document_id)
            document.ingestion_metadata = {
                **document.ingestion_metadata,
                "indexing": {
                    **document.ingestion_metadata["indexing"],
                    "vector_index_status": "pending",
                },
            }
        return result

    monkeypatch.setattr(adapter.rag, "search", change_readiness_after_search)
    found = adapter.search(
        organization_id=world[2].organization_id, user_id=world[2].user_id, query="quasar", limit=20
    )
    assert {hit.document_id for hit in found} == {receipts[1].document_id}


def test_worker_recovers_initial_qdrant_failure_with_same_job(world, monkeypatch):
    from app.providers.embeddings import MockEmbeddingsProvider
    from app.providers.qdrant import QdrantVectorStore
    from app.rag.indexing import IndexingRunner
    from app.workers.knowledge_indexing import KnowledgeIndexingCycle

    probes, clock = [], [0.0]

    class Client:
        def get_collections(self):
            probes.append(clock[0])
            if len(probes) == 1:
                raise ConnectionError("fixture outage")
            return []

    monkeypatch.setattr("qdrant_client.QdrantClient", lambda **kwargs: Client())
    monkeypatch.setattr("app.providers.qdrant.time.monotonic", lambda: clock[0])
    store = QdrantVectorStore("http://fixture.invalid", fail_closed=True)
    assert not store.using_qdrant
    stored = store_document(world)
    cycle = KnowledgeIndexingCycle(_settings())
    cycle.runner = IndexingRunner(
        world[0],
        embeddings=MockEmbeddingsProvider(),
        vector_store=store,
        settings=_settings(),
    )
    # Keep actual reconnect logic, and deterministic remote write/inventory ports.
    monkeypatch.setattr(store, "upsert", lambda *args: world[1].upsert(*args))
    monkeypatch.setattr(
        store, "document_points", lambda *args, **kwargs: world[1].document_points(*args, **kwargs)
    )
    monkeypatch.setattr(
        store,
        "delete_document_points",
        lambda *args, **kwargs: world[1].delete_document_points(*args, **kwargs),
    )
    assert not store.reconnect() and len(probes) == 1
    clock[0] = 5.0
    assert cycle().status == "success"
    assert store.using_qdrant and len(probes) == 2
    with world[0]() as session:
        document = session.get(Document, stored.document_id)
        job = session.get(KnowledgeIndexingJob, document.indexing_generation)
        assert job.status == "ready" and job.attempts == 1
        assert document.ingestion_metadata["indexing"]["vector_index_status"] == "ready"
