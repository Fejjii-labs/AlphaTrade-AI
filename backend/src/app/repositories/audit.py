"""Audit log persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select

from app.db.models import AuditLog
from app.repositories.base import SQLAlchemyRepository, require_organization_id
from app.schemas.common import AuditEventType


class AuditRepository(SQLAlchemyRepository[AuditLog]):
    model = AuditLog

    def list_events(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        request_id: str | None = None,
        event_type: AuditEventType | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditLog], int]:
        filters = []
        filters.append(AuditLog.organization_id == require_organization_id(organization_id))
        if user_id is not None:
            filters.append(AuditLog.user_id == user_id)
        if request_id is not None:
            filters.append(AuditLog.request_id == request_id)
        if event_type is not None:
            filters.append(AuditLog.action == event_type)
        if since is not None:
            filters.append(AuditLog.event_at >= since)

        count_stmt = select(func.count()).select_from(AuditLog)
        list_stmt = select(AuditLog).order_by(AuditLog.event_at.desc())
        if filters:
            count_stmt = count_stmt.where(*filters)
            list_stmt = list_stmt.where(*filters)

        total = int(self._session.scalar(count_stmt) or 0)
        rows = list(
            self._session.scalars(list_stmt.limit(limit).offset(offset)).all(),
        )
        return rows, total
