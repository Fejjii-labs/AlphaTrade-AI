"""Persist actual model-call telemetry without creating a second usage system.

Each actual LLM call records one :class:`ModelCallAttempt` and, when durable
storage is available, exactly one :class:`UsageEvent` linked one-to-one.
Cost is never fabricated. Persistence uses an isolated transaction so an outer
domain rollback cannot erase the fact that provider I/O occurred.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.schemas.common import CostSource
from app.schemas.model_routing import ModelCallAttempt
from app.schemas.usage import UsageEventCreate
from app.services.cost_estimator import estimate_placeholder_cost
from app.services.usage_cost import build_provider_metadata

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from app.services.usage_service import UsageService

_KNOWN_RATE_MODELS = frozenset({"gpt-4o-mini", "gpt-4o"})


class ModelTelemetryPersistenceError(AppError):
    """Raised when provider I/O occurred but attempt telemetry could not be persisted."""

    code = "model_telemetry_persist_failed"

    def __init__(self, message: str, *, attempt: ModelCallAttempt) -> None:
        super().__init__(message, details={"attempt_id": str(attempt.attempt_id)})
        self.attempt = attempt


@dataclass(frozen=True)
class ResolvedModelCallCost:
    estimated_cost: Decimal
    cost_source: CostSource


def resolve_model_call_cost(
    *,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
) -> ResolvedModelCallCost:
    """Return labelled cost. Never invent a provider-reported price."""
    name = (model or "").strip()
    if name not in _KNOWN_RATE_MODELS:
        return ResolvedModelCallCost(Decimal("0"), CostSource.UNAVAILABLE)
    if input_tokens <= 0 and output_tokens <= 0:
        return ResolvedModelCallCost(Decimal("0"), CostSource.UNAVAILABLE)
    estimated = estimate_placeholder_cost(
        model=name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    return ResolvedModelCallCost(estimated, CostSource.STATIC_ESTIMATED)


class ModelCallTelemetryService:
    """Record ModelCallAttempt rows and matching UsageEvent rows exactly once."""

    def __init__(
        self,
        session: Session | None = None,
        usage_service: UsageService | None = None,
        *,
        isolated: bool = True,
    ) -> None:
        self._session = session
        self._usage = usage_service
        self._isolated = isolated

    def record(self, attempt: ModelCallAttempt, *, feature: str) -> ModelCallAttempt:
        from app.services.turn_context import current_turn

        turn = current_turn()
        if turn is not None:
            # Every coordinated attempt owns an independent session and commit.
            with turn.sessions() as persist_session:
                try:
                    stored = self._persist(persist_session, attempt, feature=feature)
                    persist_session.commit()
                    return stored
                except Exception as exc:
                    persist_session.rollback()
                    raise ModelTelemetryPersistenceError(
                        "Model call telemetry could not be persisted.", attempt=attempt
                    ) from exc
        if self._session is None:
            return attempt.model_copy(update={"persisted": False, "telemetry_durable": False})

        bind = self._session.get_bind()
        if bind is None:
            raise ModelTelemetryPersistenceError(
                "Model telemetry session has no bind.",
                attempt=attempt,
            )
        # Never borrow an outer Connection: an isolated rollback must not undo it.
        isolated_bind = getattr(bind, "engine", bind)
        persist_session = Session(bind=isolated_bind) if self._isolated else self._session
        try:
            stored = self._persist(persist_session, attempt, feature=feature)
            if self._isolated:
                persist_session.commit()
            else:
                persist_session.flush()
            return stored
        except ModelTelemetryPersistenceError:
            if self._isolated:
                persist_session.rollback()
            raise
        except Exception as exc:
            if self._isolated:
                persist_session.rollback()
            logger.warning("model_call_attempt_persist_failed", error_type=type(exc).__name__)
            raise ModelTelemetryPersistenceError(
                "Model call telemetry could not be persisted.",
                attempt=attempt.model_copy(update={"persisted": False, "telemetry_durable": False}),
            ) from exc
        finally:
            if self._isolated:
                persist_session.close()

    def _persist(
        self,
        session: Session,
        attempt: ModelCallAttempt,
        *,
        feature: str,
    ) -> ModelCallAttempt:
        from app.core.persistence_firewall import (
            assert_entity_write_allowed,
            install_persistence_firewall,
        )
        from app.db.models import ModelCallAttempt as ModelCallAttemptRow
        from app.db.models import UsageEvent as UsageEventRow
        from app.services.usage_service import UsagePersistenceError, UsageService

        install_persistence_firewall()
        existing_attempt = session.get(ModelCallAttemptRow, attempt.attempt_id)
        if existing_attempt is not None:
            return attempt.model_copy(
                update={
                    "usage_event_id": existing_attempt.usage_event_id,
                    "persisted": True,
                    "telemetry_durable": True,
                }
            )

        usage_event_id = attempt.usage_event_id or attempt.attempt_id
        existing_usage = session.get(UsageEventRow, usage_event_id)
        if existing_usage is None:
            usage = UsageService(session, strict_mode=True)
            meta = build_provider_metadata(
                input_tokens=attempt.input_tokens,
                output_tokens=attempt.output_tokens,
                cost_source=attempt.cost_source,
                fallback_used=attempt.fallback_used,
                routing_purpose=attempt.purpose.value,
                routing_tier=attempt.tier.value,
                requested_model=attempt.requested_model,
                resolved_model=attempt.resolved_model,
                correlation_id=attempt.correlation_id,
                failure_category=attempt.failure_category.value,
                usage_category="actual_provider_attempt",
            )
            try:
                with session.begin_nested():
                    event = usage.record(
                        UsageEventCreate(
                            usage_event_id=usage_event_id,
                            request_id=attempt.correlation_id,
                            feature=feature,
                            user_id=attempt.user_id,
                            organization_id=attempt.organization_id,
                            provider=attempt.provider,
                            model=attempt.resolved_model,
                            input_tokens=attempt.input_tokens,
                            output_tokens=attempt.output_tokens,
                            fallback_used=attempt.fallback_used,
                            latency_ms=attempt.latency_ms,
                            status=attempt.status,
                            timestamp=attempt.completed_at,
                            provider_metadata=meta,
                        )
                    )
                    usage_event_id = event.usage_event_id or usage_event_id
            except IntegrityError:
                recovered = session.get(UsageEventRow, usage_event_id)
                if recovered is None:
                    raise
            except UsagePersistenceError as exc:
                raise ModelTelemetryPersistenceError(
                    "Usage event for model attempt could not be persisted.",
                    attempt=attempt.model_copy(
                        update={"persisted": False, "telemetry_durable": False}
                    ),
                ) from exc

        stored = attempt.model_copy(
            update={
                "usage_event_id": usage_event_id,
                "persisted": True,
                "telemetry_durable": True,
            }
        )
        row = ModelCallAttemptRow(
            id=stored.attempt_id,
            organization_id=stored.organization_id,
            user_id=stored.user_id,
            account_id=stored.account_id,
            correlation_id=stored.correlation_id,
            usage_event_id=usage_event_id,
            purpose=stored.purpose,
            tier=stored.tier,
            provider=stored.provider,
            requested_model=stored.requested_model,
            resolved_model=stored.resolved_model,
            policy_version=stored.policy_version,
            fallback_policy=stored.fallback_policy,
            fallback_used=stored.fallback_used,
            input_tokens=stored.input_tokens,
            output_tokens=stored.output_tokens,
            latency_ms=stored.latency_ms,
            success=stored.success,
            failure_category=stored.failure_category,
            status=stored.status,
            estimated_cost=stored.estimated_cost,
            cost_source=stored.cost_source,
            resource_type=stored.resource_type,
            resource_id=stored.resource_id,
            retention_category=stored.retention_category,
            mutation_allowed=False,
            started_at=stored.started_at,
            completed_at=stored.completed_at,
            event_at=stored.completed_at or datetime.now(UTC),
        )
        assert_entity_write_allowed(row)
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
        except IntegrityError:
            existing = session.get(ModelCallAttemptRow, stored.attempt_id)
            if existing is None:
                raise
            return stored.model_copy(update={"usage_event_id": existing.usage_event_id})
        return stored
