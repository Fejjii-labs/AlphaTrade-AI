"""Agent-owned adapter to RagService; every SQL read owns a short session.

The service keeps its established vector/embedding policy. Untrusted vector hits
are reloaded again by retrieval.py, including parent document and chunk scope.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import Chunk, Document, UsageEvent
from app.interactive_agent.contracts import KnowledgeHit, ProvenanceSource
from app.providers.embeddings import EmbeddingsProvider
from app.providers.factory import resolve_providers
from app.repositories.chunks import ChunkRepository
from app.repositories.documents import DocumentRepository
from app.schemas.common import DocumentSourceType
from app.schemas.rag import RagQuery, RagSearchResponse
from app.schemas.usage import UsageEvent as UsageEventRecord
from app.schemas.usage import UsageEventCreate
from app.services.rag_service import RagService
from app.services.turn_context import current_turn
from app.services.usage_service import UsageService, _to_event


class DetachedChunks(ChunkRepository):
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    def get_many(self, ids: list[UUID]) -> list[Chunk]:
        with self.sessions() as session:
            rows = ChunkRepository(session).get_many(ids)
            session.expunge_all()
            return rows


class DetachedDocuments(DocumentRepository):
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions

    def get(self, document_id: UUID) -> Document | None:
        with self.sessions() as session:
            row = DocumentRepository(session).get(document_id)
            session.expunge_all()
            return row


class DetachedUsage(UsageService):
    def __init__(self, sessions: Callable[[], Session]) -> None:
        super().__init__()
        self.sessions = sessions
        self.principal_id: UUID | None = None

    def record(self, data: UsageEventCreate) -> UsageEventRecord:
        turn = current_turn()
        if turn:
            data = data.model_copy(
                update={
                    "usage_event_id": turn.next_embedding_id(),
                    "request_id": str(turn.turn_id),
                    "user_id": self.principal_id or data.user_id,
                }
            )
        with self.sessions() as session:
            if data.usage_event_id and (row := session.get(UsageEvent, data.usage_event_id)):
                return _to_event(row)
            event = UsageService(session, strict_mode=True).record(data)
            session.commit()
            return event


class AgentVectorAdapter:
    def __init__(
        self,
        settings: Settings,
        sessions: Callable[[], Session],
        *,
        embeddings_provider: EmbeddingsProvider | None = None,
        usage_service: DetachedUsage | None = None,
    ) -> None:
        providers = resolve_providers(settings)
        self.usage = usage_service if usage_service is not None else DetachedUsage(sessions)
        self.rag = RagService(
            settings=settings,
            embeddings=embeddings_provider
            if embeddings_provider is not None
            else providers.embeddings,
            vector_store=providers.vector_store,
            usage_service=self.usage,
        )
        self.rag._chunks = DetachedChunks(sessions)
        self.rag._documents = DetachedDocuments(sessions)
        self.notes: list[str] = []
        self.sessions = sessions

    def search_response(self, query: RagQuery) -> RagSearchResponse:
        # Use the published shared-search capability. Older service versions retain
        # bounded compatibility search followed by independent SQL authorization.
        self.usage.principal_id = query.user_id
        supports_shared = "include_shared" in RagQuery.model_fields and bool(
            getattr(self.rag, "supports_shared_search", False)
        )
        search_query = (
            query.model_copy(update={"include_shared": True})
            if supports_shared
            else query.model_copy(update={"user_id": None, "top_k": 50})
        )
        result = self.rag.search(search_query)
        if not supports_shared:
            result = result.model_copy(
                update={
                    "detail": "Shared visibility filter upgrade pending; organization search "
                    "overfetches at most 50 hits before private/shared SQL authorization. "
                    + (result.detail or ""),
                }
            )
        permitted = []
        unverified = False
        seen: set[UUID] = set()
        with self.sessions() as session:
            from app.interactive_agent.retrieval import _load_scoped_chunk

            for hit in result.chunks:
                if query.organization_id is None or query.user_id is None:
                    continue
                loaded = _load_scoped_chunk(
                    session,
                    hit.chunk_id,
                    organization_id=query.organization_id,
                    user_id=query.user_id,
                )
                if loaded is None or loaded[1].id != hit.document_id:
                    unverified = True
                    continue
                if hit.chunk_id in seen:
                    continue
                seen.add(hit.chunk_id)
                chunk, document = loaded
                if (
                    document.indexing_generation is not None
                    and ((document.ingestion_metadata or {}).get("indexing") or {}).get(
                        "vector_index_status"
                    )
                    != "ready"
                ):
                    unverified = True
                    continue
                permitted.append(
                    hit.model_copy(
                        update={
                            "content": chunk.content,
                            "title": document.title,
                            "source_type": document.source_type,
                            "chunk_ordinal": chunk.ordinal,
                        }
                    )
                )
        permitted = permitted[: query.top_k]
        ids = {hit.chunk_id for hit in permitted}
        canonical = {hit.chunk_id: hit for hit in permitted}
        return result.model_copy(
            update={
                "chunks": permitted,
                "degraded": result.degraded or unverified,
                "detail": (
                    (result.detail or "")
                    + " Some hits could not be verified against current SQL readiness."
                )
                if unverified
                else result.detail,
                "citations": [
                    c.model_copy(
                        update={
                            "document_id": canonical[c.chunk_id].document_id,
                            "title": canonical[c.chunk_id].title,
                            "source_type": canonical[c.chunk_id].source_type,
                            "snippet": canonical[c.chunk_id].content[:240],
                        }
                    )
                    for c in result.citations
                    if c.chunk_id in ids
                ],
            }
        )

    def search(
        self, *, organization_id: UUID, user_id: UUID, query: str, limit: int
    ) -> list[KnowledgeHit]:
        result = self.search_response(
            RagQuery(
                query=query[:2000],
                organization_id=organization_id,
                user_id=user_id,
                top_k=limit,
                source_types=list(DocumentSourceType),
            )
        )
        self.notes = ["Vector retrieval uses the existing RagService and verifies SQL scope."]
        if result.detail:
            self.notes.append(result.detail)
        if result.degraded or result.fallback_used:
            self.notes.append("Vector retrieval is degraded; fallback embeddings/index were used.")
        return [
            KnowledgeHit(
                chunk_id=h.chunk_id,
                document_id=h.document_id,
                organization_id=organization_id,
                user_id=user_id,
                title=h.title or "Untitled",
                source_type=h.source_type.value,
                snippet=h.content[:240],
                match_count=0,
                provenance=ProvenanceSource.USER_SUPPLIED,
                retrieval_mode="vector",
            )
            for h in result.chunks
        ]
