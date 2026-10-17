"""Durable indexing fault injection; PostgreSQL exercises actual transaction boundaries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text

from app.core.config import Settings
from app.db.models import Chunk, Document, KnowledgeIndexingJob, Organization, UsageEvent, User
from app.providers.embeddings import MockEmbeddingsProvider
from app.providers.qdrant import InMemoryVectorStore, VectorPoint, VectorSearchHit
from app.rag.indexing import COLLECTION, IndexingRunner
from app.schemas.common import DocumentSourceType
from app.schemas.rag import DocumentCreateRequest, IngestDocumentRequest, RagQuery
from app.services.rag_service import RagService
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres

pytestmark = requires_postgres


@pytest.fixture
def world():
    factory = phase7_plan_session_factory()
    org, owner, other = uuid4(), uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Organization(id=org, name="Indexing test"),
                User(id=owner, email=f"{owner}@example.com", hashed_password="fixture"),
                User(id=other, email=f"{other}@example.com", hashed_password="fixture"),
            ]
        )
        session.commit()
    store = InMemoryVectorStore()
    payload = IngestDocumentRequest(
        organization_id=org,
        user_id=owner,
        source_type=DocumentSourceType.GENERAL_NOTE,
        title="Scoped knowledge",
        text="Preserve capital and require a stop.",
        source_uri="fixture://linked-note",
    )
    return factory, store, payload, other


def test_outer_rollback_does_not_write_vectors(world):
    factory, store, payload, _ = world
    with factory() as session:
        RagService(session, vector_store=store).ingest(payload, commit=False)
        session.rollback()
    with factory() as session:
        assert session.scalar(select(Document)) is None
    assert not store._collections
    with factory() as session:
        assert session.scalar(select(KnowledgeIndexingJob)) is None
        assert session.scalar(select(UsageEvent)) is None


def test_linked_same_organization_different_user_keeps_canonical_owner(world):
    factory, store, payload, other = world
    with factory() as session:
        service = RagService(session, vector_store=store)
        first = service.upsert_linked_document(payload)
        second = service.upsert_linked_document(
            payload.model_copy(
                update={
                    "user_id": other,
                    "text": "Other owner's private lesson.",
                }
            )
        )
        assert first.document_id != second.document_id
        assert session.get(Document, first.document_id).user_id == payload.user_id
        assert session.get(Document, second.document_id).user_id == other


def runner(world, **kwargs):
    return IndexingRunner(
        world[0],
        vector_store=world[1],
        embeddings=MockEmbeddingsProvider(),
        settings=Settings(_env_file=None, environment="local", provider_mode="mock"),
        **kwargs,
    )


def store_document(world, payload=None):
    with world[0]() as session:
        return RagService(session, vector_store=world[1]).ingest(payload or world[2])


def due(world):
    with world[0].begin() as session:
        for job in session.scalars(select(KnowledgeIndexingJob)):
            job.available_at = datetime.now(UTC) - timedelta(seconds=1)
            if job.status == "processing":
                job.claimed_until = job.available_at


def test_pending_storage_duplicate_and_ready_receipts(world):
    first = store_document(world)
    assert first.sql_chunks_stored and first.vector_index_status == "pending"
    assert first.vector_backend is None and not world[1]._collections
    assert store_document(world).vector_index_status == "pending"
    with world[0]() as session:
        assert len(list(session.scalars(select(KnowledgeIndexingJob)))) == 1
    assert runner(world).run_once() == ["ready"]
    repeat = store_document(world)
    assert repeat.duplicate and repeat.vector_index_status == "ready"
    with world[0]() as session:
        result = RagService(session, vector_store=world[1]).retrieve_for_agent(
            query="capital", organization_id=world[2].organization_id, user_id=world[2].user_id
        )
        assert result.chunks[0].document_id == first.document_id


def test_pending_storage_is_metered_once_and_worker_rechecks_budget(world, monkeypatch):
    from app.schemas.usage import OrganizationQuotaUpdate
    from app.services.quota_service import QuotaService

    stored = store_document(world)
    assert store_document(world).duplicate
    with world[0].begin() as session:
        events = list(session.scalars(select(UsageEvent)))
        assert len(events) == 1 and events[0].feature == "rag_ingest"
        assert events[0].provider == "indexing_outbox" and events[0].total_tokens == 0
        quota = QuotaService(session)
        quota.update_quota(world[2].organization_id, OrganizationQuotaUpdate(limit_rag_ingest=1))
        assert quota.check_feature(world[2].organization_id, "rag_ingest").hard_blocked
        quota.update_quota(world[2].organization_id, OrganizationQuotaUpdate(monthly_token_limit=0))
    worker = runner(world)
    monkeypatch.setattr(
        worker.embeddings,
        "embed_with_metadata",
        lambda _: pytest.fail("A blocked indexing budget must not call embeddings."),
    )
    assert worker.run_once() == ["pending"]
    with world[0]() as session:
        document = session.get(Document, stored.document_id)
        assert document.ingestion_metadata["indexing"]["error_code"] == "indexing_quota_exhausted"
        assert not list(
            session.scalars(select(UsageEvent).where(UsageEvent.feature == "rag_indexing"))
        )


def test_vector_outage_is_bounded_and_visible(world, monkeypatch):
    first = store_document(world)
    monkeypatch.setattr(
        world[1], "upsert", lambda *args: (_ for _ in ()).throw(ConnectionError("fixture outage"))
    )
    for attempt in range(5):
        assert runner(world).run_once() == ["failed" if attempt == 4 else "pending"]
        due(world)
    assert runner(world).run_once() == []
    with world[0]() as session:
        document = session.get(Document, first.document_id)
        observation = document.ingestion_metadata["indexing"]
        assert observation["vector_index_status"] == "failed"
        assert observation["attempts"] == 5 and observation["error_code"] == "ConnectionError"
        assert session.scalar(select(Chunk)) is not None


def test_remote_success_then_ack_failure_retries_same_points(world):
    store_document(world)
    worker = runner(world, after_write=lambda: (_ for _ in ()).throw(RuntimeError("ack fault")))
    assert worker.run_once() == ["pending"]
    identifiers = set(world[1]._collections[COLLECTION])
    due(world)
    assert runner(world).run_once() == ["ready"]
    assert set(world[1]._collections[COLLECTION]) == identifiers
    assert runner(world).run_once() == []


def test_worker_crash_restart_and_duplicate_delivery_are_safe(world):
    store_document(world)
    worker = runner(world)
    claim = worker.claim()
    assert claim is not None
    # Claiming process exits before remote work; durable lease recovers on restart.
    due(world)
    assert runner(world).run_once() == ["ready"]
    identifiers = set(world[1]._collections[COLLECTION])
    assert worker.process(claim) == "superseded"
    assert set(world[1]._collections[COLLECTION]) == identifiers


def test_crash_after_write_before_ack_recovers(world):
    class Crash(BaseException):
        pass

    store_document(world)
    worker = runner(world, after_write=lambda: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        worker.run_once()
    identifiers = set(world[1]._collections[COLLECTION])
    due(world)
    assert runner(world).run_once() == ["ready"]
    assert set(world[1]._collections[COLLECTION]) == identifiers


def test_concurrent_claims_and_session_lock_do_not_duplicate_jobs(world):
    store_document(world)
    worker = runner(world)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: worker.claim(), range(2)))
    assert sum(claim is not None for claim in claims) == 1
    claim = next(claim for claim in claims if claim is not None)
    with worker._document_lock(claim.document_id) as acquired:
        assert acquired
        for attempt in range(7):
            if attempt:
                due(world)
                claim = runner(world).claim()
                assert claim is not None
            assert runner(world).process(claim) == "pending"
            with world[0]() as session:
                job = session.scalar(select(KnowledgeIndexingJob))
                assert job.status == "pending" and job.attempts == 0
    due(world)
    assert runner(world).run_once() == ["ready"]


def test_replacement_deletion_and_stale_jobs_preserve_other_scopes(world):
    first = store_document(world)
    other = store_document(world, world[2].model_copy(update={"user_id": world[3]}))
    assert runner(world).run_once() == ["ready", "ready"]
    old = set(
        world[1].document_points(
            COLLECTION,
            document_id=first.document_id,
            organization_id=world[2].organization_id,
            user_id=world[2].user_id,
        )
    )
    with world[0]() as session:
        updated = RagService(session, vector_store=world[1]).upsert_linked_document(
            world[2].model_copy(update={"text": "Replacement lesson with a different hash."})
        )
    assert updated.document_id == first.document_id and updated.version == 2
    assert runner(world).run_once() == ["ready"]
    assert not old.intersection(world[1]._collections[COLLECTION])
    worker = runner(world)
    with world[0]() as session:
        service = RagService(session, vector_store=world[1])
        service.upsert_linked_document(world[2].model_copy(update={"text": "Third lesson."}))
    stale = worker.claim()
    with world[0]() as session:
        RagService(session, vector_store=world[1]).delete_document(
            first.document_id, organization_id=world[2].organization_id, user_id=world[2].user_id
        )
    assert worker.process(stale) == "superseded"
    assert worker.run_once() == ["ready"]
    remaining = world[1]._collections[COLLECTION].values()
    assert {point.payload["document_id"] for point in remaining} == {str(other.document_id)}


def test_replacement_during_remote_write_cannot_resurrect_old_generation(world):
    first = store_document(world)

    def replace():
        with world[0]() as session:
            RagService(session, vector_store=world[1]).upsert_linked_document(
                world[2].model_copy(update={"text": "Concurrent replacement."})
            )

    assert runner(world, after_write=replace).run_once(max_jobs=1) == ["superseded"]
    assert not world[1]._collections[COLLECTION]
    assert runner(world).run_once() == ["ready"]
    assert {
        point.payload["document_version"] for point in world[1]._collections[COLLECTION].values()
    } == {2}
    with world[0]() as session:
        assert session.get(Document, first.document_id).version == 2


def test_private_shared_templates_and_missing_sql_are_checked(world):
    own = store_document(world)
    other = store_document(world, world[2].model_copy(update={"user_id": world[3]}))
    shared = store_document(
        world,
        world[2].model_copy(
            update={"user_id": None, "source_type": DocumentSourceType.STRATEGY_TEMPLATE}
        ),
    )
    assert runner(world).run_once(max_jobs=3) == ["ready"] * 3
    with world[0]() as session:
        service = RagService(session, vector_store=world[1])
        result = service.retrieve_for_agent(
            query="capital", organization_id=world[2].organization_id, user_id=world[2].user_id
        )
        assert {row.document_id for row in result.chunks} == {own.document_id, shared.document_id}
        assert other.document_id not in {row.document_id for row in result.chunks}
        for chunk in session.scalars(select(Chunk).where(Chunk.document_id == shared.document_id)):
            session.delete(chunk)
        session.commit()
        result = service.retrieve_for_agent(
            query="capital", organization_id=world[2].organization_id, user_id=world[2].user_id
        )
        assert {row.document_id for row in result.chunks} == {own.document_id}


def test_inventory_reconciliation_repairs_missing_and_obsolete_points(world):
    document = store_document(world)
    runner(world).run_once()
    world[1]._collections[COLLECTION].clear()
    assert runner(world).reconcile_once() == 1
    assert store_document(world).vector_index_status == "pending"
    assert runner(world).run_once() == ["ready"]
    with world[0]() as session:
        assert (
            session.get(Document, document.document_id).ingestion_metadata["indexing"][
                "vector_index_status"
            ]
            == "ready"
        )


def test_actual_sql_ack_commit_failure_is_retryable(world):
    store_document(world)
    failed = False

    def before_commit(session):
        nonlocal failed
        if not failed and any(
            isinstance(row, KnowledgeIndexingJob) and row.status == "ready"
            for row in session.identity_map.values()
        ):
            failed = True
            raise RuntimeError("actual acknowledgment transaction failure")

    event.listen(world[0].class_, "before_commit", before_commit)
    try:
        assert runner(world).run_once() == ["pending"]
        assert failed
        with world[0]() as session:
            assert session.scalar(select(KnowledgeIndexingJob)).status == "pending"
        identifiers = set(world[1]._collections[COLLECTION])
        due(world)
        assert runner(world).run_once() == ["ready"]
        assert set(world[1]._collections[COLLECTION]) == identifiers
    finally:
        event.remove(world[0].class_, "before_commit", before_commit)


def test_embedding_calls_have_no_database_transaction(world, monkeypatch):
    store_document(world)
    worker = runner(world)
    original = worker.embeddings.embed_with_metadata

    def checked(texts):
        with world[0]() as session:
            idle = session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                    "AND state='idle in transaction'"
                )
            )
            assert idle == 0
        return original(texts)

    monkeypatch.setattr(worker.embeddings, "embed_with_metadata", checked)
    assert worker.run_once() == ["ready"]


def test_uncommitted_receipt_and_graceful_stop_do_not_claim_readiness(world):
    with world[0]() as session:
        service = RagService(session, vector_store=world[1])
        result = service.ingest(world[2], commit=False)
        assert not result.sql_chunks_stored and result.vector_index_status == "pending"
        duplicate = service.ingest(world[2], commit=False)
        assert duplicate.duplicate and not duplicate.sql_chunks_stored
        session.commit()
    assert runner(world, stopping=lambda: True).run_once() == []
    assert not world[1]._collections
    assert runner(world).run_once() == ["ready"]


def test_failed_retry_is_scoped_and_preserves_identity(world, monkeypatch):
    stored = store_document(world)
    with world[0].begin() as session:
        job = session.scalar(select(KnowledgeIndexingJob))
        job.status, job.attempts = "failed", 5
        original_identity = job.id
    from app.core.errors import NotFoundError

    with world[0]() as session:
        service = RagService(session, vector_store=world[1])
        with pytest.raises(NotFoundError):
            service.retry_indexing(
                stored.document_id, organization_id=world[2].organization_id, user_id=world[3]
            )
        session.rollback()
        result = service.retry_indexing(
            stored.document_id, organization_id=world[2].organization_id, user_id=world[2].user_id
        )
        assert result.vector_index_status == "pending"
    assert runner(world).run_once() == ["ready"]
    with world[0]() as session:
        assert session.scalar(select(KnowledgeIndexingJob)).id == original_identity


def test_payload_cannot_replace_sql_scope_or_source_filter(world, monkeypatch):
    own = store_document(world)
    other = store_document(world, world[2].model_copy(update={"user_id": world[3]}))
    runner(world).run_once()
    hits = [
        VectorSearchHit(
            point.point_id,
            0.9,
            {**point.payload, "user_id": str(world[2].user_id), "source_type": "risk_policy"},
        )
        for point in world[1]._collections[COLLECTION].values()
    ]
    monkeypatch.setattr(world[1], "search", lambda *args, **kwargs: hits)
    with world[0]() as session:
        service = RagService(session, vector_store=world[1])
        result = service.search(
            RagQuery(
                query="capital",
                organization_id=world[2].organization_id,
                user_id=world[2].user_id,
                source_types=[DocumentSourceType.RISK_POLICY],
            )
        )
        assert not result.chunks and result.degraded
        result = service.search(
            RagQuery(
                query="capital", organization_id=world[2].organization_id, user_id=world[2].user_id
            )
        )
        assert {row.document_id for row in result.chunks} == {own.document_id}
        assert other.document_id not in {row.document_id for row in result.chunks}
        assert result.degraded


def test_reconciliation_skips_metadata_only_and_removes_obsolete_scoped_points(world):
    with world[0]() as session:
        metadata = RagService(session, vector_store=world[1]).create_document(
            DocumentCreateRequest(
                title="Metadata only",
                source_type=DocumentSourceType.GENERAL_NOTE,
                organization_id=world[2].organization_id,
                user_id=world[2].user_id,
            )
        )
    assert runner(world).reconcile_once(limit=2) == 0
    stored = store_document(world)
    runner(world).run_once()
    existing = next(iter(world[1]._collections[COLLECTION].values()))
    stale = VectorPoint(
        str(uuid4()), existing.vector, {**existing.payload, "generation": "obsolete"}
    )
    world[1].upsert(COLLECTION, [stale])
    assert runner(world).reconcile_once(limit=2) == 1
    assert runner(world).run_once() == ["ready"]
    assert stale.point_id not in world[1]._collections[COLLECTION]
    with world[0]() as session:
        assert session.get(Document, metadata.id).indexing_generation is None
        assert session.get(Document, stored.document_id).indexing_generation is not None


def test_supported_worker_component_indexes_with_independent_health_and_shutdown(world):

    from app.workers.knowledge_indexing import KnowledgeIndexingCycle
    from app.workers.paper_worker import (
        CycleOutcome,
        PaperWorkerSupervisor,
    )

    stored = store_document(world)
    worker = runner(world)
    cycle = KnowledgeIndexingCycle(worker.settings)
    cycle.runner = worker
    supervisor = PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome("disarmed"),
        telegram_cycle=lambda: CycleOutcome("disarmed"),
        poll_seconds=0.05,
    )
    supervisor.attach_indexing(cycle, close=cycle.close, request_stop=cycle.request_stop)
    health = supervisor.run_round()
    assert health.indexing.status == "success"
    assert health.watcher.status == health.telegram.status == "disarmed"
    supervisor.start()
    supervisor.request_stop()
    supervisor.join()
    supervisor.close()
    assert supervisor.snapshot().indexing.status == "stopped"
    assert cycle._stop.is_set()
    assert store_document(world).document_id == stored.document_id


def test_opt_in_builder_connects_real_runner_without_arming_trade_components(world, monkeypatch):
    from types import SimpleNamespace

    from app.core import disarmed_worker_boot
    from app.workers import knowledge_indexing
    from app.workers.paper_worker import build_paper_worker_supervisor

    store_document(world)
    monkeypatch.setattr(
        disarmed_worker_boot, "settings_are_disarmed_paper_worker", lambda settings: True
    )
    monkeypatch.setattr(knowledge_indexing, "get_session_factory", lambda: world[0])
    monkeypatch.setattr(
        knowledge_indexing,
        "resolve_providers",
        lambda settings: SimpleNamespace(
            embeddings=MockEmbeddingsProvider(), vector_store=world[1]
        ),
    )
    settings = Settings(
        _env_file=None, environment="local", provider_mode="mock", knowledge_indexing_enabled=True
    )
    supervisor = build_paper_worker_supervisor(settings)
    health = supervisor.run_round()
    assert health.indexing.status == "success"
    assert health.watcher.status == health.telegram.status == "disarmed"
    assert not settings.enable_real_trading and not settings.watcher_paper_staging_activation
    supervisor.request_stop()
    supervisor.close()
