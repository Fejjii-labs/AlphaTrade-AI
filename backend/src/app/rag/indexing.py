"""Durable bounded indexing. Remote work never runs inside a SQL transaction.

PostgreSQL row claims use SKIP LOCKED and fenced acknowledgments. A session-level
advisory lock serializes remote work per document without holding a transaction.
Versioned points and a SQL generation check prevent stale content being retrieved.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Engine, and_, func, or_, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.provider_policy import provider_fail_closed, requires_authoritative_qdrant
from app.db.models import Chunk, Document, KnowledgeIndexingJob
from app.providers.embeddings import EmbeddingsProvider
from app.providers.qdrant import QdrantVectorStore, VectorPoint, VectorStore
from app.schemas.rag import ChunkMetadata, DocumentIngestionMetadata, IndexingObservation

COLLECTION = "alphatrade_knowledge"
MAX_ATTEMPTS = 5
MAX_CHUNKS = 2048
BATCH_SIZE = 32
LEASE_SECONDS = 300


class IndexingError(RuntimeError):
    """A fixed diagnostic code, without provider text or uploaded content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def indexing_observation(
    document: Document,
    job: KnowledgeIndexingJob,
    count: int,
    *,
    backend: str | None = None,
    fallback: bool = False,
) -> None:
    document.ingestion_metadata = observation_metadata(
        document.ingestion_metadata, job, count, backend=backend, fallback=fallback
    )


def observation_metadata(
    stored: dict[str, Any] | None,
    job: KnowledgeIndexingJob,
    count: int,
    *,
    backend: str | None = None,
    fallback: bool = False,
) -> dict[str, Any]:
    metadata = DocumentIngestionMetadata.model_validate(stored or {})
    metadata.indexing = IndexingObservation(
        sql_chunk_count=count,
        vector_backend=backend,
        vector_index_status="pending" if job.status == "processing" else job.status,
        fallback_used=fallback,
        observed_at=datetime.now(UTC),
        job_id=job.id,
        attempts=job.attempts,
        next_attempt_at=job.available_at if job.status == "pending" else None,
        error_code=job.error_code,
    )
    return metadata.model_dump(mode="json")


def enqueue_indexing(
    session: Session, document: Document, *, operation: str = "upsert"
) -> KnowledgeIndexingJob:
    identifier = uuid5(
        document.id, f"index-v1|{document.version}|{document.source_hash}|{operation}"
    )
    job = session.get(KnowledgeIndexingJob, identifier)
    if job is None:
        job = KnowledgeIndexingJob(
            id=identifier,
            document_id=document.id,
            organization_id=document.organization_id,
            user_id=document.user_id,
            document_version=document.version,
            source_hash=document.source_hash,
            operation=operation,
            status="pending",
            attempts=0,
            available_at=datetime.now(UTC),
        )
        session.add(job)
    document.indexing_generation = identifier
    if operation == "upsert":
        count = (
            session.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id)
            )
            or 0
        )
        indexing_observation(document, job, count)
    return job


@dataclass(frozen=True)
class Claim:
    id: UUID
    token: UUID
    document_id: UUID
    organization_id: UUID | None
    user_id: UUID | None
    version: int
    source_hash: str | None
    operation: str


@dataclass(frozen=True)
class Content:
    id: UUID
    content: str
    metadata: dict[str, Any]


