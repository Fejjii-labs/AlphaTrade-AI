"""HTTP contracts for autonomous trading settings (issue #284)."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool

from app.schemas.common import StrictModel

AutonomousStrategyScope = Literal["selected", "all_eligible"]


class AutonomousStrategyOption(StrictModel):
    strategy_id: UUID
    name: str
    # Current version lifecycle is approved/active, and the paper gate passed.
    # The bot only trades a strategy with both; the guard re-checks at decision time.
    approved: bool = False
    paper_eligible: bool = False


class DemoLeverageOverride(StrictModel):
    leverage: int | None = Field(default=None, ge=1, le=20)
    margin_mode: Literal["isolated", "cross"] | None = None


class AutonomousTradingSettingsView(StrictModel):
    organization_id: UUID
    user_id: UUID
    daily_loss_limit_percent: Decimal
    max_trades_per_day: int
    risk_per_trade_percent: Decimal | None = None
    # Legacy single strategy: first entry of strategy_ids (deprecated).
    strategy_id: UUID | None = None
    strategy_ids: list[UUID] = Field(default_factory=list)
    strategy_scope: AutonomousStrategyScope = "selected"
    symbol: str | None = None
    using_defaults: bool = False
    # Account cap counts all trades; the bot is held to min(bot, account).
    account_max_trades_per_day: int
    effective_max_trades_per_day: int
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
    # BloFin demo only: the app sets this on the demo venue before an entry.
    demo_leverage: int = 10
    demo_margin_mode: Literal["isolated", "cross"] = "isolated"
    demo_leverage_overrides: dict[str, DemoLeverageOverride] = Field(default_factory=dict)
    demo_leverage_using_default: bool = True
    max_demo_leverage_ceiling: int = 20


class AutonomousTradingSettingsUpdate(StrictModel):
    daily_loss_limit_percent: Decimal | None = None
    max_trades_per_day: int | None = Field(default=None, ge=1, le=1000)
    risk_per_trade_percent: Decimal | None = Field(default=None, gt=0, le=5)
    clear_risk_per_trade_percent: bool = False
    # Legacy single selection; saved as [strategy_id]. Do not send with strategy_ids.
    strategy_id: UUID | None = None
    strategy_ids: list[UUID] | None = Field(default=None, max_length=50)
    strategy_scope: AutonomousStrategyScope | None = None
    symbol: str | None = Field(default=None, min_length=3, max_length=32)
    # Strict: only JSON true/false, never "yes"/1 (Griller #356 review).
    internal_paper_enabled: StrictBool | None = None
    # Range checks run in the service so the API returns stable 422 codes.
    demo_leverage: int | None = None
    demo_margin_mode: str | None = None
    demo_leverage_overrides: dict[str, dict[str, int | str | None]] | None = None
    clear_demo_leverage_overrides: bool = False
