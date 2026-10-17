"""Knowledge and strategy retrieval over existing stores.

Lexical search reads `documents` and `chunks`. Optional vector hits are
accepted only when the chunk row reloads inside the caller's tenant.
"""

from __future__ import annotations

import uuid
from typing import Literal, Protocol

import structlog
from sqlalchemy import case, literal, or_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models import Chunk, Document
from app.interactive_agent.contracts import KnowledgeHit, ProvenanceSource, StrategyHit
from app.interactive_agent.parsing import query_tokens
from app.schemas.common import DocumentSourceType
from app.schemas.strategy_library import UserStrategy
from app.services.strategy_library_service import StrategyLibraryService
from app.services.strategy_versioning import StrategyVersioningService

logger = structlog.get_logger(__name__)

_SCAN_LIMIT = 200
_USER_SOURCE_TYPES = frozenset(
    {
        DocumentSourceType.TRADE_JOURNAL.value,
        DocumentSourceType.REVIEW_NOTE.value,
        DocumentSourceType.MISTAKES_DATABASE.value,
        DocumentSourceType.GENERAL_NOTE.value,
        DocumentSourceType.STRATEGY_TEMPLATE.value,
    }
)


class VectorKnowledgeRetriever(Protocol):
    """Optional adapter. Implementations must not be treated as tenant-safe."""

    def search(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        query: str,
        limit: int,
    ) -> list[KnowledgeHit]: ...


def provenance_for_source(source_type: str) -> ProvenanceSource:
    """Map a stored document source to content provenance."""
    if source_type in _USER_SOURCE_TYPES:
        return ProvenanceSource.USER_SUPPLIED
    return ProvenanceSource.SYSTEM_GENERATED


def provenance_for_document(document: Document) -> ProvenanceSource:
    """User-owned ingestion is reference content supplied by that principal."""
    if document.user_id is not None:
        return ProvenanceSource.USER_SUPPLIED
    return provenance_for_source(document.source_type.value)


def _snippet(content: str) -> str:
    compact = " ".join(content.split())
    return compact[:240]


def _hit_from_row(
    chunk: Chunk,
    document: Document,
    *,
    mode: str,
    match_count: int,
) -> KnowledgeHit | None:
    if document.organization_id is None or chunk.organization_id is None:
        return None
    title = document.title.strip() or "Untitled"
    snippet = _snippet(chunk.content)
    source = document.source_type
    source_name = source.value if isinstance(source, DocumentSourceType) else str(source)
    retrieval_mode: Literal["lexical_store", "vector"] = (
        "vector" if mode == "vector" else "lexical_store"
    )
    return KnowledgeHit(
        chunk_id=chunk.id,
        document_id=document.id,
        organization_id=document.organization_id,
        user_id=document.user_id,
        title=title[:255],
        source_type=source_name,
        snippet=snippet,
        match_count=match_count,
        provenance=provenance_for_document(document),
        retrieval_mode=retrieval_mode,
    )


def _load_scoped_chunk(
    session: Session,
    chunk_id: uuid.UUID,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[Chunk, Document] | None:
    chunk = session.get(Chunk, chunk_id)
    if chunk is None or chunk.organization_id != organization_id:
        return None
    if chunk.user_id is not None and chunk.user_id != user_id:
        return None
    document = session.get(Document, chunk.document_id)
    if document is None or document.organization_id != organization_id:
        return None
    if document.user_id is not None and document.user_id != user_id:
        return None
    return chunk, document


def _lexical_hits(
    session: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    tokens: set[str],
    limit: int,
) -> list[KnowledgeHit]:
    stmt = (
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.organization_id == organization_id)
        .where(Chunk.organization_id == organization_id)
        .where(or_(Document.user_id.is_(None), Document.user_id == user_id))
        .where(or_(Chunk.user_id.is_(None), Chunk.user_id == user_id))
        .where(
            or_(
                *[
                    field.ilike(f"%{token}%", escape="\\")
                    for token in sorted(tokens)[:12]
                    for field in (Document.title, Chunk.content)
                ]
            )
        )
        .order_by(
            sum(
                (
                    case(
                        (
                            or_(
                                Document.title.ilike(f"%{token}%"),
                                Chunk.content.ilike(f"%{token}%"),
                            ),
                            1,
                        ),
                        else_=0,
                    )
                    for token in sorted(tokens)[:12]
                ),
                literal(0),
            ).desc(),
            Document.title,
            Chunk.ordinal,
            Chunk.id,
        )
        .limit(_SCAN_LIMIT)
    )
    ranked: list[tuple[int, KnowledgeHit]] = []
    for chunk, document in session.execute(stmt).all():
        overlap = query_tokens(f"{document.title} {chunk.content}") & tokens
        if not overlap:
            continue
        hit = _hit_from_row(chunk, document, mode="lexical_store", match_count=len(overlap))
        if hit is not None:
            ranked.append((len(overlap), hit))
    ranked.sort(key=lambda item: (-item[0], item[1].title))
    return [hit for _, hit in ranked[:limit]]