class IndexingRunner:
    def __init__(
        self,
        factory: sessionmaker[Session],
        *,
        embeddings: EmbeddingsProvider,
        vector_store: VectorStore,
        settings: Settings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        after_write: Callable[[], None] | None = None,
        stopping: Callable[[], bool] = lambda: False,
    ) -> None:
        self.factory, self.embeddings, self.store, self.settings = (
            factory,
            embeddings,
            vector_store,
            settings,
        )
        self.clock, self.after_write = clock, after_write
        self.stopping = stopping
        self._cursor: UUID | None = None

    def claim(self) -> Claim | None:
        now = self.clock()
        with self.factory.begin() as session:
            job = session.scalar(
                select(KnowledgeIndexingJob)
                .where(
                    or_(
                        and_(
                            KnowledgeIndexingJob.status == "pending",
                            KnowledgeIndexingJob.available_at <= now,
                        ),
                        and_(
                            KnowledgeIndexingJob.status == "processing",
                            KnowledgeIndexingJob.claimed_until <= now,
                        ),
                    )
                )
                .order_by(KnowledgeIndexingJob.available_at, KnowledgeIndexingJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            if job.attempts >= MAX_ATTEMPTS:
                job.status, job.error_code = "failed", "claim_attempts_exhausted"
                self._observe(session, job)
                return None
            job.status, job.claim_token = "processing", uuid4()
            job.attempts += 1
            job.claimed_until = now + timedelta(seconds=LEASE_SECONDS)
            self._observe(session, job)
            return Claim(
                job.id,
                job.claim_token,
                job.document_id,
                job.organization_id,
                job.user_id,
                job.document_version,
                job.source_hash,
                job.operation,
            )

    @contextmanager
    def _document_lock(self, document_id: UUID) -> Iterator[bool]:
        engine = self.factory.kw.get("bind")
        if not isinstance(engine, Engine):
            raise RuntimeError("Indexing requires an engine-bound session factory.")
        if engine.dialect.name != "postgresql":
            # SQLite supports deterministic single-runner fixtures only.
            yield True
            return
        key = int.from_bytes(hashlib.sha256(document_id.bytes).digest()[:8], "big", signed=True)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            acquired = bool(
                connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar()
            )
            try:
                yield acquired
            finally:
                if acquired:
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})

    def _current(self, document: Document | None, claim: Claim) -> bool:
        return document is not None and (
            document.organization_id,
            document.user_id,
            document.indexing_generation,
            document.version,
            document.source_hash,
        ) == (claim.organization_id, claim.user_id, claim.id, claim.version, claim.source_hash)

    def _observe(
        self,
        session: Session,
        job: KnowledgeIndexingJob,
        *,
        backend: str | None = None,
        fallback: bool = False,
    ) -> None:
        document = session.get(Document, job.document_id)
        if document is not None and document.indexing_generation == job.id:
            count = (
                session.scalar(
                    select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id)
                )
                or 0
            )
            observed = observation_metadata(
                document.ingestion_metadata, job, count, backend=backend, fallback=fallback
            )
            # Job claims/acknowledgments lock the job first. The conditional SQL
            # write takes the parent lock only at update and checks its current
            # generation atomically, including replacement committed after read.
            # Do not dirty the ORM snapshot: a later flush could undo this fence.
            session.execute(
                update(Document)
                .where(
                    Document.id == job.document_id,
                    Document.indexing_generation == job.id,
                    Document.organization_id == job.organization_id,
                    Document.user_id == job.user_id,
                    Document.version == job.document_version,
                    Document.source_hash == job.source_hash,
                )
                .values(ingestion_metadata=observed)
                .execution_options(synchronize_session=False)
            )
            session.expire(document, ["ingestion_metadata"])

    def _finish(
        self,
        claim: Claim,
        *,
        status: str,
        error: str | None = None,
        references: dict[UUID, str] | None = None,
        fallback: bool = False,
        release_attempt: bool = False,
    ) -> bool:
        with self.factory.begin() as session:
            job = session.scalar(
                select(KnowledgeIndexingJob)
                .where(
                    KnowledgeIndexingJob.id == claim.id,
                    KnowledgeIndexingJob.claim_token == claim.token,
                    KnowledgeIndexingJob.status == "processing",
                )
                .with_for_update()
            )
            if job is None:
                return False
            document = session.scalar(
                select(Document).where(Document.id == claim.document_id).with_for_update()
            )
            if claim.operation == "upsert" and not self._current(document, claim):
                status = "superseded"
            job.status, job.error_code = status, error
            job.claim_token, job.claimed_until = None, None
            if release_attempt:
                job.attempts = max(0, job.attempts - 1)
            if status == "pending":
                job.available_at = self.clock() + timedelta(seconds=min(300, 2**job.attempts))
            if status == "ready" and references and document is not None:
                for chunk in session.scalars(
                    select(Chunk).where(Chunk.document_id == claim.document_id)
                ):
                    chunk.embedding_ref = references[chunk.id]
            if status != "superseded":
                self._observe(
                    session,
                    job,
                    backend=self.store.name if status == "ready" else None,
                    fallback=fallback,
                )
            return True

    def _scope(self, claim: Claim) -> dict[str, Any]:
        return {
            "document_id": claim.document_id,
            "organization_id": claim.organization_id,
            "user_id": claim.user_id,
        }

    def process(self, claim: Claim) -> str:
        with self._document_lock(claim.document_id) as acquired:
            if not acquired:
                self._finish(claim, status="pending", error="document_busy", release_attempt=True)
                return "pending"
            try:
                with self.factory() as session:
                    job = session.get(KnowledgeIndexingJob, claim.id)
                    if job is None or job.claim_token != claim.token or job.status != "processing":
                        return "superseded"
                    document = session.get(Document, claim.document_id)
                    current = self._current(document, claim)
                    deleted = document is None
                    contents = (
                        [
                            Content(row.id, row.content, dict(row.chunk_metadata))
                            for row in session.scalars(
                                select(Chunk)
                                .where(
                                    Chunk.document_id == claim.document_id,
                                )
                                .order_by(Chunk.ordinal)
                                .limit(MAX_CHUNKS + 1)
                            )
                        ]
                        if current
                        else []
                    )
                # All SQL sessions have closed before provider calls.
                if claim.operation == "delete":
                    if not deleted:
                        self._finish(claim, status="superseded")
                        return "superseded"
                    self._assert_backend()
                    self.store.delete_document_points(
                        COLLECTION, **self._scope(claim), keep_ids=set()
                    )
                    if self.store.document_points(COLLECTION, **self._scope(claim)):
                        raise IndexingError("delete_not_verified")
                    self._finish(claim, status="ready")
                    return "ready"
                if not current:
                    self._finish(claim, status="superseded")
                    return "superseded"
                if not contents or len(contents) > MAX_CHUNKS:
                    raise IndexingError("content_budget_or_empty")
                self._assert_backend()
                references: dict[UUID, str] = {}
                fallback = False
                for start in range(0, len(contents), BATCH_SIZE):
                    if self.stopping():
                        raise IndexingError("worker_stopping")
                    with self.factory.begin() as session:
                        job = session.scalar(
                            select(KnowledgeIndexingJob)
                            .where(
                                KnowledgeIndexingJob.id == claim.id,
                                KnowledgeIndexingJob.claim_token == claim.token,
                                KnowledgeIndexingJob.status == "processing",
                            )
                            .with_for_update()
                        )
                        if job is None:
                            return "superseded"
                        job.claimed_until = self.clock() + timedelta(seconds=LEASE_SECONDS)
                    batch = contents[start : start + BATCH_SIZE]
                    if claim.organization_id is not None:
                        from app.services.quota_service import QuotaService

                        with self.factory.begin() as session:
                            budget = QuotaService(session).check_feature(
                                claim.organization_id,
                                "rag_indexing",
                                request_id=f"index:{claim.id}",
                                user_id=claim.user_id,
                            )
                        if budget.hard_blocked:
                            raise IndexingError("indexing_quota_exhausted")
                    result = self.embeddings.embed_with_metadata([row.content for row in batch])
                    from app.schemas.usage import UsageEventCreate
                    from app.services.usage_service import UsageService

                    with self.factory.begin() as session:
                        UsageService(session).record(
                            UsageEventCreate(
                                request_id=f"index:{claim.id}",
                                feature="rag_indexing",
                                organization_id=claim.organization_id,
                                user_id=claim.user_id,
                                model=result.model,
                                provider=result.provider,
                                input_tokens=result.input_tokens or max(len(batch) * 32, 1),
                                fallback_used=result.fallback_used,
                                latency_ms=result.latency_ms,
                            )
                        )
                    if provider_fail_closed(self.settings) and result.fallback_used:
                        raise IndexingError("embeddings_fallback_refused")
                    fallback |= result.fallback_used
                    points = []
                    for row, vector in zip(batch, result.vectors, strict=True):
                        metadata = ChunkMetadata.model_validate(row.metadata)
                        point_id = str(uuid5(claim.id, str(row.id)))
                        references[row.id] = point_id
                        points.append(
                            VectorPoint(
                                point_id,
                                vector,
                                {
                                    "chunk_id": str(row.id),
                                    "document_id": str(claim.document_id),
                                    "organization_id": str(claim.organization_id)
                                    if claim.organization_id
                                    else None,
                                    "user_id": str(claim.user_id) if claim.user_id else None,
                                    "generation": str(claim.id),
                                    "source_hash": claim.source_hash,
                                    "document_version": claim.version,
                                    **metadata.model_dump(mode="json"),
                                },
                            )
                        )
                    self.store.upsert(COLLECTION, points)
                    self._assert_backend()
                keep = set(references.values())
                self.store.delete_document_points(COLLECTION, **self._scope(claim), keep_ids=keep)
                if self.store.document_points(COLLECTION, **self._scope(claim)) != keep:
                    raise IndexingError("vector_write_not_verified")
                if self.after_write:
                    self.after_write()
                acknowledged = self._finish(
                    claim,
                    status="ready",
                    references=references,
                    fallback=fallback or self.store.status().using_fallback,
                )
                if not acknowledged:
                    return "pending"
                # If replaced/deleted during remote work, remove only this generation.
                with self.factory() as session:
                    still_current = self._current(session.get(Document, claim.document_id), claim)
                    document = session.get(Document, claim.document_id)
                    keep_current = (
                        {
                            str(row.embedding_ref)
                            for row in session.scalars(
                                select(Chunk).where(Chunk.document_id == claim.document_id)
                            )
                            if row.embedding_ref
                        }
                        if document
                        else set()
                    )
                if not still_current:
                    self.store.delete_document_points(
                        COLLECTION, **self._scope(claim), keep_ids=keep_current
                    )
                    return "superseded"
                return "ready"
            except Exception as exc:
                with self.factory() as session:
                    job = session.get(KnowledgeIndexingJob, claim.id)
                    exhausted = job is not None and job.attempts >= MAX_ATTEMPTS
                self._finish(
                    claim,
                    status="failed" if exhausted else "pending",
                    error=exc.code if isinstance(exc, IndexingError) else type(exc).__name__,
                )
                return "failed" if exhausted else "pending"

    def _assert_backend(self) -> None:
        if requires_authoritative_qdrant(self.settings) and not (
            isinstance(self.store, QdrantVectorStore)
            and self.store.using_qdrant
            and not self.store.status().using_fallback
        ):
            raise IndexingError("authoritative_qdrant_unavailable")

    def run_once(self, *, max_jobs: int = 2) -> list[str]:
        if not 1 <= max_jobs <= 8:
            raise ValueError("Indexing cycle budget must be 1..8 jobs.")
        outcomes = []
        for _ in range(max_jobs):
            if self.stopping():
                break
            claim = self.claim()
            if claim is None:
                break
            outcomes.append(self.process(claim))
        return outcomes

    def queue_counts(self) -> dict[str, int]:
        with self.factory() as session:
            return {
                status: int(count)
                for status, count in session.execute(
                    select(KnowledgeIndexingJob.status, func.count()).group_by(
                        KnowledgeIndexingJob.status
                    )
                )
            }

    def reconcile_once(self, *, limit: int = 2) -> int:
        """Bounded rolling inventory of ready/legacy rows; no tenant-wide reset."""
        if not 1 <= limit <= 8:
            raise ValueError("Reconciliation budget must be 1..8 documents.")
        with self.factory() as session:
            query = select(Document).order_by(Document.id).limit(limit)
            if self._cursor is not None:
                query = query.where(Document.id > self._cursor)
            documents = list(session.scalars(query))
            snapshots = []
            for row in documents:
                chunks = list(
                    session.scalars(
                        select(Chunk).where(Chunk.document_id == row.id).limit(MAX_CHUNKS + 1)
                    )
                )
                snapshots.append(
                    (
                        row.id,
                        row.organization_id,
                        row.user_id,
                        row.indexing_generation,
                        (row.ingestion_metadata or {}).get("indexing") or {},
                        {str(chunk.embedding_ref) for chunk in chunks if chunk.embedding_ref},
                        len(chunks),
                    )
                )
        self._cursor = snapshots[-1][0] if snapshots else None
        repaired = 0
        for identifier, organization, user, generation, observation, expected, count in snapshots:
            if not count and generation is None:
                continue
            if generation is not None and observation.get("vector_index_status") != "ready":
                continue
            with self._document_lock(identifier) as acquired:
                if not acquired:
                    continue
                actual = self.store.document_points(
                    COLLECTION, document_id=identifier, organization_id=organization, user_id=user
                )
                if generation is not None and expected and actual == expected:
                    continue
                with self.factory.begin() as session:
                    document = session.scalar(
                        select(Document).where(Document.id == identifier).with_for_update()
                    )
                    if document is None or document.indexing_generation != generation:
                        continue
                    job = enqueue_indexing(session, document)
                    job.status, job.attempts, job.error_code = "pending", 0, "inventory_mismatch"
                    job.claim_token, job.claimed_until, job.available_at = None, None, self.clock()
                    self._observe(session, job)
                    repaired += 1
        return repaired
