"""Durable conversation transcripts. Not a domain-memory or strategy authority."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models import Conversation, ConversationMessage
from app.repositories.conversations import ConversationMessageRepository, ConversationRepository
from app.repositories.strategy_library import UserStrategyRepository
from app.schemas.agent import ConversationTurn
from app.schemas.common import ConversationMessageRole, ConversationStatus
from app.schemas.conversation import (
    ConversationCreate,
    ConversationMessageRecord,
    ConversationSummary,
    PaginatedConversationMessages,
    PaginatedConversations,
)

HISTORY_LIMIT = 20
MAX_STORED_CONTENT = 16000


def parse_conversation_id(raw: str | None) -> uuid.UUID | None:
    """Accept real UUIDs only. Echo ids such as ``conv-abc`` start a new thread."""
    if raw is None or not raw.strip():
        return None
    try:
        return uuid.UUID(raw.strip())
    except ValueError:
        return None


def _truncate(content: str) -> str:
    if len(content) <= MAX_STORED_CONTENT:
        return content
    return content[: MAX_STORED_CONTENT - 16] + "\n…[truncated]"


class ConversationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._conversations = ConversationRepository(session)
        self._messages = ConversationMessageRepository(session)
        self._strategies = UserStrategyRepository(session)

    def require(
        self,
        conversation_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Conversation:
        row = self._conversations.get_scoped(
            conversation_id, organization_id=organization_id, user_id=user_id
        )
        if row is None:
            raise NotFoundError("Conversation not found.")
        return row

    def create(
        self,
        payload: ConversationCreate,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Conversation:
        strategy_id = payload.strategy_id
        if strategy_id is not None:
            strategy = self._strategies.get_scoped(
                strategy_id, organization_id=organization_id, user_id=user_id
            )
            if strategy is None:
                raise NotFoundError("Strategy not found.")
        row = Conversation(
            organization_id=organization_id,
            user_id=user_id,
            title=payload.title,
            status=ConversationStatus.ACTIVE,
            strategy_id=strategy_id,
        )
        self._conversations.add(row)
        return row

    def get_or_create(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        strategy_id: uuid.UUID | None = None,
        title: str | None = None,
    ) -> Conversation:
        if conversation_id is not None:
            row = self.require(conversation_id, organization_id=organization_id, user_id=user_id)
            if strategy_id is not None and row.strategy_id is None:
                strategy = self._strategies.get_scoped(
                    strategy_id, organization_id=organization_id, user_id=user_id
                )
                if strategy is None:
                    raise NotFoundError("Strategy not found.")
                row.strategy_id = strategy_id
                row.updated_at = datetime.now(UTC)
                self._session.flush()
            return row
        if strategy_id is not None:
            existing = self._conversations.latest_for_strategy(
                organization_id=organization_id, user_id=user_id, strategy_id=strategy_id
            )
            if existing is not None:
                return existing
        return self.create(
            ConversationCreate(title=title, strategy_id=strategy_id),
            organization_id=organization_id,
            user_id=user_id,
        )

    def list_conversations(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        strategy_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedConversations:
        rows, total = self._conversations.list_scoped(
            organization_id=organization_id,
            user_id=user_id,
            strategy_id=strategy_id,
            limit=limit,
            offset=offset,
        )
        return PaginatedConversations(
            items=[ConversationSummary.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_summary(
        self,
        conversation_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ConversationSummary:
        return ConversationSummary.model_validate(
            self.require(conversation_id, organization_id=organization_id, user_id=user_id)
        )

    def append_message(
        self,
        *,
        conversation: Conversation,
        role: ConversationMessageRole,
        content: str,
        request_id: str | None = None,
        intent: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ConversationMessage:
        row = ConversationMessage(
            conversation_id=conversation.id,
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
            role=role,
            content=_truncate(content),
            request_id=request_id,
            intent=intent,
            payload=payload or {},
        )
        self._messages.add(row)
        conversation.updated_at = datetime.now(UTC)
        if conversation.title is None and role is ConversationMessageRole.USER:
            conversation.title = content.strip()[:120] or None
        self._session.flush()
        return row

    def list_messages(
        self,
        conversation_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
        tail: bool = False,
    ) -> PaginatedConversationMessages:
        self.require(conversation_id, organization_id=organization_id, user_id=user_id)
        rows, total = self._messages.list_for_conversation(
            conversation_id,
            organization_id=organization_id,
            user_id=user_id,
            limit=limit,
            offset=offset,
            tail=tail,
        )
        return PaginatedConversationMessages(
            items=[ConversationMessageRecord.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=max(0, total - limit) if tail else offset,
        )

    def history_turns(
        self,
        conversation: Conversation,
        *,
        limit: int = HISTORY_LIMIT,
    ) -> list[ConversationTurn]:
        rows = self._messages.recent(
            conversation.id,
            organization_id=conversation.organization_id,
            user_id=conversation.user_id,
            limit=limit,
        )
        turns: list[ConversationTurn] = []
        for row in rows:
            if row.role is ConversationMessageRole.SYSTEM:
                continue
            role = "user" if row.role is ConversationMessageRole.USER else "assistant"
            turns.append(ConversationTurn(role=role, content=row.content))
        return turns