def retrieve_knowledge(
    session: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    query: str,
    limit: int = 5,
    vector_retriever: VectorKnowledgeRetriever | None = None,
) -> tuple[list[KnowledgeHit], list[str]]:
    """Return tenant-scoped knowledge hits and honest retrieval limitations."""
    limitations: list[str] = []
    tokens = query_tokens(query)
    if vector_retriever is not None:
        verified, notes = _verified_vector_hits(
            session,
            retriever=vector_retriever,
            organization_id=organization_id,
            user_id=user_id,
            query=query,
            tokens=tokens,
            limit=limit,
        )
        limitations.extend(notes)
        limitations.extend(getattr(vector_retriever, "notes", []))
        if verified:
            return verified, limitations
        if not getattr(vector_retriever, "allow_sql_fallback", True):
            limitations.append(
                "Provider policy refuses degraded retrieval; source evidence is unavailable."
            )
            return [], limitations
    if not tokens:
        return [], limitations
    limitations.append(
        "Deterministic lexical fallback filters the full scoped store, then ranks "
        "at most 200 matching candidates; vector relevance is unavailable."
    )
    return (
        _lexical_hits(
            session,
            organization_id=organization_id,
            user_id=user_id,
            tokens=tokens,
            limit=limit,
        ),
        limitations,
    )


def _verified_vector_hits(
    session: Session,
    *,
    retriever: VectorKnowledgeRetriever,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    query: str,
    tokens: set[str],
    limit: int,
) -> tuple[list[KnowledgeHit], list[str]]:
    try:
        raw = retriever.search(
            organization_id=organization_id,
            user_id=user_id,
            query=query,
            limit=limit,
        )
    except Exception:
        logger.warning("interactive_agent_vector_retrieval_failed")
        return [], ["Vector retrieval failed. Lexical chunk search was used."]
    verified: list[KnowledgeHit] = []
    seen: set[uuid.UUID] = set()
    dropped = False
    for hit in raw:
        loaded = _load_scoped_chunk(
            session,
            hit.chunk_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if loaded is None or loaded[1].id != hit.document_id:
            dropped = True
            continue
        if hit.chunk_id in seen:
            continue
        seen.add(hit.chunk_id)
        chunk, document = loaded
        overlap = len(query_tokens(chunk.content) & tokens)
        rebuilt = _hit_from_row(chunk, document, mode="vector", match_count=overlap)
        if rebuilt is None:
            dropped = True
            continue
        verified.append(rebuilt)
    notes = ["Vector hits were reloaded from the chunk store before use."]
    if dropped:
        notes.append("Vector hits outside the tenant knowledge store were dropped.")
    return verified[:limit], notes


def _strategy_summary(
    name: str, setup_type: str, status: str | None, lifecycle: str | None, entry: str | None
) -> str:
    text = (
        f"{name} ({setup_type}; lifecycle={lifecycle or 'unavailable'}; "
        f"research_validation={status or 'unspecified'})"
    )
    if entry:
        text = f"{text}: {entry}"
    return text[:300]


def retrieve_strategies(
    session: Session,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    query: str,
    list_all: bool,
    strategy_id: uuid.UUID | None,
    limit: int = 8,
) -> tuple[list[StrategyHit], list[str]]:
    """Read the caller's strategy library. This does not create or update rows."""
    limitations: list[str] = []
    library = StrategyLibraryService(session)
    matched_rows: list[UserStrategy] = []
    seen: set[uuid.UUID] = set()
    if strategy_id is not None:
        try:
            bound = library.get(strategy_id, organization_id=organization_id, user_id=user_id)
        except NotFoundError:
            limitations.append("The requested strategy is not in this tenant.")
        else:
            matched_rows.append(bound)
            seen.add(bound.id)
    rows, total = library.list_strategies(
        organization_id=organization_id,
        user_id=user_id,
        limit=50,
        offset=0,
    )
    if total > len(rows):
        limitations.append("Strategy retrieval scanned the 50 most recently updated strategies.")
    tokens = query_tokens(query)
    for row in rows:
        if row.id in seen:
            continue
        blob = " ".join(
            part for part in (row.name, row.setup_type.value, row.notes or "", row.name) if part
        )
        if row.latest_card is not None:
            blob = " ".join(
                [blob, *row.latest_card.entry_conditions, *row.latest_card.invalidation]
            )
        matched = list_all or bool(query_tokens(blob) & tokens)
        if not matched:
            continue
        matched_rows.append(row)
        seen.add(row.id)
    # Interleave families so recently updated Nested variants cannot hide all SFP rules.
    groups: dict[str, list[UserStrategy]] = {}
    for row in matched_rows:
        groups.setdefault(row.setup_type.value, []).append(row)
    balanced: list[UserStrategy] = []
    while any(groups.values()) and len(balanced) < limit:
        for group in groups.values():
            if group and len(balanced) < limit:
                balanced.append(group.pop(0))
    return [_to_strategy_hit(session, row) for row in balanced], limitations


def _to_strategy_hit(session: Session, item: UserStrategy) -> StrategyHit:
    entry = None
    if item.latest_card is not None and item.latest_card.entry_conditions:
        entry = item.latest_card.entry_conditions[0][:160]
    status = item.validation_status.value if item.validation_status is not None else None
    versioning = StrategyVersioningService(session)
    row = versioning.require_strategy(
        item.id, organization_id=item.organization_id, user_id=item.user_id
    )
    version = versioning.selected_version(row)
    event = versioning.latest_lifecycle_event_for_version(version.id) if version else None
    lifecycle = (
        event.new_state.value if event and event.organization_id == item.organization_id else None
    )
    return StrategyHit(
        strategy_id=item.id,
        name=item.name,
        setup_type=item.setup_type.value,
        version=item.current_version,
        validation_status=status,
        lifecycle_status=lifecycle,
        selected_version_id=version.id if version else None,
        paper_eligible=item.paper_eligible,
        summary=_strategy_summary(item.name, item.setup_type.value, status, lifecycle, entry),
    )
