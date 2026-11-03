"""Per-strategy journal trade stats for the Autonomous Trading page.

Read-only. Counts come from canonical journal trades for the calling user,
grouped by linked strategy. Backtest rows are left out so totals describe
paper/demo and manual trades only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.autonomous_trading_settings import AutonomousTradingSettingsRow
from app.db.models import JournalTrade, SetupDetectionRecord, UserStrategy
from app.schemas.autonomous_strategy_stats import (
    AutonomousStrategyStatsView,
    StrategyStatsRow,
    StrategyTradeStats,
)
from app.schemas.common import JournalTradeSource, JournalTradeStatus, TradeResult

EXCLUDED_SOURCES = (JournalTradeSource.BACKTEST,)


class AutonomousStrategyStatsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def view(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> AutonomousStrategyStatsView:
        strategies = self._session.scalars(
            select(UserStrategy)
            .where(
                UserStrategy.organization_id == organization_id,
                UserStrategy.user_id == user_id,
            )
            .order_by(UserStrategy.enabled.desc(), UserStrategy.name.asc())
        ).all()
        stats = self._trade_stats(organization_id=organization_id, user_id=user_id)
        setups = self._last_setups(
            organization_id=organization_id,
            setup_names={row.setup_type.value for row in strategies},
        )
        selected = self._session.scalar(
            select(AutonomousTradingSettingsRow.strategy_id).where(
                AutonomousTradingSettingsRow.organization_id == organization_id,
                AutonomousTradingSettingsRow.user_id == user_id,
            )
        )
        return AutonomousStrategyStatsView(
            strategies=[
                StrategyStatsRow(
                    strategy_id=row.id,
                    name=row.name,
                    setup_type=row.setup_type.value,
                    enabled=bool(row.enabled),
                    paper_eligible=bool(row.paper_eligible),
                    bot_selected=selected == row.id,
                    last_setup_at=setups.get(row.setup_type.value),
                    stats=stats.get(row.id, StrategyTradeStats()),
                )
                for row in strategies
            ],
            unattributed=stats.get(None, StrategyTradeStats()),
            excluded_sources=[source.value for source in EXCLUDED_SOURCES],
        )

    def _trade_stats(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> dict[uuid.UUID | None, StrategyTradeStats]:
        closed = JournalTrade.status == JournalTradeStatus.CLOSED
        rows = self._session.execute(
            select(
                JournalTrade.user_strategy_id,
                func.sum(case((closed, 1), else_=0)),
                func.sum(case((closed & (JournalTrade.result == TradeResult.WIN), 1), else_=0)),
                func.sum(case((closed & (JournalTrade.result == TradeResult.LOSS), 1), else_=0)),
                func.sum(case((closed, JournalTrade.net_pnl), else_=None)),
                func.sum(case((JournalTrade.status == JournalTradeStatus.OPEN, 1), else_=0)),
                func.max(func.coalesce(JournalTrade.exit_time, JournalTrade.entry_time)),
            )
            .where(
                JournalTrade.organization_id == organization_id,
                JournalTrade.user_id == user_id,
                JournalTrade.source.not_in(EXCLUDED_SOURCES),
            )
            .group_by(JournalTrade.user_strategy_id)
        ).all()
        return {
            strategy_id: _stats(
                closed_trades=int(closed_count or 0),
                wins=int(wins or 0),
                losses=int(losses or 0),
                net_pnl=net_pnl,
                open_positions=int(open_count or 0),
                last_trade_at=last_at,
            )
            for strategy_id, closed_count, wins, losses, net_pnl, open_count, last_at in rows
        }

    def _last_setups(
        self, *, organization_id: uuid.UUID, setup_names: set[str]
    ) -> dict[str, datetime]:
        if not setup_names:
            return {}
        rows = self._session.execute(
            select(SetupDetectionRecord.setup_name, func.max(SetupDetectionRecord.detected_at))
            .where(
                SetupDetectionRecord.organization_id == organization_id,
                SetupDetectionRecord.setup_name.in_(setup_names),
            )
            .group_by(SetupDetectionRecord.setup_name)
        ).all()
        return {name: at for name, at in rows if at is not None}


def _stats(
    *,
    closed_trades: int,
    wins: int,
    losses: int,
    net_pnl: Decimal | None,
    open_positions: int,
    last_trade_at: datetime | None,
) -> StrategyTradeStats:
    return StrategyTradeStats(
        closed_trades=closed_trades,
        wins=wins,
        losses=losses,
        win_rate=round(wins / closed_trades, 4) if closed_trades else None,
        net_pnl=Decimal(net_pnl) if net_pnl is not None else Decimal("0"),
        open_positions=open_positions,
        last_trade_at=last_trade_at,
    )
