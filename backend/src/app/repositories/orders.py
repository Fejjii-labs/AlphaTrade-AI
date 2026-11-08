"""Paper order persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db.models import Order
from app.repositories.base import SQLAlchemyRepository, require_organization_id


class OrderRepository(SQLAlchemyRepository[Order]):
    model = Order

    def get_by_idempotency_key(self, idempotency_key: str) -> Order | None:
        stmt = select(Order).where(Order.idempotency_key == idempotency_key)
        return self._session.scalar(stmt)

    def list_orders(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Order], int]:
        filters = []
        filters.append(Order.organization_id == require_organization_id(organization_id))
        if user_id is not None:
            filters.append(Order.user_id == user_id)

        count_stmt = select(func.count()).select_from(Order)
        list_stmt = select(Order).order_by(Order.created_at.desc())
        if filters:
            count_stmt = count_stmt.where(*filters)
            list_stmt = list_stmt.where(*filters)
        total = int(self._session.scalar(count_stmt) or 0)
        return list(self._session.scalars(list_stmt.limit(limit).offset(offset)).all()), total
