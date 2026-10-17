"""Durable admission, reservation, stale guards and reply replay over transcripts.

No process-local locks, provider calls, or live Sessions are stored here. Each
phase commits and closes its own Session before control returns to network work.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ConflictError, QuotaExceededError, ValidationAppError
from app.db.models import Conversation, ConversationMessage, Organization
from app.interactive_agent.action_registry import require_action_permission
from app.schemas.common import ConversationMessageRole
from app.schemas.conversation import ConversationCreate
from app.schemas.usage import UsageEventCreate
from app.services.conversation_service import ConversationService
from app.services.quota_service import QuotaService
from app.services.turn_context import turn_context
from app.services.usage_service import UsageService

logger = structlog.get_logger(__name__)
RESERVATION = "agent_turn_reservation_v1"
LEASE_SECONDS = 360


@dataclass(frozen=True)
class TurnReservation:
    turn_id: UUID
    conversation_id: UUID
    organization_id: UUID
    user_id: UUID


def request_key(raw: str | None) -> str:
    if raw is None:
        return str(uuid4())
    try:
        return str(UUID(raw))
    except ValueError as exc:
        raise ValidationAppError("Idempotency-Key must be a UUID.") from exc


def transcript_revision(session: Session, conversation_id: UUID) -> str:
    rows = session.scalars(
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.role != ConversationMessageRole.SYSTEM,
        )
        .order_by(ConversationMessage.created_at, ConversationMessage.id)
        .execution_options(populate_existing=True)
    )
    payload = [(str(r.id), r.content, r.payload) for r in rows]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class TurnCoordinator:
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self.sessions = sessions
        self.transaction_ms: list[float] = []

    @classmethod
    def from_request_session(cls, session: Session) -> TurnCoordinator:
        bind = session.get_bind()
        # A dependency/test may bind a Connection. Never borrow its outer transaction.
        factory = sessionmaker(bind=getattr(bind, "engine", bind), expire_on_commit=False)
        session.close()
        return cls(factory)

    def reserve(
        self,
        *,
        channel: str,
        key: str,
        body: dict[str, Any],
        organization_id: UUID,
        user_id: UUID,
        conversation_id: UUID | None,
        strategy_id: UUID | None = None,
    ) -> tuple[TurnReservation, dict[str, Any] | None]:
        turn_id = uuid5(NAMESPACE_URL, f"alphatrade-turn:{organization_id}:{user_id}:{key}")
        digest = hashlib.sha256(
            json.dumps({"channel": channel, "body": body}, sort_keys=True, default=str).encode()
        ).hexdigest()
        start = time.perf_counter()
        with self.sessions() as session:
            # Admission is serialized across routes and workers within one organization.
            session.scalar(
                select(Organization.id)
                .where(Organization.id == organization_id)
                .with_for_update(key_share=True)
            )
            require_action_permission(session, organization_id=organization_id, user_id=user_id)
            existing = session.get(ConversationMessage, turn_id)
            if existing is not None:
                detail = existing.payload[RESERVATION]
                if detail["digest"] != digest:
                    raise ConflictError(
                        "Idempotency key was used with different input.",
                        details={
                            "reason": "turn_key_conflict",
                            "turn_id": str(turn_id),
                            "conversation_id": str(existing.conversation_id),
                        },
                    )
                reservation = TurnReservation(
                    turn_id, existing.conversation_id, organization_id, user_id
                )
                self.guard(session, reservation, allow_inactive=True)
                if detail["state"] == "completed" or (
                    detail["state"] == "capture" and detail.get("response")
                ):
                    return reservation, self.refresh_response(
                        session, reservation, detail["response"]
                    )
                if detail["state"] == "running" and self.expired(existing):
                    detail = {**detail, "state": "interrupted"}
                    existing.payload = {**existing.payload, RESERVATION: detail}
                    session.commit()
                raise ConflictError(
                    "This request is already accepted; recover its saved result.",
                    details={
                        "reason": "turn_" + detail["state"],
                        "turn_id": str(turn_id),
                        "conversation_id": str(existing.conversation_id),
                    },
                )
            conversations = ConversationService(session)
            if conversation_id is None:
                conversation = conversations.create(
                    ConversationCreate(
                        title=str(body.get("message") or "Capture retry")[:120],
                        strategy_id=strategy_id,
                    ),
                    organization_id=organization_id,
                    user_id=user_id,
                )
            else:
                conversation = conversations.require(
                    conversation_id, organization_id=organization_id, user_id=user_id
                )
                session.scalar(
                    select(Conversation.id)
                    .where(Conversation.id == conversation.id)
                    .with_for_update()
                )
            latest = session.scalars(
                select(ConversationMessage)
                .where(
                    ConversationMessage.conversation_id == conversation.id,
                    ConversationMessage.intent == RESERVATION,
                )
                .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            )
            for row in latest:
                detail = dict(row.payload[RESERVATION])
                if detail["state"] not in {"running", "capture"}:
                    continue
                if not self.expired(row):
                    raise ConflictError(
                        "Another turn is running in this conversation.",
                        details={
                            "reason": "conversation_turn_in_progress",
                            "turn_id": str(row.id),
                            "conversation_id": str(conversation.id),
                        },
                    )
                # Never repeat an ambiguous provider call after process interruption.
                detail["state"] = "interrupted"
                row.payload = {**row.payload, RESERVATION: detail}
            quota = QuotaService(session).check_feature(
                organization_id, "agent_chat", request_id=str(turn_id), user_id=user_id
            )
            if quota.hard_blocked:
                raise QuotaExceededError(quota.message)
            session.add(
                ConversationMessage(
                    id=turn_id,
                    conversation_id=conversation.id,
                    organization_id=organization_id,
                    user_id=user_id,
                    role=ConversationMessageRole.SYSTEM,
                    content="",
                    intent=RESERVATION,
                    request_id=str(turn_id),
                    payload={
                        RESERVATION: {
                            "digest": digest,
                            "state": "running",
                            "channel": channel,
                            "response": None,
                            "revision": None,
                        }
                    },
                )
            )
            UsageService(session, strict_mode=True).record(
                UsageEventCreate(
                    usage_event_id=uuid5(turn_id, "admission"),
                    request_id=str(turn_id),
                    feature="agent_chat",
                    organization_id=organization_id,
                    user_id=user_id,
                    provider="agent_turn_admission",
                    input_tokens=0,
                    output_tokens=0,
                )
            )
            session.commit()
            result = TurnReservation(turn_id, conversation.id, organization_id, user_id)
        self.transaction_ms.append((time.perf_counter() - start) * 1000)
        return result, None

    def guard(
        self,
        session: Session,
        reservation: TurnReservation,
        *,
        revision: str | None = None,
        allow_complete: bool = False,
        allow_inactive: bool = False,
    ) -> ConversationMessage:
        ConversationService(session).require(
            reservation.conversation_id,
            organization_id=reservation.organization_id,
            user_id=reservation.user_id,
        )
        session.scalar(
            select(Conversation.id)
            .where(Conversation.id == reservation.conversation_id)
            .with_for_update()
        )
        require_action_permission(
            session, organization_id=reservation.organization_id, user_id=reservation.user_id
        )
        row = session.scalar(
            select(ConversationMessage)
            .where(ConversationMessage.id == reservation.turn_id)
            .execution_options(populate_existing=True)
        )
        allowed = {"running", "capture", "completed"} if allow_complete else {"running", "capture"}
        if allow_inactive:
            allowed |= {"completed", "failed", "interrupted"}
        if (
            row is None
            or row.payload[RESERVATION]["state"] not in allowed
            or (not allow_complete and not allow_inactive and self.expired(row))
        ):
            raise ConflictError(
                "Turn reservation is no longer current.",
                details={
                    "reason": "turn_stale",
                    "turn_id": str(reservation.turn_id),
                    "conversation_id": str(reservation.conversation_id),
                },
            )
        if (
            revision is not None
            and transcript_revision(session, reservation.conversation_id) != revision
        ):
            raise ConflictError(
                "Conversation changed during provider work.",
                details={
                    "reason": "turn_stale_snapshot",
                    "turn_id": str(reservation.turn_id),
                    "conversation_id": str(reservation.conversation_id),
                },
            )
        return row

    @staticmethod
    def expired(row: ConversationMessage) -> bool:
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return (datetime.now(UTC) - created).total_seconds() >= LEASE_SECONDS

    @staticmethod
    def refresh_response(
        session: Session, reservation: TurnReservation, response: dict[str, Any]
    ) -> dict[str, Any]:
        """Recover current capture receipts, including later retry, correction and Undo."""
        from app.agent_capture.store import entry_record
        from app.db.models import AgentSavedEntry
        from app.interactive_agent.contracts import PAYLOAD_KEY

        response = deepcopy(response)
        if assistant_id := response.get("assistant_message_id"):
            assistant = session.get(ConversationMessage, UUID(assistant_id))
            if (
                assistant is not None
                and assistant.organization_id == reservation.organization_id
                and assistant.user_id == reservation.user_id
                and assistant.conversation_id == reservation.conversation_id
            ):
                receipt = (assistant.payload.get(PAYLOAD_KEY) or {}).get("capture") or {}
                for target, source in (
                    ("saved_entries", "saved_entries"),
                    ("capture_status", "status"),
                    ("capture_error", "error"),
                    ("capture_clarification", "clarification"),
                    ("model_usage", "model_usage"),
                ):
                    if source in receipt:
                        response[target] = receipt[source]
        field = "saved_entries" if "saved_entries" in response else "items"
        if field in response:
            canonical = []
            for entry in response[field]:
                row = session.get(AgentSavedEntry, UUID(entry["id"]))
                if (
                    row is not None
                    and row.organization_id == reservation.organization_id
                    and row.user_id == reservation.user_id
                ):
                    canonical.append(entry_record(row).model_dump(mode="json"))
            response[field] = canonical
            if field == "items":
                response["total"] = len(canonical)
        return response

    def finish(
        self, reservation: TurnReservation, response: dict[str, Any], *, state: str = "completed"
    ) -> None:
        start = time.perf_counter()
        with self.sessions() as session:
            row = self.guard(session, reservation, allow_complete=True)
            if row.payload[RESERVATION]["state"] == "completed":
                return
            row.payload = {
                **row.payload,
                RESERVATION: {**row.payload[RESERVATION], "state": state, "response": response},
            }
            session.commit()
        self.transaction_ms.append((time.perf_counter() - start) * 1000)

    def fail(self, reservation: TurnReservation) -> None:
        with self.sessions() as session:
            row = session.get(ConversationMessage, reservation.turn_id)
            if row is not None and row.payload[RESERVATION]["state"] == "running":
                row.payload = {
                    **row.payload,
                    RESERVATION: {**row.payload[RESERVATION], "state": "failed"},
                }
                session.commit()

    def run(
        self, *, work: Callable[[TurnReservation], dict[str, Any]], **kwargs: Any
    ) -> dict[str, Any]:
        started = time.perf_counter()
        reservation, replay = self.reserve(**kwargs)
        if replay is not None:
            return replay
        try:
            with turn_context(reservation.turn_id, self.sessions):
                response = work(reservation)
            self.finish(reservation, response)
            return response
        except Exception:
            self.fail(reservation)
            raise
        finally:
            logger.info(
                "agent_turn_latency",
                turn_id=str(reservation.turn_id),
                total_ms=round((time.perf_counter() - started) * 1000, 2),
                transaction_ms=[round(t, 2) for t in self.transaction_ms],
            )
