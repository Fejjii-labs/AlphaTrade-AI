"""Usage event recording and cost aggregation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.db.models import UsageEvent as UsageEventModel
from app.db.session import run_in_savepoint_when_active
from app.repositories.usage import UsageRepository
from app.schemas.common import CostSource
from app.schemas.usage import (
    UsageEvent,
    UsageEventCreate,
    UsageFeatureBreakdown,
    UsageProviderBreakdown,
    UsageSummary,
)
from app.services.usage_cost import resolve_usage_cost

logger = structlog.get_logger(__name__)


class UsagePersistenceError(Exception):
    """Raised when usage persistence fails in strict mode."""


def month_start(reference: datetime | None = None) -> datetime:
    ref = reference or datetime.now(UTC)
    return ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def day_start(reference: datetime | None = None) -> datetime:
    ref = reference or datetime.now(UTC)
    return ref.replace(hour=0, minute=0, second=0, microsecond=0)


class UsageService:
    """Record and query metered usage.

    AT-016 / AT-ADR-008: ``record`` flushes into the caller's unit-of-work and
    never calls ``session.commit()``. Callers own the authoritative commit.
    Persistence failures are fail-open unless ``strict_mode`` is enabled.
    """

    def __init__(
        self,
        session: Session | None = None,
        *,
        strict_mode: bool = False,
    ) -> None:
        self._session = session
        self._repo = UsageRepository(session) if session is not None else None
        self._strict_mode = strict_mode

    def record(self, data: UsageEventCreate) -> UsageEvent:
        """Flush usage into the caller's session (no hidden commit)."""
        timestamp = data.timestamp or datetime.now(UTC)
        input_tokens = _tokens_from_provider(
            data.provider_metadata,
            "input_tokens",
            data.input_tokens,
        )
        output_tokens = _tokens_from_provider(
            data.provider_metadata,
            "output_tokens",
            data.output_tokens,
        )
        total_tokens = input_tokens + output_tokens
        resolved = resolve_usage_cost(
            model=data.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider_metadata=data.provider_metadata,
        )

        event = UsageEvent(
            usage_event_id=data.usage_event_id or uuid.uuid4(),
            request_id=data.request_id,
            organization_id=data.organization_id,
            user_id=data.user_id,
            feature=data.feature,
            model=data.model,
            provider=data.provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            provider_reported_cost=resolved.provider_reported_cost,
            estimated_cost=resolved.estimated_cost,
            cost_source=resolved.cost_source,
            cost_is_placeholder=not resolved.is_billing_grade,
            tool_calls=data.tool_calls,
            cache_hit=data.cache_hit,
            fallback_used=data.fallback_used,
            latency_ms=data.latency_ms,
            status=data.status,
            timestamp=timestamp,
        )

        if self._repo is None:
            return event

        entity = UsageEventModel(
            id=event.usage_event_id,
            organization_id=event.organization_id,
            user_id=event.user_id,
            request_id=event.request_id,
            feature=event.feature,
            model=event.model,
            provider=event.provider,
            input_tokens=event.input_tokens,
            output_tokens=event.output_tokens,
            total_tokens=total_tokens,
            provider_reported_cost=event.provider_reported_cost,
            estimated_cost=event.estimated_cost,
            cost_source=event.cost_source,
            cost_is_placeholder=not resolved.is_billing_grade,
            tool_calls=event.tool_calls,
            cache_hit=event.cache_hit,
            fallback_used=event.fallback_used,
            latency_ms=event.latency_ms,
            status=event.status,
            event_at=event.timestamp,
        )
        repo = self._repo
        try:
            # Savepoint only when an outer DB txn is already active (AT-ADR-008).
            if self._session is not None:
                run_in_savepoint_when_active(self._session, lambda: repo.add(entity))
            else:
                repo.add(entity)
        except Exception as exc:
            logger.warning("usage_persist_failed", error_type=type(exc).__name__)
            if self._strict_mode:
                raise UsagePersistenceError(str(exc)) from exc
        return event

    def list_events(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        request_id: str | None = None,
        feature: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[UsageEvent], int]:
        if self._repo is None:
            return [], 0
        rows, total = self._repo.list_events(
            organization_id=organization_id,
            user_id=user_id,
            request_id=request_id,
            feature=feature,
            limit=limit,
            offset=offset,
        )
        return [_to_event(row) for row in rows], total

    def summarize(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        since: datetime | None = None,
    ) -> UsageSummary:
        if self._repo is None:
            return UsageSummary(organization_id=organization_id, user_id=user_id)
        period_start = since or month_start()
        return self._repo.summarize(
            organization_id=organization_id,
            user_id=user_id,
            since=period_start,
        )

    def summarize_by_feature(
        self,
        *,
        organization_id: uuid.UUID,
        since: datetime | None = None,
    ) -> list[UsageFeatureBreakdown]:
        if self._repo is None:
            return []
        return self._repo.aggregate_by_feature(
            organization_id=organization_id,
            since=since or month_start(),
        )

    def summarize_by_provider(
        self,
        *,
        organization_id: uuid.UUID,
        since: datetime | None = None,
    ) -> list[UsageProviderBreakdown]:
        if self._repo is None:
            return []
        return self._repo.aggregate_by_provider(
            organization_id=organization_id,
            since=since or month_start(),
        )

    def count_requests_since(
        self,
        *,
        organization_id: uuid.UUID,
        since: datetime,
        feature: str | None = None,
    ) -> int:
        if self._repo is None:
            return 0
        return self._repo.count_events_since(
            organization_id=organization_id,
            since=since,
            feature=feature,
        )


def _tokens_from_provider(metadata: dict[str, Any], key: str, fallback: int) -> int:
    raw = metadata.get(key)
    if isinstance(raw, int) and raw >= 0:
        return raw
    return fallback


def _to_event(row: UsageEventModel) -> UsageEvent:
    cost_source = row.cost_source
    if isinstance(cost_source, str):
        try:
            cost_source = CostSource(cost_source)
        except ValueError:
            cost_source = CostSource.UNAVAILABLE

    return UsageEvent(
        usage_event_id=row.id,
        request_id=row.request_id,
        organization_id=row.organization_id,
        user_id=row.user_id,
        feature=row.feature,
        model=row.model,
        provider=row.provider,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        total_tokens=row.total_tokens,
        provider_reported_cost=row.provider_reported_cost,
        estimated_cost=row.estimated_cost,
        cost_source=cost_source,
        cost_is_placeholder=row.cost_is_placeholder,
        tool_calls=row.tool_calls,
        cache_hit=row.cache_hit,
        fallback_used=row.fallback_used,
        latency_ms=row.latency_ms,
        status=row.status,
        timestamp=row.event_at,
    )
