"""Audit event recording with redaction and optional persistence.

AT-016 / AT-ADR-008: ``record`` only flushes into the caller's unit-of-work.
Callers own ``session.commit()``. Security/reject events that must survive a
business rollback use ``record_durable_isolated`` (dedicated session + commit).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import AuditLog
from app.db.session import run_in_savepoint_when_active
from app.guardrails.redaction import redact_mapping
from app.repositories.audit import AuditRepository
from app.schemas.audit import AuditRecord, AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType

logger = structlog.get_logger(__name__)

SessionFactory = Callable[[], Session] | sessionmaker[Session]


class AuditPersistenceError(Exception):
    """Raised when audit persistence fails in strict mode."""


def _payload_hash(metadata: dict[str, Any]) -> str:
    canonical = json.dumps(metadata, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class AuditService:
    """Record and query auditable events."""

    def __init__(
        self,
        session: Session | None = None,
        *,
        strict_mode: bool = False,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._session = session
        self._repo = AuditRepository(session) if session is not None else None
        self._strict_mode = strict_mode
        self._session_factory = session_factory

    def record(self, data: AuditRecordCreate) -> AuditRecord | None:
        """Flush an audit row into the caller's session (no hidden commit).

        The caller / route unit-of-work must ``session.commit()`` (or roll back)
        to make the event durable with the surrounding business mutation.
        ``event_id`` is available after flush for FK linkage (e.g. approvals).
        """
        record, entity = self._build_record(data)
        if self._repo is None:
            return record

        repo = self._repo
        try:
            # Savepoint only when an outer DB txn is already active (AT-ADR-008).
            if self._session is not None:
                run_in_savepoint_when_active(self._session, lambda: repo.add(entity))
            else:
                repo.add(entity)
        except Exception as exc:
            logger.warning("audit_persist_failed", error_type=type(exc).__name__)
            if self._strict_mode:
                raise AuditPersistenceError(str(exc)) from exc
            return record
        return record

    def record_durable_isolated(self, data: AuditRecordCreate) -> AuditRecord | None:
        """Persist an audit event in a dedicated short-lived session and commit.

        Use deliberately for rejected / blocked / security events that must
        remain durable even when the request's business transaction rolls back.
        Does not commit the caller's shared request session.
        """
        factory = self._resolve_isolated_factory()
        isolated = factory()
        try:
            service = AuditService(
                isolated,
                strict_mode=self._strict_mode,
                session_factory=factory,
            )
            record = service.record(data)
            isolated.commit()
            return record
        except Exception as exc:
            isolated.rollback()
            logger.warning(
                "audit_durable_isolated_failed",
                error_type=type(exc).__name__,
            )
            if self._strict_mode:
                raise AuditPersistenceError(str(exc)) from exc
            # Best-effort in-memory record when persistence fails non-strictly.
            record, _ = self._build_record(data)
            return record
        finally:
            isolated.close()

    def _resolve_isolated_factory(self) -> SessionFactory:
        """Prefer an explicit factory, else the request session's engine."""
        if self._session_factory is not None:
            return self._session_factory
        if self._session is not None:
            bind = self._session.get_bind()
            return sessionmaker(bind=bind, expire_on_commit=False)
        from app.db.session import get_session_factory

        return get_session_factory()

    def list_records(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        request_id: str | None = None,
        event_type: AuditEventType | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditRecord], int]:
        if self._repo is None:
            return [], 0
        rows, total = self._repo.list_events(
            organization_id=organization_id,
            user_id=user_id,
            request_id=request_id,
            event_type=event_type,
            limit=limit,
            offset=offset,
        )
        return [_to_record(row) for row in rows], total

    def _build_record(self, data: AuditRecordCreate) -> tuple[AuditRecord, AuditLog]:
        redacted = redact_mapping(data.metadata)
        timestamp = data.timestamp or datetime.now(UTC)
        action = data.action or data.event_type.value
        record = AuditRecord(
            event_id=uuid.uuid4(),
            request_id=data.request_id,
            trace_id=data.trace_id,
            user_id=data.user_id,
            organization_id=data.organization_id,
            event_type=data.event_type,
            resource_type=data.resource_type,
            resource_id=data.resource_id,
            actor_type=data.actor_type,
            action=action,
            result=data.result,
            severity=data.severity,
            payload_hash=_payload_hash(redacted),
            redacted_metadata=redacted,
            timestamp=timestamp,
        )
        entity = AuditLog(
            id=record.event_id,
            organization_id=record.organization_id,
            user_id=record.user_id,
            trace_id=record.trace_id,
            actor=_actor_label(record.actor_type, record.user_id),
            actor_type=record.actor_type,
            action=record.event_type,
            resource_type=record.resource_type,
            resource_id=record.resource_id,
            result=record.result,
            severity=record.severity,
            payload_hash=record.payload_hash,
            redacted_metadata=record.redacted_metadata,
            after=record.redacted_metadata,
            request_id=record.request_id,
            event_at=record.timestamp,
        )
        return record, entity


def _actor_label(actor_type: ActorType, user_id: uuid.UUID | None) -> str:
    if actor_type is ActorType.USER and user_id is not None:
        return str(user_id)
    return actor_type.value


def _to_record(row: AuditLog) -> AuditRecord:
    return AuditRecord(
        event_id=row.id,
        request_id=row.request_id or "",
        trace_id=row.trace_id or "",
        user_id=row.user_id,
        organization_id=row.organization_id,
        event_type=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        actor_type=row.actor_type,
        action=row.action.value,
        result=row.result,
        severity=row.severity,
        payload_hash=row.payload_hash or "",
        redacted_metadata=row.redacted_metadata or {},
        timestamp=row.event_at,
    )
