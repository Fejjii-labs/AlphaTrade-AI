"""Chunk metadata persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db.models import Chunk
from app.repositories.base import SQLAlchemyRepository, require_organization_id


class ChunkRepository(SQLAlchemyRepository[Chunk]):
    model = Chunk

    def list_by_document(self, document_id: uuid.UUID) -> list[Chunk]:
        stmt = select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.ordinal)
        return list(self._session.scalars(stmt).all())

    def list_chunks(
        self,
        *,
        organization_id: uuid.UUID,
        document_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Chunk], int]:
        filters = [Chunk.organization_id == require_organization_id(organization_id)]
        if document_id is not None:
            filters.append(Chunk.document_id == document_id)
        if user_id is not None:
            filters.append(Chunk.user_id == user_id)

        count_stmt = select(func.count()).select_from(Chunk)
        list_stmt = select(Chunk).order_by(Chunk.created_at.desc(), Chunk.ordinal)
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)

        total = int(self._session.scalar(count_stmt) or 0)
        rows = list(self._session.scalars(list_stmt.limit(limit).offset(offset)).all())
        return rows, total

    def get_many(self, chunk_ids: list[uuid.UUID]) -> list[Chunk]:
        if not chunk_ids:
            return []
        stmt = select(Chunk).where(Chunk.id.in_(chunk_ids))
        return list(self._session.scalars(stmt).all())
