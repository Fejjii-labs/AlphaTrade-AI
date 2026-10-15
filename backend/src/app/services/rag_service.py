"""RAG ingestion and retrieval service — sole boundary for knowledge base access."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ServiceUnavailableError, ValidationAppError
from app.core.provider_policy import provider_fail_closed, requires_authoritative_qdrant
from app.db.models import Chunk as ChunkModel
from app.db.models import Document as DocumentModel
from app.db.session import run_in_savepoint_when_active
from app.providers.embeddings import EmbeddingsProvider, MockEmbeddingsProvider
from app.providers.factory import resolve_providers
from app.providers.qdrant import (
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorSearchFilters,
    VectorStore,
)
from app.rag.text_processing import (
    chunk_text,
    compute_source_hash,
    compute_text_hash,
    estimate_token_count,
    stable_chunk_id,
)
from app.repositories.chunks import ChunkRepository
from app.repositories.documents import DocumentRepository
from app.schemas.common import DocumentSourceType
from app.schemas.rag import (
    ChunkMetadata,
    Citation,
    DocumentCreateRequest,
    DocumentIngestionMetadata,
    FileProvenance,
    IngestDocumentRequest,
    IngestDocumentResponse,
    RagChunk,
    RagDocument,
    RagQuery,
    RagSearchResponse,
    RetrievedChunk,
)
from app.schemas.usage import UsageEventCreate
from app.services.audit_service import AuditService
from app.services.usage_service import UsageService

logger = structlog.get_logger(__name__)

RAG_COLLECTION = "alphatrade_knowledge"


class RagService:
    """Ingest documents and retrieve grounded context with citations.

    RAG provides rules, explanations, and journal lessons only — never direct
    trading signals or order instructions.
    """

    # search supports authenticated own/private plus organization-shared scope.
    supports_shared_search = True

    def __init__(
        self,
        session: Session | None = None,
        *,
        embeddings: EmbeddingsProvider | None = None,
        vector_store: VectorStore | None = None,
        audit_service: AuditService | None = None,
        usage_service: UsageService | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._embeddings = embeddings or MockEmbeddingsProvider()
        self._vector_store = vector_store or InMemoryVectorStore()
        self._documents = DocumentRepository(session) if session is not None else None
        self._chunks = ChunkRepository(session) if session is not None else None
        self._audit = audit_service or AuditService()
        self._usage = usage_service or UsageService()

    def create_document(self, data: DocumentCreateRequest) -> RagDocument:
        """Register document metadata without body ingestion."""
        if self._documents is None or self._session is None:
            raise RuntimeError("Database session required to create documents.")
        now = datetime.now(UTC)
        entity = DocumentModel(
            id=uuid.uuid4(),
            organization_id=data.organization_id,
            user_id=data.user_id,
            source_type=data.source_type,
            title=data.title,
            uri=data.source_uri,
            version=data.version,
            source_hash=None,
            tags=[],
            created_at=now,
            updated_at=now,
        )
        self._documents.add(entity)
        self._session.commit()
        return _document_to_schema(entity)

    def ingest(
        self,
        data: IngestDocumentRequest,
        *,
        commit: bool = True,
        file_provenance: FileProvenance | None = None,
    ) -> IngestDocumentResponse:
        """Store normalized content and an indexing intent in one SQL transaction."""
        if self._documents is None or self._chunks is None or self._session is None:
            raise RuntimeError("Database session required for ingestion.")

        text_chunks = chunk_text(data.text)
        if not text_chunks:
            raise ValidationAppError("A document must contain readable text.")
        source_hash = compute_source_hash(
            title=data.title,
            text=data.text,
            source_type=data.source_type.value,
            organization_id=data.organization_id,
            user_id=data.user_id,
        )
        if file_provenance is not None:
            # Identical raw files in the same principal/category converge even
            # when renamed. Different owners and categories remain independent.
            source_hash = hashlib.sha256(
                f"file-v1|{data.organization_id}|{data.user_id}|{data.source_type.value}|"
                f"{file_provenance.raw_content_hash}".encode()
            ).hexdigest()
        existing = self._documents.get_by_source_hash(
            organization_id=data.organization_id,
            user_id=data.user_id,
            source_hash=source_hash,
        )
        if existing is None and file_provenance is None and data.user_id is not None:
            # Old hashes omitted user ID. Only the exact owner can reuse them.
            existing = self._documents.get_by_source_hash(
                organization_id=data.organization_id,
                user_id=data.user_id,
                source_hash=compute_source_hash(
                    title=data.title,
                    text=data.text,
                    source_type=data.source_type.value,
                    organization_id=data.organization_id,
                ),
            )
        if existing is not None:
            return self._duplicate_result(existing).model_copy(update={"sql_chunks_stored": commit})

        now = datetime.now(UTC)
        document_id = uuid.uuid4()
        document = DocumentModel(
            id=document_id,
            organization_id=data.organization_id,
            user_id=data.user_id,
            source_type=data.source_type,
            title=data.title,
            uri=data.source_uri,
            source_hash=source_hash,
            version=data.version,
            tags=[],
            created_at=now,
            updated_at=now,
        )
        documents = self._documents
        try:
            run_in_savepoint_when_active(self._session, lambda: documents.add(document))
        except IntegrityError:
            if not self._session.is_active:
                self._session.rollback()
            existing = self._documents.get_by_source_hash(
                organization_id=data.organization_id,
                user_id=data.user_id,
                source_hash=source_hash,
            )
            if existing is None:
                raise
            return self._duplicate_result(existing).model_copy(update={"sql_chunks_stored": commit})
        return self._store_chunks(
            document, data, text_chunks, commit=commit, file_provenance=file_provenance
        )

    def _store_chunks(
        self,
        document: DocumentModel,
        data: IngestDocumentRequest,
        text_chunks: list[str],
        *,
        commit: bool,
        file_provenance: FileProvenance | None = None,
    ) -> IngestDocumentResponse:
        from app.rag.indexing import enqueue_indexing

        assert self._session is not None and self._chunks is not None
        now = datetime.now(UTC)
        if len(text_chunks) > 2048:
            raise ValidationAppError("A document exceeds the bounded indexing chunk budget.")
        for ordinal, content in enumerate(text_chunks):
            text_hash = compute_text_hash(content)
            chunk_id = stable_chunk_id(document.id, ordinal, text_hash)
            metadata = ChunkMetadata(
                title=data.title,
                section_title=_infer_section_title(content),
                source_type=data.source_type,
                strategy_tag=data.strategy_tag,
                symbol_tag=data.symbol_tag,
                timeframe_tag=data.timeframe_tag,
                risk_tag=data.risk_tag,
                source_filename=file_provenance.filename if file_provenance else None,
            )
            self._chunks.add(
                ChunkModel(
                    id=chunk_id,
                    document_id=document.id,
                    organization_id=document.organization_id,
                    user_id=document.user_id,
                    ordinal=ordinal,
                    content=content,
                    token_count=estimate_token_count(content),
                    text_hash=text_hash,
                    embedding_ref=None,
                    chunk_metadata=metadata.model_dump(mode="json"),
                    created_at=now,
                    updated_at=now,
                )
            )
        document.ingestion_metadata = DocumentIngestionMetadata(file=file_provenance).model_dump(
            mode="json"
        )
        job = enqueue_indexing(self._session, document)
        # Admission remains metered even while remote indexing is pending.
        UsageService(self._session, strict_mode=True).record(
            UsageEventCreate(
                usage_event_id=uuid.uuid5(job.id, "content-stored"),
                request_id=f"knowledge-store:{job.id}",
                feature="rag_ingest",
                organization_id=document.organization_id,
                user_id=document.user_id,
                provider="indexing_outbox",
            )
        )
        if commit:
            self._session.commit()
        else:
            self._session.flush()
        result = self._duplicate_result(document)
        return result.model_copy(update={"duplicate": False, "sql_chunks_stored": commit})

    def _duplicate_result(self, existing: DocumentModel) -> IngestDocumentResponse:
        if self._chunks is None:
            raise RuntimeError("Database session required for duplicate inspection.")
        metadata = DocumentIngestionMetadata.model_validate(existing.ingestion_metadata or {})
        observation = metadata.indexing
        return IngestDocumentResponse(
            document_id=existing.id,
            source_hash=existing.source_hash or "",
            chunk_count=len(self._chunks.list_by_document(existing.id)),
            duplicate=True,
            version=existing.version,
            vector_backend=observation.vector_backend if observation else None,
            fallback_used=observation.fallback_used if observation else False,
            vector_index_status=observation.vector_index_status if observation else "unknown",
        )

    def upsert_linked_document(self, data: IngestDocumentRequest) -> IngestDocumentResponse:
        """Ingest or replace chunks for a stable ``source_uri`` (e.g. journal entries)."""
        if self._documents is None or self._chunks is None or self._session is None:
            raise RuntimeError("Database session required for ingestion.")
        if not data.source_uri:
            return self.ingest(data)

        if self._session.get_bind().dialect.name == "postgresql":
            from sqlalchemy import text

            scope = f"{data.organization_id}|{data.user_id}|{data.source_uri}".encode()
            key = int.from_bytes(hashlib.sha256(scope).digest()[:8], "big", signed=True)
            self._session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

        source_hash = compute_source_hash(
            title=data.title,
            text=data.text,
            source_type=data.source_type.value,
            organization_id=data.organization_id,
            user_id=data.user_id,
        )
        existing = self._documents.get_by_source_uri(
            organization_id=data.organization_id,
            source_uri=data.source_uri,
            user_id=data.user_id,
            for_update=True,
        )
        if existing is not None and existing.source_hash == source_hash:
            self._session.commit()
            return self._duplicate_result(existing)

        if existing is not None:
            for chunk in self._chunks.list_by_document(existing.id):
                self._session.delete(chunk)
            self._session.flush()
            existing.source_type = data.source_type
            existing.title = data.title
            existing.source_hash = source_hash
            existing.version = existing.version + 1
            existing.updated_at = datetime.now(UTC)
            self._documents.add(existing)
            return self._ingest_into_document(existing.id, data, source_hash=source_hash)

        return self.ingest(data)

    def _ingest_into_document(
        self,
        document_id: uuid.UUID,
        data: IngestDocumentRequest,
        *,
        source_hash: str,
    ) -> IngestDocumentResponse:
        """Chunk, embed, and persist text into an existing document row."""
        if self._documents is None or self._chunks is None or self._session is None:
            raise RuntimeError("Database session required for ingestion.")

        document = self._documents.get(document_id)
        if document is None:
            raise RuntimeError("Linked document vanished.")
        text_chunks = chunk_text(data.text)
        if not text_chunks:
            raise ValidationAppError("A document must contain readable text.")
        return self._store_chunks(document, data, text_chunks, commit=True)

    def delete_document(self, document_id: UUID, *, organization_id: UUID, user_id: UUID) -> None:
        from sqlalchemy import select

        from app.core.errors import NotFoundError
        from app.rag.indexing import enqueue_indexing

        assert self._session is not None and self._chunks is not None
        document = self._session.scalar(
            select(DocumentModel)
            .where(
                DocumentModel.id == document_id,
                DocumentModel.organization_id == organization_id,
                DocumentModel.user_id == user_id,
            )
            .with_for_update()
        )
        if document is None:
            raise NotFoundError("Knowledge document not found in your scope.")
        enqueue_indexing(self._session, document, operation="delete")
        for chunk in self._chunks.list_by_document(document.id):
            self._session.delete(chunk)
        self._session.delete(document)
        self._session.commit()

    def retry_indexing(
        self, document_id: UUID, *, organization_id: UUID, user_id: UUID
    ) -> IngestDocumentResponse:
        from sqlalchemy import select

        from app.core.errors import NotFoundError
        from app.db.models import KnowledgeIndexingJob
        from app.rag.indexing import enqueue_indexing, indexing_observation

        assert self._session is not None and self._chunks is not None
        document = self._session.scalar(
            select(DocumentModel)
            .where(
                DocumentModel.id == document_id,
                DocumentModel.organization_id == organization_id,
                DocumentModel.user_id == user_id,
            )
            .with_for_update()
        )
        if document is None:
            raise NotFoundError("Knowledge document not found in your scope.")
        job = (
            self._session.get(KnowledgeIndexingJob, document.indexing_generation)
            if document.indexing_generation
            else None
        )
        if job is not None and job.status in {"pending", "processing", "ready"}:
            return self._duplicate_result(document)
        job = enqueue_indexing(self._session, document)
        job.status, job.attempts, job.error_code = "pending", 0, None
        job.available_at, job.claim_token, job.claimed_until = datetime.now(UTC), None, None
        indexing_observation(document, job, len(self._chunks.list_by_document(document.id)))
        self._session.commit()
        return self._duplicate_result(document)

    def search(self, query: RagQuery, *, request_id: str | None = None) -> RagSearchResponse:
        """Retrieve ranked chunks with citation metadata."""
        embed_result = self._embeddings.embed_with_metadata([query.query])
        vectors = embed_result.vectors
        if provider_fail_closed(self._settings) and embed_result.fallback_used:
            raise ServiceUnavailableError(
                "Knowledge search is unavailable: embeddings degraded.",
                details={"reason": "embeddings_fallback_used"},
            )
        self._assert_vector_backend_for_search()
        self._record_embedding_usage(
            organization_id=query.organization_id,
            user_id=query.user_id,
            text_count=1,
            feature="rag_search",
            request_id=request_id,
            provider=embed_result.provider,
            input_tokens=embed_result.input_tokens,
            fallback_used=embed_result.fallback_used,
            latency_ms=embed_result.latency_ms,
        )
        filters = VectorSearchFilters(
            organization_id=query.organization_id,
            user_id=query.user_id,
            include_shared=query.include_shared,
            source_types=tuple(st.value for st in query.source_types),
            strategy_tag=query.strategy_tag,
            symbol_tag=query.symbol_tag,
            timeframe_tag=query.timeframe_tag,
            risk_tag=query.risk_tag,
        )
        try:
            hits = self._vector_store.search(
                RAG_COLLECTION,
                vectors[0],
                filters=filters,
                top_k=query.top_k,
            )
        except ServiceUnavailableError:
            raise
        except Exception as exc:
            if provider_fail_closed(self._settings):
                raise ServiceUnavailableError(
                    "Knowledge search is unavailable: vector store failed.",
                    details={"reason": "vector_search_failed"},
                ) from exc
            raise

        def hit_chunk_id(hit: Any) -> UUID | None:
            try:
                return UUID(str(hit.payload.get("chunk_id", hit.point_id)))
            except (ValueError, TypeError):
                return None

        chunk_ids = [identifier for hit in hits if (identifier := hit_chunk_id(hit)) is not None]
        chunk_map: dict[UUID, ChunkModel] = {}
        if self._chunks is not None and chunk_ids:
            for loaded_chunk in self._chunks.get_many(chunk_ids):
                chunk_map[loaded_chunk.id] = loaded_chunk

        retrieved: list[RetrievedChunk] = []
        citations: list[Citation] = []
        for hit in hits:
            chunk_id = hit_chunk_id(hit)
            if chunk_id is None:
                continue
            chunk = chunk_map.get(chunk_id)
            if chunk is None:
                continue
            if chunk.organization_id != query.organization_id:
                continue
            if chunk.user_id != query.user_id and not (
                query.include_shared and query.organization_id is not None and chunk.user_id is None
            ):
                continue
            # Repository reads support detached callers. Never require an ambient
            # Session across embedding/vector network work to verify the parent.
            document = self._documents.get(chunk.document_id) if self._documents else None
            if (
                document is None
                or document.organization_id != chunk.organization_id
                or document.user_id != chunk.user_id
                or (
                    hit.payload.get("document_id") is not None
                    and hit.payload["document_id"] != str(document.id)
                )
            ):
                continue
            if document.indexing_generation is not None:
                observation = (document.ingestion_metadata or {}).get("indexing") or {}
                if (
                    observation.get("vector_index_status") != "ready"
                    or hit.payload.get("generation") != str(document.indexing_generation)
                    or (
                        hit.payload.get("document_version") != document.version
                        or hit.payload.get("source_hash") != document.source_hash
                    )
                ):
                    continue
            metadata = _chunk_metadata_from_row(chunk)
            if metadata.source_type != document.source_type or (
                query.source_types and metadata.source_type not in query.source_types
            ):
                continue
            if any(
                getattr(query, field) is not None
                and getattr(metadata, field) != getattr(query, field)
                for field in ("strategy_tag", "symbol_tag", "timeframe_tag", "risk_tag")
            ):
                continue
            retrieved.append(
                RetrievedChunk(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    title=metadata.title,
                    section_title=metadata.section_title,
                    page_number=metadata.page_number,
                    chunk_ordinal=chunk.ordinal,
                    source_type=metadata.source_type,
                    content=chunk.content,
                    score=hit.score,
                    source_filename=metadata.source_filename,
                )
            )
            citations.append(_citation_from_chunk(chunk, metadata, score=hit.score))

        vector_status = self._vector_store.status()
        unverified_hits = len(retrieved) != len(hits)
        degraded = bool(
            embed_result.fallback_used
            or vector_status.using_fallback
            or vector_status.health.value != "healthy"
            or unverified_hits
        )
        return RagSearchResponse(
            query=query.query,
            chunks=retrieved,
            citations=citations,
            degraded=degraded,
            fallback_used=embed_result.fallback_used or vector_status.using_fallback,
            vector_backend=self._vector_store.name,
            detail=(
                "Some vector hits could not be verified against current scoped SQL content."
                if unverified_hits
                else vector_status.detail
                if degraded
                else None
            ),
        )

    def _assert_ingest_embeddings_allowed(self, fallback_used: bool) -> None:
        if not provider_fail_closed(self._settings):
            return
        if fallback_used:
            if self._session is not None:
                self._session.rollback()
            raise ServiceUnavailableError(
                "Knowledge ingestion refused: embeddings fallback is not allowed.",
                details={"reason": "embeddings_fallback_used"},
            )

    def _assert_vector_backend_for_ingest(self) -> None:
        self._assert_authoritative_qdrant(action="ingestion")

    def _assert_vector_backend_for_search(self) -> None:
        self._assert_authoritative_qdrant(action="search")

    def _assert_authoritative_qdrant(self, *, action: str) -> None:
        if not requires_authoritative_qdrant(self._settings):
            return
        store = self._vector_store
        if isinstance(store, QdrantVectorStore) and store.using_qdrant:
            return
        raise ServiceUnavailableError(
            f"Knowledge {action} refused: authoritative Qdrant is unavailable.",
            details={"reason": "qdrant_unavailable", "provider": store.name},
        )

    def retrieve_for_agent(
        self,
        *,
        query: str,
        organization_id: UUID | None,
        user_id: UUID | None,
        request_id: str | None = None,
        top_k: int = 5,
    ) -> RagSearchResponse:
        """Scoped retrieval for agent context — rules and lessons only."""
        return self.search(
            RagQuery(
                query=query,
                organization_id=organization_id,
                user_id=user_id,
                include_shared=True,
                top_k=top_k,
                source_types=[
                    DocumentSourceType.STRATEGY_TEMPLATE,
                    DocumentSourceType.TRADING_PLAYBOOK,
                    DocumentSourceType.RISK_POLICY,
                    DocumentSourceType.TRADE_JOURNAL,
                    DocumentSourceType.REVIEW_NOTE,
                    DocumentSourceType.MISTAKES_DATABASE,
                    DocumentSourceType.GENERAL_NOTE,
                ],
            ),
            request_id=request_id,
        )

    def list_documents(
        self,
        *,
        organization_id: UUID | None = None,
        user_id: UUID | None = None,
        source_type: DocumentSourceType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RagDocument], int]:
        if self._documents is None:
            return [], 0
        rows, total = self._documents.list_documents(
            organization_id=organization_id,
            user_id=user_id,
            source_type=source_type,
            limit=limit,
            offset=offset,
        )
        return [_document_to_schema(row) for row in rows], total

    def list_chunks(
        self,
        *,
        document_id: UUID | None = None,
        organization_id: UUID | None = None,
        user_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[RagChunk], int]:
        if self._chunks is None:
            return [], 0
        rows, total = self._chunks.list_chunks(
            document_id=document_id,
            organization_id=organization_id,
            user_id=user_id,
            limit=limit,
            offset=offset,
        )
        return [_chunk_to_schema(row) for row in rows], total

    def _record_embedding_usage(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID | None,
        text_count: int,
        feature: str,
        request_id: str | None = None,
        provider: str | None = None,
        input_tokens: int | None = None,
        fallback_used: bool = False,
        latency_ms: float | None = None,
    ) -> None:
        from app.schemas.common import CostSource
        from app.services.usage_cost import build_provider_metadata

        resolved_input = input_tokens if input_tokens is not None else max(text_count * 32, 1)
        self._usage.record(
            UsageEventCreate(
                request_id=request_id or "rag-local",
                organization_id=organization_id,
                user_id=user_id,
                feature=feature,
                model=self._settings.embeddings_model,
                provider=provider or self._embeddings.name,
                input_tokens=resolved_input,
                output_tokens=0,
                fallback_used=fallback_used,
                latency_ms=latency_ms,
                provider_metadata=build_provider_metadata(
                    input_tokens=resolved_input,
                    output_tokens=0,
                    cost_source=(
                        CostSource.TOKENIZER_ESTIMATED
                        if resolved_input and not fallback_used
                        else CostSource.STATIC_ESTIMATED
                    ),
                    fallback_used=fallback_used,
                    embedding_calls=text_count,
                ),
            )
        )


def build_rag_service(
    settings: Settings | None = None,
    session: Session | None = None,
    *,
    vector_store: VectorStore | None = None,
    embeddings: EmbeddingsProvider | None = None,
    audit_service: AuditService | None = None,
    usage_service: UsageService | None = None,
) -> RagService:
    """Factory used by API and agent runtime wiring."""
    settings = settings or get_settings()
    resolved = resolve_providers(settings)
    return RagService(
        session,
        settings=settings,
        embeddings=embeddings or resolved.embeddings,
        vector_store=vector_store or resolved.vector_store,
        audit_service=audit_service,
        usage_service=usage_service,
    )


def _infer_section_title(content: str) -> str | None:
    first_line = content.split("\n", 1)[0].strip()
    if first_line.endswith(":") or (first_line.isupper() and len(first_line) < 80):
        return first_line.rstrip(":")
    return None


def _vector_payload(
    *,
    chunk_id: UUID,
    document_id: UUID,
    organization_id: UUID | None,
    user_id: UUID | None,
    metadata: ChunkMetadata,
) -> dict[str, Any]:
    return {
        "chunk_id": str(chunk_id),
        "document_id": str(document_id),
        "organization_id": str(organization_id) if organization_id else None,
        "user_id": str(user_id) if user_id else None,
        "source_type": metadata.source_type.value,
        "strategy_tag": metadata.strategy_tag,
        "symbol_tag": metadata.symbol_tag,
        "timeframe_tag": metadata.timeframe_tag,
        "risk_tag": metadata.risk_tag,
    }


def _document_to_schema(entity: DocumentModel) -> RagDocument:
    return RagDocument(
        id=entity.id,
        organization_id=entity.organization_id,
        user_id=entity.user_id,
        source_type=entity.source_type,
        title=entity.title,
        source_uri=entity.uri,
        source_hash=entity.source_hash,
        ingestion_metadata=entity.ingestion_metadata,
        version=entity.version,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
    )


def _chunk_metadata_from_row(chunk: ChunkModel) -> ChunkMetadata:
    raw = chunk.chunk_metadata or {}
    return ChunkMetadata.model_validate(raw)


def _chunk_to_schema(entity: ChunkModel) -> RagChunk:
    metadata = _chunk_metadata_from_row(entity)
    return RagChunk(
        id=entity.id,
        document_id=entity.document_id,
        organization_id=entity.organization_id,
        user_id=entity.user_id,
        title=metadata.title,
        section_title=metadata.section_title,
        page_number=metadata.page_number,
        chunk_ordinal=entity.ordinal,
        content=entity.content,
        token_count=entity.token_count,
        text_hash=entity.text_hash,
        embedding_ref=entity.embedding_ref,
        metadata=metadata,
        created_at=entity.created_at,
    )


def _citation_from_chunk(
    chunk: ChunkModel,
    metadata: ChunkMetadata,
    *,
    score: float | None = None,
) -> Citation:
    snippet = chunk.content[:240] + ("..." if len(chunk.content) > 240 else "")
    return Citation(
        document_id=chunk.document_id,
        chunk_id=chunk.id,
        title=metadata.title,
        source_type=metadata.source_type,
        section_title=metadata.section_title,
        page_number=metadata.page_number,
        chunk_ordinal=chunk.ordinal,
        score=score,
        snippet=snippet,
        source_filename=metadata.source_filename,
    )
