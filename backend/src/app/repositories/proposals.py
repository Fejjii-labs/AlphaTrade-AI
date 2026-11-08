"""Trade proposal persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.db.canonical_trade_plans import PLAN_ROOT_ANALYSIS
from app.db.models import TradeProposal
from app.repositories.base import SQLAlchemyRepository, require_organization_id


class ProposalRepository(SQLAlchemyRepository[TradeProposal]):
    model = TradeProposal

    def list_proposals(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TradeProposal], int]:
        filters = []
        filters.append(TradeProposal.organization_id == require_organization_id(organization_id))
        if user_id is not None:
            filters.append(TradeProposal.user_id == user_id)
        filters.append(TradeProposal.plan_root_kind == PLAN_ROOT_ANALYSIS)

        count_stmt = select(func.count()).select_from(TradeProposal)
        list_stmt = select(TradeProposal).order_by(TradeProposal.created_at.desc())
        if filters:
            count_stmt = count_stmt.where(*filters)
            list_stmt = list_stmt.where(*filters)
        total = int(self._session.scalar(count_stmt) or 0)
        return list(self._session.scalars(list_stmt.limit(limit).offset(offset)).all()), total

    def get_scoped(
        self,
        proposal_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> TradeProposal | None:
        stmt = select(TradeProposal).where(TradeProposal.id == proposal_id)
        stmt = stmt.where(TradeProposal.organization_id == require_organization_id(organization_id))
        if user_id is not None:
            stmt = stmt.where(TradeProposal.user_id == user_id)
        return self._session.scalar(stmt)
