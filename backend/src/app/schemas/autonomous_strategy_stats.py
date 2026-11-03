"""Read-only per-strategy trade stats for the Autonomous Trading page."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrategyTradeStats(BaseModel):
    """Journal-trade totals for one strategy (or for trades with no strategy)."""

    model_config = ConfigDict(extra="forbid")

    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    # wins / closed_trades; None until a trade has closed.
    win_rate: float | None = None
    net_pnl: Decimal = Decimal("0")
    open_positions: int = 0
    last_trade_at: datetime | None = None


class StrategyStatsRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: UUID
    name: str
    setup_type: str
    enabled: bool
    paper_eligible: bool
    # True when this is the strategy saved in the autonomous bot settings.
    bot_selected: bool
    # Newest scanner detection for this strategy's setup type (organization scope).
    last_setup_at: datetime | None = None
    stats: StrategyTradeStats


class AutonomousStrategyStatsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategies: list[StrategyStatsRow] = Field(default_factory=list)
    # Journal trades with no linked strategy, or linked to a strategy the caller
    # does not own in this organization (deleted/foreign), so totals still add up.
    unattributed: StrategyTradeStats
    # Journal sources left out of every total.
    excluded_sources: list[str] = Field(default_factory=list)
    # Allowlist of journal sources counted in the totals.
    counted_sources: list[str] = Field(default_factory=list)
