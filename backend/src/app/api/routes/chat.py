"""AI trading workspace chat API — LangGraph agent orchestration."""

from __future__ import annotations

from fastapi import APIRouter, Header

from app.core.dependencies import (
    CanonicalEvidenceServiceDep,
    CanonicalRuntimeDep,
    SessionDep,
    SettingsDep,
)
from app.interactive_agent.turn_contracts import TURN_CONFLICT_RESPONSES
from app.schemas.chat import AgentMessageResponse, ChatMessageRequest
from app.security.rbac import TraderDep
from app.services.conversation_service import parse_conversation_id
from app.services.turn_policy import TURN_DEPENDENCIES

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/message",
    response_model=AgentMessageResponse,
    summary="Send chat message",
    dependencies=TURN_DEPENDENCIES,
    responses=TURN_CONFLICT_RESPONSES,
)
async def send_message(
    body: ChatMessageRequest,
    tenant: TraderDep,
    session: SessionDep,
    settings: SettingsDep,
    canonical_runtime: CanonicalRuntimeDep,
    canonical_evidence: CanonicalEvidenceServiceDep,
    idempotency_key: str | None = Header(
        default=None, description="Stable UUID for turn recovery."
    ),
) -> AgentMessageResponse:
    from functools import partial

    from starlette.concurrency import run_in_threadpool

    from app.services.chat_turn_runtime import run_chat
    from app.services.turn_coordinator import TurnCoordinator, request_key

    coordinator = TurnCoordinator.from_request_session(session)
    key = request_key(idempotency_key)
    result = await run_in_threadpool(
        partial(
            coordinator.run,
            channel="chat",
            key=key,
            body=body.model_dump(mode="json"),
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
            conversation_id=parse_conversation_id(body.conversation_id),
            strategy_id=parse_conversation_id(body.strategy_id),
            work=lambda reservation: run_chat(
                coordinator, settings, reservation, body, canonical_runtime, canonical_evidence
            ),
        )
    )
    return AgentMessageResponse.model_validate(result)
