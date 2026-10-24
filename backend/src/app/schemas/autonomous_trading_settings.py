"""HTTP contracts for autonomous trading settings (issue #284)."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import StrictModel


class AutonomousStrategyOption(StrictModel):
    strategy_id: UUID
    name: str


class AutonomousTradingSettingsView(StrictModel):
    organization_id: UUID
    user_id: UUID
    daily_loss_limit_percent: Decimal
    max_trades_per_day: int
    risk_per_trade_percent: Decimal | None = None
    strategy_id: UUID | None = None
    symbol: str | None = None
    using_defaults: bool = False
    strategy_options: list[AutonomousStrategyOption] = Field(default_factory=list)
    symbol_options: list[str] = Field(default_factory=list)
    max_daily_loss_percent_ceiling: Decimal = Field(default=Decimal("6"))
    max_risk_per_trade_percent_ceiling: Decimal = Field(default=Decimal("5"))
    internal_paper_enabled: bool = False
    internal_paper_env_ceiling: bool | None = None
    internal_paper_effective: bool = False
    worker_runtime_health: Literal["RUNNING", "STALE", "UNAVAILABLE"] = "UNAVAILABLE"
    worker_heartbeat_age_seconds: float | None = None
    worker_flags_stale: bool = True
    autonomy_mode: str = ""


class AutonomousTradingSettingsUpdate(StrictModel):
    daily_loss_limit_percent: Decimal | None = None
    max_trades_per_day: int | None = Field(default=None, ge=1, le=1000)
    risk_per_trade_percent: Decimal | None = Field(default=None, gt=0, le=5)
    clear_risk_per_trade_percent: bool = False
    strategy_id: UUID | None = None
    symbol: str | None = Field(default=None, min_length=3, max_length=32)
    internal_paper_enabled: bool | None = None
