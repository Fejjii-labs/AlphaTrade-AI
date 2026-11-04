"""Read-only bounds for the Autonomous Trading settings page.

Every number here is read from the code ceiling, the request schema, or
``Settings`` that the server already enforces. The page renders these so it has
no hard-coded limits of its own; the server still validates every write.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from app.schemas.common import StrictModel

BoundSource = Literal["code_ceiling", "request_schema", "environment", "risk_policy_profile"]


class NumericBound(StrictModel):
    """Inclusive ``max``; ``min`` is exclusive when ``min_exclusive`` is true."""

    min: Decimal | None = None
    min_exclusive: bool = False
    max: Decimal | None = None
    editable: bool
    source: BoundSource


class StopLossBandBounds(StrictModel):
    """Per-trade stop-distance loss as a percent of equity (BloFin demo plans)."""

    configured_min_percent: Decimal
    configured_max_percent: Decimal
    floor_percent: Decimal
    ceiling_percent: Decimal
    # False when the configured band falls outside floor..ceiling; demo plans then refuse.
    configured_valid: bool
    editable: bool = False
    source: BoundSource = "environment"


class LeverageBounds(StrictModel):
    profile_max_leverage_ceiling: Decimal
    demo_hard_cap: Decimal
    # Lower of the hard cap and GOVERNED_DEMO_MAX_LEVERAGE.
    demo_effective_max: Decimal


class ProfileCapBounds(StrictModel):
    """Code ceilings that a governed risk-policy profile is validated against."""

    kind: str
    risk_per_trade_percent_ceiling: Decimal
    notional_percent_ceiling: Decimal
    aggregate_open_risk_percent_ceiling: Decimal
    daily_loss_percent_ceiling: Decimal
    max_leverage_ceiling: Decimal
    editable: bool = False
    source: BoundSource = "risk_policy_profile"


class AutonomousSettingsBoundsView(StrictModel):
    bot_risk_per_trade_percent: NumericBound
    bot_daily_loss_limit_percent: NumericBound
    bot_max_trades_per_day: NumericBound
    account_max_risk_per_trade_percent: NumericBound
    account_max_trades_per_day: NumericBound
    stop_loss_band: StopLossBandBounds
    leverage: LeverageBounds
    profiles: list[ProfileCapBounds]
    real_trading_enabled: Literal[False] = False
