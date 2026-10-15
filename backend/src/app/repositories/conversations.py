"""Tenant-scoped conversation and proposal persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select

from app.db.models import (
    Conversation,
    ConversationMessage,
    StrategyConversationProposal,
    StrategyVersionConversationLink,
)
from app.repositories.base import SQLAlchemyRepository
from app.schemas.common import ConversationStatus, StrategyProposalStatus


class ConversationRepository(SQLAlchemyRepository[Conversation]):
    model = Conversation

    def get_scoped(
        self,
        conversation_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Conversation | None:
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.organization_id == organization_id,
            Conversation.user_id == user_id,
        )
        return self._session.scalar(stmt)

    def list_scoped(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        strategy_id: uuid.UUID | None = None,
        status: ConversationStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Conversation], int]:
        filters = [
            Conversation.organization_id == organization_id,
            Conversation.user_id == user_id,
        ]
        if strategy_id is not None:
            filters.append(Conversation.strategy_id == strategy_id)
        if status is not None:
            filters.append(Conversation.status == status)
        total = int(
            self._session.scalar(select(func.count()).select_from(Conversation).where(*filters))
            or 0
        )
        stmt = (
            select(Conversation)
            .where(*filters)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(stmt).all()), total

    def latest_for_strategy(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        strategy_id: uuid.UUID,
    ) -> Conversation | None:
        stmt = (
            select(Conversation)
            .where(
                Conversation.organization_id == organization_id,
                Conversation.user_id == user_id,
                Conversation.strategy_id == strategy_id,
                Conversation.status == ConversationStatus.ACTIVE,
            )
            .order_by(Conversation.updated_at.desc())
            .limit(1)
        )
        return self._session.scalar(stmt)


class ConversationMessageRepository(SQLAlchemyRepository[ConversationMessage]):
    model = ConversationMessage

    def list_for_conversation(
        self,
        conversation_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
        newest_first: bool = False,
    ) -> tuple[list[ConversationMessage], int]:
        filters = [
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.organization_id == organization_id,
            ConversationMessage.user_id == user_id,
            or_(
                ConversationMessage.intent.is_(None),
                ConversationMessage.intent != "agent_turn_reservation_v1",
            ),
        ]
        total = int(
            self._session.scalar(
                select(func.count()).select_from(ConversationMessage).where(*filters)
            )
            or 0
        )
        order = (
            ConversationMessage.created_at.desc()
            if newest_first
            else ConversationMessage.created_at.asc()
        )
        stmt = (
            select(ConversationMessage).where(*filters).order_by(order).limit(limit).offset(offset)
        )
        return list(self._session.scalars(stmt).all()), total

    def recent(
        self,
        conversation_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        limit: int,
    ) -> list[ConversationMessage]:
        rows, _ = self.list_for_conversation(
            conversation_id,
            organization_id=organization_id,
            user_id=user_id,
            limit=limit,
            newest_first=True,
        )
        return list(reversed(rows))


class StrategyConversationProposalRepository(SQLAlchemyRepository[StrategyConversationProposal]):
    model = StrategyConversationProposal

    def get_scoped(
        self,
        proposal_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID | None = None,
    ) -> StrategyConversationProposal | None:
        filters = [
            StrategyConversationProposal.id == proposal_id,
            StrategyConversationProposal.organization_id == organization_id,
            StrategyConversationProposal.user_id == user_id,
        ]
        if conversation_id is not None:
            filters.append(StrategyConversationProposal.conversation_id == conversation_id)
        return self._session.scalar(select(StrategyConversationProposal).where(*filters))

    def get_scoped_for_update(
        self,
        proposal_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        conversation_id: uuid.UUID | None = None,
    ) -> StrategyConversationProposal | None:
        filters = [
            StrategyConversationProposal.id == proposal_id,
            StrategyConversationProposal.organization_id == organization_id,
            StrategyConversationProposal.user_id == user_id,
        ]
        if conversation_id is not None:
            filters.append(StrategyConversationProposal.conversation_id == conversation_id)
        stmt = (
            select(StrategyConversationProposal)
            .where(*filters)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self._session.scalar(stmt)

    def list_for_conversation(
        self,
        conversation_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        status: StrategyProposalStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[StrategyConversationProposal], int]:
        filters = [
            StrategyConversationProposal.conversation_id == conversation_id,
            StrategyConversationProposal.organization_id == organization_id,
            StrategyConversationProposal.user_id == user_id,
        ]
        if status is not None:
            filters.append(StrategyConversationProposal.status == status)
        total = int(
            self._session.scalar(
                select(func.count()).select_from(StrategyConversationProposal).where(*filters)
            )
            or 0
        )
        stmt = (
            select(StrategyConversationProposal)
            .where(*filters)
            .order_by(StrategyConversationProposal.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(stmt).all()), total

    def open_drafts(
        self,
        conversation_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[StrategyConversationProposal]:
        items, _ = self.list_for_conversation(
            conversation_id,
            organization_id=organization_id,
            user_id=user_id,
            status=StrategyProposalStatus.DRAFT,
            limit=20,
        )
        return items


class StrategyVersionConversationLinkRepository(
    SQLAlchemyRepository[StrategyVersionConversationLink]
):
    model = StrategyVersionConversationLink

    def get_for_proposal(
        self,
        proposal_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
    ) -> StrategyVersionConversationLink | None:
        stmt = select(StrategyVersionConversationLink).where(
            StrategyVersionConversationLink.proposal_id == proposal_id,
            StrategyVersionConversationLink.organization_id == organization_id,
        )
        return self._session.scalar(stmt)

    def get_for_version(
        self,
        strategy_version_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
    ) -> StrategyVersionConversationLink | None:
        stmt = select(StrategyVersionConversationLink).where(
            StrategyVersionConversationLink.strategy_version_id == strategy_version_id,
            StrategyVersionConversationLink.organization_id == organization_id,
        )
        return self._session.scalar(stmt)
