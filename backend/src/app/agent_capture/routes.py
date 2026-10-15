"""Authenticated private capture API. A receipt is returned after outer commit."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query
from sqlalchemy import select

from app.agent_capture.contracts import (
    CaptureRetry,
    Category,
    SavedEntriesPage,
    SavedEntry,
    SavedEntryUpdate,
)
from app.agent_capture.store import entry_record, list_entries, require_entry, update_entry
from app.core.dependencies import SessionDep, SettingsDep
from app.db.models import ConversationMessage
from app.interactive_agent.contracts import PAYLOAD_KEY
from app.interactive_agent.turn_contracts import TURN_CONFLICT_RESPONSES
from app.security.rbac import TraderDep
from app.services.turn_coordinator import TurnReservation
from app.services.turn_policy import TURN_DEPENDENCIES

router = APIRouter(prefix="/agent/saved", tags=["agent"])


@router.get("", response_model=SavedEntriesPage)
async def saved_list(
    tenant: TraderDep,
    session: SessionDep,
    category: Category | None = None,
    view: Literal["journal", "knowledge"] | None = None,
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SavedEntriesPage:
    return list_entries(
        session,
        tenant.organization_id,
        tenant.user_id,
        category=category,
        view=view,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/retry",
    response_model=SavedEntriesPage,
    dependencies=TURN_DEPENDENCIES,
    responses=TURN_CONFLICT_RESPONSES,
)
async def retry_capture(
    body: CaptureRetry,
    tenant: TraderDep,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: str | None = Header(
        default=None, description="Stable UUID for capture recovery."
    ),
) -> SavedEntriesPage:
    from functools import partial

    from starlette.concurrency import run_in_threadpool

    from app.core.errors import NotFoundError, ValidationAppError
    from app.interactive_agent.turn_runtime import capture_in_phases
    from app.services.turn_coordinator import TurnCoordinator, request_key

    coordinator = TurnCoordinator.from_request_session(session)

    def work(reservation: TurnReservation) -> dict[str, Any]:
        with coordinator.sessions() as phase_session:
            coordinator.guard(phase_session, reservation)
            message = phase_session.get(ConversationMessage, body.source_message_id)
            if (
                message is None
                or message.conversation_id != body.conversation_id
                or message.organization_id != tenant.organization_id
                or message.user_id != tenant.user_id
            ):
                raise NotFoundError("Source message not found.")
            source = (message.payload.get(PAYLOAD_KEY) or {}).get("source_document_id")
        entries, status, clarification, usage = capture_in_phases(
            coordinator.sessions,
            settings,
            reservation,
            body.source_message_id,
            UUID(source) if source else None,
        )
        if status == "clarification":
            raise ValidationAppError(clarification or "Capture needs clarification.")
        with coordinator.sessions() as phase_session:
            reservation_row = coordinator.guard(phase_session, reservation)
            for assistant in phase_session.scalars(
                select(ConversationMessage).where(
                    ConversationMessage.conversation_id == body.conversation_id,
                    ConversationMessage.organization_id == tenant.organization_id,
                    ConversationMessage.user_id == tenant.user_id,
                )
            ):
                payload = dict(assistant.payload or {})
                detail = dict(payload.get(PAYLOAD_KEY) or {})
                receipt = detail.get("capture") or {}
                if receipt.get("source_message_id") == str(body.source_message_id):
                    usage_rows = [*receipt.get("model_usage", []), *([usage] if usage else [])]
                    detail["model_usage"] = usage_rows
                    detail["capture"] = {
                        **receipt,
                        "saved_entries": [e.model_dump(mode="json") for e in entries],
                        "status": status,
                        "error": None,
                        "clarification": None,
                        "model_usage": usage_rows,
                    }
                    assistant.payload = {**payload, PAYLOAD_KEY: detail}
            response = SavedEntriesPage(items=entries, total=len(entries)).model_dump(mode="json")
            from app.services.turn_coordinator import RESERVATION

            reservation_row.payload = {
                **reservation_row.payload,
                RESERVATION: {
                    **reservation_row.payload[RESERVATION],
                    "state": "completed",
                    "response": response,
                },
            }
            phase_session.commit()
        return response

    result = await run_in_threadpool(
        partial(
            coordinator.run,
            channel="capture_retry",
            key=request_key(idempotency_key),
            body=body.model_dump(mode="json"),
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            conversation_id=body.conversation_id,
            work=work,
        )
    )
    return SavedEntriesPage.model_validate(result)


@router.get("/{entry_id}", response_model=SavedEntry)
async def saved_get(entry_id: UUID, tenant: TraderDep, session: SessionDep) -> SavedEntry:
    return entry_record(require_entry(session, entry_id, tenant.organization_id, tenant.user_id))


@router.patch("/{entry_id}", response_model=SavedEntry)
async def saved_update(
    entry_id: UUID, body: SavedEntryUpdate, tenant: TraderDep, session: SessionDep
) -> SavedEntry:
    result = update_entry(session, entry_id, body, tenant.organization_id, tenant.user_id)
    session.commit()
    return result
