"""Usage event persistence, aggregation, and quota consumption queries."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import UsageEvent as UsageEventModel
from app.repositories.base import SQLAlchemyRepository
from app.schemas.usage import UsageFeatureBreakdown, UsageProviderBreakdown, UsageSummary


class UsageRepository(SQLAlchemyRepository[UsageEventModel]):
    model = UsageEventModel

    def list_events(
        self,
        *,
        organization_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        request_id: str | None = None,
        feature: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[UsageEventModel], int]:
        filters = self._filters(
            organization_id=organization_id,
            user_id=user_id,
            request_id=request_id,
            feature=feature,
            since=since,
        )

        count_stmt = select(func.count()).select_from(UsageEventModel)
        list_stmt = select(UsageEventModel).order_by(UsageEventModel.event_at.desc())
        if filters:
            count_stmt = count_stmt.where(*filters)
            list_stmt = list_stmt.where(*filters)

        total = int(self._session.scalar(count_stmt) or 0)
        rows = list(
            self._session.scalars(list_stmt.limit(limit).offset(offset)).all(),
        )
        return rows, total

    def summarize(
        self,
        *,
        organization_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> UsageSummary:
        stmt = select(
            func.count(UsageEventModel.id),
            func.coalesce(func.sum(UsageEventModel.input_tokens), 0),
            func.coalesce(func.sum(UsageEventModel.output_tokens), 0),
            func.coalesce(func.sum(UsageEventModel.total_tokens), 0),
            func.coalesce(
                func.sum(
                    case(
                        (
                            UsageEventModel.cost_source == "provider_reported",
                            func.coalesce(UsageEventModel.provider_reported_cost, 0),
                        ),
                        else_=func.coalesce(UsageEventModel.estimated_cost, 0),
                    )
                ),
                0,
            ),
            func.coalesce(func.sum(UsageEventModel.tool_calls), 0),
            func.coalesce(
                func.sum(case((UsageEventModel.fallback_used.is_(True), 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(case((UsageEventModel.cache_hit.is_(True), 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            UsageEventModel.cost_source == "provider_reported",
                            func.coalesce(UsageEventModel.provider_reported_cost, 0),
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(func.sum(UsageEventModel.estimated_cost), 0),
        ).select_from(UsageEventModel)

        filters = self._filters(
            organization_id=organization_id,
            user_id=user_id,
            since=since,
            until=until,
        )
        if filters:
            stmt = stmt.where(*filters)

        row = self._session.execute(stmt).one()
        total_cost = Decimal(str(row[4]))
        billing_grade = Decimal(str(row[8]))
        total_estimated = Decimal(str(row[9]))

        return UsageSummary(
            organization_id=organization_id,
            user_id=user_id,
            period_start=since,
            period_end=until,
            event_count=int(row[0]),
            total_input_tokens=int(row[1]),
            total_output_tokens=int(row[2]),
            total_tokens=int(row[3]),
            total_provider_reported_cost=billing_grade,
            total_estimated_cost=total_estimated,
            total_cost=total_cost,
            billing_grade_cost=billing_grade,
            cost_is_placeholder=billing_grade <= 0,
            total_tool_calls=int(row[5]),
            fallback_count=int(row[6]),
            cache_hit_count=int(row[7]),
        )

    def aggregate_by_feature(
        self,
        *,
        organization_id: uuid.UUID,
        since: datetime,
        until: datetime | None = None,
    ) -> list[UsageFeatureBreakdown]:
        cost_expr = func.coalesce(UsageEventModel.estimated_cost, 0) + func.coalesce(
            UsageEventModel.provider_reported_cost, 0
        )
        stmt = (
            select(
                UsageEventModel.feature,
                func.count(UsageEventModel.id),
                func.coalesce(func.sum(UsageEventModel.total_tokens), 0),
                func.coalesce(func.sum(cost_expr), 0),
                func.coalesce(
                    func.sum(case((UsageEventModel.fallback_used.is_(True), 1), else_=0)),
                    0,
                ),
            )
            .where(
                UsageEventModel.organization_id == organization_id,
                UsageEventModel.event_at >= since,
            )
            .group_by(UsageEventModel.feature)
            .order_by(func.count(UsageEventModel.id).desc())
        )
        if until is not None:
            stmt = stmt.where(UsageEventModel.event_at < until)

        return [
            UsageFeatureBreakdown(
                feature=str(row[0]),
                event_count=int(row[1]),
                total_tokens=int(row[2]),
                total_cost=Decimal(str(row[3])),
                fallback_count=int(row[4]),
            )
            for row in self._session.execute(stmt).all()
        ]

    def aggregate_by_provider(
        self,
        *,
        organization_id: uuid.UUID,
        since: datetime,
        until: datetime | None = None,
    ) -> list[UsageProviderBreakdown]:
        provider_expr = func.coalesce(UsageEventModel.provider, "unknown")
        cost_expr = func.coalesce(UsageEventModel.estimated_cost, 0) + func.coalesce(
            UsageEventModel.provider_reported_cost, 0
        )
        stmt = (
            select(
                provider_expr,
                func.count(UsageEventModel.id),
                func.coalesce(func.sum(UsageEventModel.total_tokens), 0),
                func.coalesce(func.sum(cost_expr), 0),
                func.coalesce(
                    func.sum(case((UsageEventModel.fallback_used.is_(True), 1), else_=0)),
                    0,
                ),
            )
            .where(
                UsageEventModel.organization_id == organization_id,
                UsageEventModel.event_at >= since,
            )
            .group_by(provider_expr)
            .order_by(func.count(UsageEventModel.id).desc())
        )
        if until is not None:
            stmt = stmt.where(UsageEventModel.event_at < until)

        return [
            UsageProviderBreakdown(
                provider=str(row[0]),
                event_count=int(row[1]),
                total_tokens=int(row[2]),
                total_cost=Decimal(str(row[3])),
                fallback_count=int(row[4]),
            )
            for row in self._session.execute(stmt).all()
        ]

    def count_events_since(
        self,
        *,
        organization_id: uuid.UUID,
        since: datetime,
        feature: str | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(UsageEventModel)
            .where(
                UsageEventModel.organization_id == organization_id,
                UsageEventModel.event_at >= since,
            )
        )
        if feature is not None:
            stmt = stmt.where(UsageEventModel.feature == feature)
        return int(self._session.scalar(stmt) or 0)

    def count_requests_since(
        self, *, organization_id: uuid.UUID, since: datetime, feature: str | None = None
    ) -> int:
        """Count admissions; retain every attempt in token/cost/event aggregates.

        Only a server-written admission covers correlated provider/tool events.
        Arbitrary repeated request IDs on legacy events cannot bypass quotas.
        Indexing batches are internal work, separately metered as rag_indexing.
        """
        admission = aliased(UsageEventModel)
        covered = (
            select(admission.id)
            .where(
                admission.provider == "agent_turn_admission",
                admission.feature == "agent_chat",
                admission.organization_id == UsageEventModel.organization_id,
                admission.user_id == UsageEventModel.user_id,
                admission.request_id == UsageEventModel.request_id,
                admission.id != UsageEventModel.id,
            )
            .exists()
        )
        stmt = (
            select(func.count())
            .select_from(UsageEventModel)
            .where(
                UsageEventModel.organization_id == organization_id,
                UsageEventModel.event_at >= since,
                UsageEventModel.feature != "rag_indexing",
                ~covered,
            )
        )
        if feature is not None:
            stmt = stmt.where(UsageEventModel.feature == feature)
        return int(self._session.scalar(stmt) or 0)

    @staticmethod
    def _filters(
        *,
        organization_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        request_id: str | None = None,
        feature: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[ColumnElement[bool]]:
        filters = []
        if organization_id is not None:
            filters.append(UsageEventModel.organization_id == organization_id)
        if user_id is not None:
            filters.append(UsageEventModel.user_id == user_id)
        if request_id is not None:
            filters.append(UsageEventModel.request_id == request_id)
        if feature is not None:
            filters.append(UsageEventModel.feature == feature)
        if since is not None:
            filters.append(UsageEventModel.event_at >= since)
        if until is not None:
            filters.append(UsageEventModel.event_at < until)
        return filters
