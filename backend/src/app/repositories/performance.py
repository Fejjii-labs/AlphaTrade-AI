"""Persistence for performance snapshots and per-strategy daily rollups."""

from __future__ import annotations

import uuid
from datetime import date as date_type
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import PerformanceSnapshot, StrategyPerformanceDaily
from app.repositories.base import SQLAlchemyRepository, require_organization_id


class PerformanceSnapshotRepository(SQLAlchemyRepository[PerformanceSnapshot]):
    model = PerformanceSnapshot

    def latest(self, *, organization_id: uuid.UUID) -> PerformanceSnapshot | None:
        stmt = (
            select(PerformanceSnapshot)
            .where(PerformanceSnapshot.organization_id == require_organization_id(organization_id))
            .order_by(PerformanceSnapshot.as_of.desc())
            .limit(1)
        )
        return self._session.scalar(stmt)

    def list_for_tenant(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        start_date: date_type | None = None,
        end_date: date_type | None = None,
        limit: int = 50,
    ) -> list[PerformanceSnapshot]:
        stmt = select(PerformanceSnapshot).where(
            PerformanceSnapshot.organization_id == organization_id,
            PerformanceSnapshot.user_id == user_id,
        )
        if start_date is not None:
            start_dt = datetime.combine(start_date, time.min, tzinfo=ZoneInfo("UTC"))
            stmt = stmt.where(PerformanceSnapshot.as_of >= start_dt)
        if end_date is not None:
            end_dt = datetime.combine(end_date, time.max, tzinfo=ZoneInfo("UTC")) + timedelta(
                microseconds=1
            )
            stmt = stmt.where(PerformanceSnapshot.as_of < end_dt)
        stmt = stmt.order_by(PerformanceSnapshot.as_of.desc()).limit(limit)
        return list(self._session.scalars(stmt).all())


class StrategyPerformanceDailyRepository(SQLAlchemyRepository[StrategyPerformanceDaily]):
    model = StrategyPerformanceDaily

    def get_for_day(
        self,
        *,
        organization_id: uuid.UUID | None,
        strategy_id: str,
        day: date_type,
    ) -> StrategyPerformanceDaily | None:
        stmt = select(StrategyPerformanceDaily).where(
            StrategyPerformanceDaily.organization_id == organization_id,
            StrategyPerformanceDaily.strategy_id == strategy_id,
            StrategyPerformanceDaily.day == day,
        )
        return self._session.scalar(stmt)
