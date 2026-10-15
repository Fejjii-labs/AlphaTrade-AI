"""Document metadata persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db.models import Document
from app.repositories.base import SQLAlchemyRepository
from app.schemas.common import DocumentSourceType


class DocumentRepository(SQLAlchemyRepository[Document]):
    model = Document

    def get_by_source_uri(
        self,
        *,
        organization_id: uuid.UUID | None,
        source_uri: str,
        user_id: uuid.UUID | None = None,
        for_update: bool = False,
    ) -> Document | None:
        stmt = select(Document).where(Document.uri == source_uri)
        if organization_id is None:
            stmt = stmt.where(Document.organization_id.is_(None))
        else:
            stmt = stmt.where(Document.organization_id == organization_id)
        stmt = stmt.where(
            Document.user_id.is_(None) if user_id is None else Document.user_id == user_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        return self._session.scalar(stmt)

    def get_by_source_hash(
        self,
        *,
        organization_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        source_hash: str,
    ) -> Document | None:
        stmt = select(Document).where(Document.source_hash == source_hash)
        if organization_id is None:
            stmt = stmt.where(Document.organization_id.is_(None))
        else:
            stmt = stmt.where(Document.organization_id == organization_id)
        stmt = stmt.where(
            Document.user_id.is_(None) if user_id is None else Document.user_id == user_id
        )
        return self._session.scalar(stmt)

    def list_documents(
        self,
        *,
        organization_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        source_type: DocumentSourceType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Document], int]:
        filters = []
        if organization_id is not None:
            filters.append(Document.organization_id == organization_id)
        if user_id is not None:
            filters.append(Document.user_id == user_id)
        if source_type is not None:
            filters.append(Document.source_type == source_type)

        count_stmt = select(func.count()).select_from(Document)
        list_stmt = select(Document).order_by(Document.created_at.desc())
        if filters:
            count_stmt = count_stmt.where(*filters)
            list_stmt = list_stmt.where(*filters)

        total = int(self._session.scalar(count_stmt) or 0)
        rows = list(self._session.scalars(list_stmt.limit(limit).offset(offset)).all())
        return rows, total
