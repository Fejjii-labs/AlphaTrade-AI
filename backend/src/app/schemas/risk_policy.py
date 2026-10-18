"""HTTP contracts for the grade-based risk policy."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field

from app.schemas.common import StrictModel


class RiskPolicyProposeRequest(StrictModel):
    """A full profile. Omitted numbers are not filled in."""

    kind: str
    reason: str = Field(min_length=3, max_length=500)
    risk_percent_a_plus: Decimal
    risk_percent_a: Decimal
    risk_percent_b_plus: Decimal
    risk_percent_b: Decimal
    risk_percent_c: Decimal
    max_leverage: Decimal
    daily_loss_limit_percent: Decimal
    max_aggregate_open_risk_percent: Decimal
    max_open_positions: int
    max_notional_per_trade_percent: Decimal
    max_aggregate_notional_percent: Decimal
    min_stop_atr_multiple: Decimal
    min_stop_absolute_floor: Decimal
    max_cost_share_of_r: Decimal
    drawdown_breaker_percent: Decimal
    consecutive_loss_limit: int
    autonomous_internal_paper_enabled: bool = False
    autonomous_blofin_demo_enabled: bool = False


class RiskPolicyApplyRequest(StrictModel):
    """``confirm`` is required when the change loosens a limit."""

    confirm: bool = False


class RiskPolicyProposalResponse(StrictModel):
    id: UUID
    kind: str
    actor_channel: str
    content_hash: str
    reason: str


class RiskPolicyVersionResponse(StrictModel):
    id: UUID
    kind: str
    version_number: int
    change_class: str
    effective_at: datetime
    actor_channel: str
    content_hash: str
    applies_to_new_trades_only: bool = True


class RiskPolicyView(StrictModel):
    kind: str
    source: str
    version_id: UUID | None = None
    version_number: int | None = None
    effective_at: datetime | None = None
    change_class: str | None = None
    content_hash: str | None = None
    profile: dict[str, str]
    scheduled_version_number: int | None = None
    scheduled_effective_at: datetime | None = None
    applies_to_new_trades_only: bool = True
    real_trading_enabled: bool = False
