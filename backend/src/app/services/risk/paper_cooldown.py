"""The existing paper loss cooldown, including canonical journal closes."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import JournalTrade, Position
from app.schemas.common import JournalTradeSource, JournalTradeStatus, PositionStatus
from app.services.risk.mirror_economics import journal_mirror_economics


def paper_loss_cooldown_active(
    session: Session, *, organization_id: UUID, user_id: UUID, seconds: int, now: datetime
) -> bool:
    if seconds <= 0:
        return False
    since = now - timedelta(seconds=seconds)
    legacy = session.scalar(
        select(Position.id)
        .where(
            Position.organization_id == organization_id,
            Position.user_id == user_id,
            Position.status == PositionStatus.CLOSED,
            Position.closed_at >= since,
            Position.realized_pnl < 0,
        )
        .limit(1)
    )
    canonical = session.scalar(
        select(JournalTrade.id)
        .where(
            JournalTrade.organization_id == organization_id,
            JournalTrade.user_id == user_id,
            JournalTrade.status == JournalTradeStatus.CLOSED,
            JournalTrade.source == JournalTradeSource.PAPER_EXECUTION,
            JournalTrade.execution_lifecycle_id.is_not(None),
            JournalTrade.exit_time >= since,
            JournalTrade.net_pnl < 0,
            ~journal_mirror_economics(),
        )
        .limit(1)
    )
    return legacy is not None or canonical is not None
