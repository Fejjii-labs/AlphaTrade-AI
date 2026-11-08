"""Document metadata persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, func, select

from app.db.models import Document
from app.repositories.base import SQLAlchemyRepository, require_organization_id
from app.schemas.common import DocumentSourceType


def _document_org_filter(
    organization_id: uuid.UUID | None, *, global_scope: bool
) -> ColumnElement[bool]:
    """Global (org-less) documents are reachable only when the caller asks for them (C-A4)."""
    if global_scope:
        if organization_id is not None:
            raise ValueError("global_scope lookups must not pass an organization_id.")
        return Document.organization_id.is_(None)
    return Document.organization_id == require_organization_id(organization_id)


class DocumentRepository(SQLAlchemyRepository[Document]):
    model = Document

    def get_by_source_uri(
        self,
        *,
        organization_id: uuid.UUID | None,
        source_uri: str,
        user_id: uuid.UUID | None = None,
        for_update: bool = False,
        global_scope: bool = False,
    ) -> Document | None:
        stmt = select(Document).where(Document.uri == source_uri)
        stmt = stmt.where(_document_org_filter(organization_id, global_scope=global_scope))
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
        global_scope: bool = False,
    ) -> Document | None:
        stmt = select(Document).where(Document.source_hash == source_hash)
        stmt = stmt.where(_document_org_filter(organization_id, global_scope=global_scope))
        stmt = stmt.where(
            Document.user_id.is_(None) if user_id is None else Document.user_id == user_id
        )
        return self._session.scalar(stmt)

    def list_documents(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        source_type: DocumentSourceType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Document], int]:
        filters = []
        filters.append(Document.organization_id == require_organization_id(organization_id))
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
