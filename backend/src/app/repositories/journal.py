"""Trade journal persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db.models import TradeJournal
from app.repositories.base import SQLAlchemyRepository, require_organization_id


class JournalRepository(SQLAlchemyRepository[TradeJournal]):
    model = TradeJournal

    def list_entries(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TradeJournal], int]:
        filters = []
        filters.append(TradeJournal.organization_id == require_organization_id(organization_id))
        if user_id is not None:
            filters.append(TradeJournal.user_id == user_id)

        count_stmt = select(func.count()).select_from(TradeJournal)
        list_stmt = select(TradeJournal).order_by(TradeJournal.created_at.desc())
        if filters:
            count_stmt = count_stmt.where(*filters)
            list_stmt = list_stmt.where(*filters)
        total = int(self._session.scalar(count_stmt) or 0)
        return list(self._session.scalars(list_stmt.limit(limit).offset(offset)).all()), total

    def get_scoped(
        self,
        entry_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> TradeJournal | None:
        stmt = select(TradeJournal).where(TradeJournal.id == entry_id)
        stmt = stmt.where(TradeJournal.organization_id == require_organization_id(organization_id))
        if user_id is not None:
            stmt = stmt.where(TradeJournal.user_id == user_id)
        return self._session.scalar(stmt)
