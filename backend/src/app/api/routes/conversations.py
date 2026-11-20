"""Persistent strategy conversations, transcripts, and proposal confirmation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.agents.confirmation_identity import (
    format_presented_confirmation_identity,
    identity_from_proposal,
)
from app.core.dependencies import SessionDep
from app.core.errors import ConflictError
from app.schemas.common import ConversationMessageRole, StrategyProposalStatus
from app.schemas.conversation import (
    ConversationCreate,
    ConversationSummary,
    PaginatedConversationMessages,
    PaginatedConversations,
    PaginatedStrategyProposals,
    StrategyProposalConfirm,
    StrategyProposalCreate,
    StrategyProposalRecord,
    StrategyProposalReject,
)
from app.security.rbac import TraderDep
from app.services.conversation_service import ConversationService
from app.services.strategy_proposal_service import StrategyProposalService

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _conversations(session: SessionDep) -> ConversationService:
    return ConversationService(session)


def _proposals(session: SessionDep) -> StrategyProposalService:
    return StrategyProposalService(session)


@router.get("", response_model=PaginatedConversations, summary="List conversations")
def list_conversations(
    tenant: TraderDep,
    session: SessionDep,
    strategy_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedConversations:
    return _conversations(session).list_conversations(
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        strategy_id=strategy_id,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ConversationSummary, summary="Create conversation")
def create_conversation(
    body: ConversationCreate,
    tenant: TraderDep,
    session: SessionDep,
) -> ConversationSummary:
    row = _conversations(session).create(
        body, organization_id=tenant.organization_id, user_id=tenant.user_id
    )
    session.commit()
    return ConversationSummary.model_validate(row)


@router.get(
    "/{conversation_id}",
    response_model=ConversationSummary,
    summary="Get conversation",
)
def get_conversation(
    conversation_id: uuid.UUID,
    tenant: TraderDep,
    session: SessionDep,
) -> ConversationSummary:
    return _conversations(session).get_summary(
        conversation_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=PaginatedConversationMessages,
    summary="List conversation messages",
)
def list_conversation_messages(
    conversation_id: uuid.UUID,
    tenant: TraderDep,
    session: SessionDep,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedConversationMessages:
    result = _conversations(session).list_messages(
        conversation_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        limit=limit,
        offset=offset,
    )

    # Read canonical entries when replaying receipts, including corrections and Undo.
    # Transcript snapshots preserve the original event; the response reflects current state.
    from sqlalchemy import select

    from app.agent_capture.store import entry_record
    from app.db.models import AgentSavedEntry
    from app.interactive_agent.contracts import PAYLOAD_KEY

    ids = set()
    for message in result.items:
        capture = (message.payload.get(PAYLOAD_KEY) or {}).get("capture") or {}
        for entry in capture.get("saved_entries", []):
            try:
                ids.add(uuid.UUID(entry["id"]))
            except (ValueError, KeyError, TypeError):
                continue
    if ids:
        entries = session.scalars(
            select(AgentSavedEntry).where(
                AgentSavedEntry.id.in_(ids),
                AgentSavedEntry.organization_id == tenant.organization_id,
                AgentSavedEntry.user_id == tenant.user_id,
            )
        )
        canonical = {str(row.id): entry_record(row).model_dump(mode="json") for row in entries}
        for message in result.items:
            payload = dict(message.payload)
            detail = dict(payload.get(PAYLOAD_KEY) or {})
            if capture := detail.get("capture"):
                detail["capture"] = {
                    **capture,
                    "saved_entries": [
                        canonical[e["id"]]
                        for e in capture.get("saved_entries", [])
                        if e.get("id") in canonical
                    ],
                }
                payload[PAYLOAD_KEY] = detail
                message.payload = payload
    return result


@router.get(
    "/{conversation_id}/proposals",
    response_model=PaginatedStrategyProposals,
    summary="List strategy proposals for a conversation",
)
def list_conversation_proposals(
    conversation_id: uuid.UUID,
    tenant: TraderDep,
    session: SessionDep,
    status: StrategyProposalStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedStrategyProposals:
    _conversations(session).require(
        conversation_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    return _proposals(session).list_for_conversation(
        conversation_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        status=status,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{conversation_id}/proposals",
    response_model=StrategyProposalRecord,
    summary="Create a preview-only structured strategy proposal",
)
def create_conversation_proposal(
    conversation_id: uuid.UUID,
    body: StrategyProposalCreate,
    tenant: TraderDep,
    session: SessionDep,
) -> StrategyProposalRecord:
    conversation = _conversations(session).require(
        conversation_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
    )
    result = _proposals(session).create_draft_from_text(
        conversation,
        text=body.text,
        strategy_id=body.strategy_id,
    )
    if result.content_hash:
        identity = identity_from_proposal(
            result,
            conversation_id=conversation.id,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
        )
        _conversations(session).append_message(
            conversation=conversation,
            role=ConversationMessageRole.ASSISTANT,
            content=format_presented_confirmation_identity(identity),
            intent="structure_strategy",
            payload={
                "proposal_id": str(result.id),
                "content_hash": result.content_hash,
            },
        )
    session.commit()
    return result


@router.get(
    "/{conversation_id}/proposals/{proposal_id}",
    response_model=StrategyProposalRecord,
    summary="Get a strategy proposal",
)
def get_conversation_proposal(
    conversation_id: uuid.UUID,
    proposal_id: uuid.UUID,
    tenant: TraderDep,
    session: SessionDep,
) -> StrategyProposalRecord:
    return _proposals(session).get(
        proposal_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        conversation_id=conversation_id,
    )


@router.post(
    "/{conversation_id}/proposals/{proposal_id}/confirm",
    response_model=StrategyProposalRecord,
    summary="Confirm a proposal and fork a strategy version",
)
def confirm_conversation_proposal(
    conversation_id: uuid.UUID,
    proposal_id: uuid.UUID,
    body: StrategyProposalConfirm,
    tenant: TraderDep,
    session: SessionDep,
) -> StrategyProposalRecord:
    if (
        body.expected_organization_id != tenant.organization_id
        or body.expected_user_id != tenant.user_id
        or body.expected_conversation_id != conversation_id
    ):
        raise ConflictError(
            "Presented confirmation identity does not match this conversation, "
            "organization, or user."
        )
    result = _proposals(session).confirm(
        proposal_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        confirm_message=body.confirm,
        request_id=body.request_id,
        conversation_id=conversation_id,
        expected_content_hash=body.expected_content_hash,
        expected_parent_version_id=body.expected_parent_version_id,
        expected_target_strategy_id=body.expected_target_strategy_id,
        expected_organization_id=body.expected_organization_id,
        expected_user_id=body.expected_user_id,
        expected_conversation_id=body.expected_conversation_id,
    )
    session.commit()
    return result


@router.post(
    "/{conversation_id}/proposals/{proposal_id}/reject",
    response_model=StrategyProposalRecord,
    summary="Reject a preview proposal without writing a version",
)
def reject_conversation_proposal(
    conversation_id: uuid.UUID,
    proposal_id: uuid.UUID,
    body: StrategyProposalReject,
    tenant: TraderDep,
    session: SessionDep,
) -> StrategyProposalRecord:
    result = _proposals(session).reject(
        proposal_id,
        organization_id=tenant.organization_id,
        user_id=tenant.user_id,
        confirm_message=body.confirm,
        conversation_id=conversation_id,
    )
    session.commit()
    return result
