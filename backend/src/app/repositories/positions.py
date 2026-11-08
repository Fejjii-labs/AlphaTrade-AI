"""Position persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db.models import Position
from app.repositories.base import SQLAlchemyRepository, require_organization_id
from app.schemas.common import PositionStatus


class PositionRepository(SQLAlchemyRepository[Position]):
    model = Position

    def list_positions(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        status: PositionStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Position], int]:
        filters = []
        filters.append(Position.organization_id == require_organization_id(organization_id))
        if user_id is not None:
            filters.append(Position.user_id == user_id)
        if status is not None:
            filters.append(Position.status == status)

        count_stmt = select(func.count()).select_from(Position)
        list_stmt = select(Position).order_by(Position.opened_at.desc())
        if filters:
            count_stmt = count_stmt.where(*filters)
            list_stmt = list_stmt.where(*filters)
        total = int(self._session.scalar(count_stmt) or 0)
        return list(self._session.scalars(list_stmt.limit(limit).offset(offset)).all()), total

    def list_closed_for_analytics(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> list[Position]:
        """Return all closed positions (oldest-first) for performance analytics."""
        stmt = select(Position).where(Position.status == PositionStatus.CLOSED)
        stmt = stmt.where(Position.organization_id == require_organization_id(organization_id))
        if user_id is not None:
            stmt = stmt.where(Position.user_id == user_id)
        stmt = stmt.order_by(Position.closed_at.asc())
        return list(self._session.scalars(stmt).all())

    def get_scoped(
        self,
        position_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> Position | None:
        stmt = select(Position).where(Position.id == position_id)
        stmt = stmt.where(Position.organization_id == require_organization_id(organization_id))
        if user_id is not None:
            stmt = stmt.where(Position.user_id == user_id)
        return self._session.scalar(stmt)
