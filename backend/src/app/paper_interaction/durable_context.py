"""Read-only paper facts for Telegram discussion. Does not trade or mint."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import JournalTrade, Order, Position, UserStrategy, UserStrategyVersion
from app.learning_attribution.contracts import LearningVenueMode
from app.paper_evaluation.contracts import PaperEvaluationSummary
from app.paper_evaluation.journal_facts import SqlAlchemyJournalExcursionPort
from app.paper_evaluation.query import PaperEvaluationQueryService
from app.paper_interaction.bridge import EvaluationLearningContext
from app.persistence.attribution_postgres import PostgresAttributionStore
from app.persistence.eligibility_postgres import latest_evaluations_for_organization
from app.persistence.paper_evaluation_postgres import PostgresPaperEvaluationStore
from app.schemas.common import ExecutionMode, OrderStatus, PositionStatus
from app.signal_fusion.action_eligibility import ActionEligibilityEvaluation
from app.telegram_paper_agent.contracts import (
    JournalOutcomeView,
    LearningSummaryView,
    PaperTradeStatusView,
    StrategyDraftView,
)

logger = structlog.get_logger("paper_interaction.durable_context")

_OPEN_ORDER_STATUSES = (
    OrderStatus.PENDING,
    OrderStatus.OPEN,
    OrderStatus.PARTIALLY_FILLED,
)


class DurablePaperContext:
    """Tenant-scoped journal, paper status, and strategy reads."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def paper_trade_status(self, *, organization_id: UUID, user_id: UUID) -> PaperTradeStatusView:
        try:
            with self._session_factory() as session:
                positions = _count_open_positions(
                    session, organization_id=organization_id, user_id=user_id
                )
                orders = _count_open_paper_orders(
                    session, organization_id=organization_id, user_id=user_id
                )
        except Exception:
            logger.warning("telegram_paper_status_unavailable")
            return PaperTradeStatusView(
                open_positions=0,
                open_orders=0,
                summary="Paper status is unavailable. No live order was read.",
            )
        return PaperTradeStatusView(
            open_positions=positions,
            open_orders=orders,
            summary="Paper portfolio only. Telegram cannot place or close an order.",
        )

    def journal_outcome(
        self, *, organization_id: UUID, user_id: UUID, trade_id: UUID | None
    ) -> JournalOutcomeView | None:
        try:
            with self._session_factory() as session:
                trade = _load_journal(
                    session,
                    organization_id=organization_id,
                    user_id=user_id,
                    trade_id=trade_id,
                )
        except Exception:
            logger.warning("telegram_journal_unavailable")
            return None
        if trade is None:
            return None
        pnl = None if trade.net_pnl is None else format(trade.net_pnl, "f")
        status = trade.status.value
        result = trade.result.value
        return JournalOutcomeView(
            trade_id=trade.id,
            symbol=trade.symbol,
            status=status,
            result=result,
            net_pnl=pnl,
            summary="Paper trade only, not a live fill.",
        )

    def learning_summary(
        self, *, organization_id: UUID, user_id: UUID, candidate_id: UUID | None
    ) -> LearningSummaryView | None:
        del organization_id, user_id, candidate_id
        return None

    def strategy_discussion(
        self, *, organization_id: UUID, user_id: UUID, strategy_id: UUID | None
    ) -> StrategyDraftView | None:
        try:
            with self._session_factory() as session:
                found = _load_strategy(
                    session,
                    organization_id=organization_id,
                    user_id=user_id,
                    strategy_id=strategy_id,
                )
        except Exception:
            logger.warning("telegram_strategy_discussion_unavailable")
            return None
        if found is None:
            return None
        strategy, version = found
        return StrategyDraftView(
            strategy_id=strategy.id,
            version_id=version.id,
            summary=(
                f"{strategy.name} version {version.version}. "
                "Discussion only. Telegram cannot approve, compile, or activate."
            ),
        )


class SessionPaperEvaluationQuery:
    """Open a short read session per learning summary. Does not write."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def summary(
        self,
        *,
        organization_id: UUID,
        learning_venue_mode: LearningVenueMode | None = None,
        eligibility: tuple[ActionEligibilityEvaluation, ...] = (),
        generated_at: datetime | None = None,
        narrative: str | None = None,
    ) -> PaperEvaluationSummary:
        with self._session_factory() as session:
            resolved = eligibility or latest_evaluations_for_organization(
                session, organization_id=organization_id
            )
            query = PaperEvaluationQueryService(
                PostgresPaperEvaluationStore(session),
                attribution_store=PostgresAttributionStore(session),
                journal=SqlAlchemyJournalExcursionPort(session),
            )
            return query.summary(
                organization_id=organization_id,
                learning_venue_mode=learning_venue_mode,
                eligibility=resolved,
                generated_at=generated_at,
                narrative=narrative,
            )


def build_durable_discussion_context(
    session_factory: sessionmaker[Session],
) -> EvaluationLearningContext:
    """Journal and paper status from PostgreSQL. Learning from evaluation facts."""

    return EvaluationLearningContext(
        DurablePaperContext(session_factory),
        SessionPaperEvaluationQuery(session_factory),
    )


def _count_open_positions(session: Session, *, organization_id: UUID, user_id: UUID) -> int:
    found = session.scalar(
        select(func.count())
        .select_from(Position)
        .where(
            Position.organization_id == organization_id,
            Position.user_id == user_id,
            Position.status == PositionStatus.OPEN,
        )
    )
    return int(found or 0)


def _count_open_paper_orders(session: Session, *, organization_id: UUID, user_id: UUID) -> int:
    found = session.scalar(
        select(func.count())
        .select_from(Order)
        .where(
            Order.organization_id == organization_id,
            Order.user_id == user_id,
            Order.mode == ExecutionMode.PAPER,
            Order.status.in_(_OPEN_ORDER_STATUSES),
        )
    )
    return int(found or 0)


def _load_journal(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    trade_id: UUID | None,
) -> JournalTrade | None:
    stmt = select(JournalTrade).where(
        JournalTrade.organization_id == organization_id,
        JournalTrade.user_id == user_id,
    )
    if trade_id is not None:
        stmt = stmt.where(JournalTrade.id == trade_id)
    else:
        stmt = stmt.order_by(JournalTrade.created_at.desc())
    return session.scalars(stmt.limit(1)).first()


def _load_strategy(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    strategy_id: UUID | None,
) -> tuple[UserStrategy, UserStrategyVersion] | None:
    stmt = (
        select(UserStrategy, UserStrategyVersion)
        .join(UserStrategyVersion, UserStrategyVersion.strategy_id == UserStrategy.id)
        .where(
            UserStrategy.organization_id == organization_id,
            UserStrategy.user_id == user_id,
        )
    )
    if strategy_id is not None:
        stmt = stmt.where(
            or_(UserStrategy.id == strategy_id, UserStrategyVersion.id == strategy_id)
        )
    else:
        stmt = stmt.order_by(UserStrategyVersion.created_at.desc())
    row = session.execute(stmt.limit(1)).first()
    if row is None:
        return None
    strategy, version = row
    return strategy, version
