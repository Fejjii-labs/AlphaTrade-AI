"""Canonical journal trade schemas (AT-030/AT-032 — Journal Intelligence).

Record-only intelligence layer: these schemas never carry execution authority.
Excursion metrics (MFE/MAE, available vs realized profit) are deterministic
values supplied by callers or HistoricalCandle replay — no live market I/O.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field, computed_field

from app.schemas.common import (
    JournalEvidenceKind,
    JournalObservationCategory,
    JournalTradeSource,
    JournalTradeStatus,
    MarketRegime,
    ORMModel,
    RuleComplianceStatus,
    StrictModel,
    Symbol,
    Timeframe,
    TradeDirection,
    TradeResult,
)
from app.schemas.manual_demo import ManualDemoAttempt
from app.services.display_names import display_name


class PlannedTarget(StrictModel):
    """One planned take-profit target."""

    price: Decimal
    size_fraction: float = Field(default=1.0, ge=0.0, le=1.0)
    label: str | None = Field(default=None, max_length=120)


class JournalTradeLinks(StrictModel):
    """Optional links from a journal trade to existing records (tenant-checked)."""

    linked_position_id: UUID | None = None
    linked_paper_trade_id: UUID | None = None
    linked_proposal_id: UUID | None = None
    linked_order_id: UUID | None = None
    linked_backtest_trade_id: UUID | None = None
    linked_journal_entry_id: UUID | None = None
    linked_paper_validation_run_id: UUID | None = None


class JournalTradeCreate(StrictModel):
    """Request to create a canonical journal trade."""

    source: JournalTradeSource = JournalTradeSource.MANUAL
    status: JournalTradeStatus = JournalTradeStatus.PLANNED
    symbol: Symbol
    exchange: str | None = Field(default=None, max_length=40)
    timeframe: Timeframe
    market_regime: MarketRegime = MarketRegime.UNKNOWN
    regime_notes: str | None = Field(default=None, max_length=2000)
    setup_id: UUID | None = None
    user_strategy_id: UUID | None = None
    strategy_version_id: UUID | None = None
    strategy_label: str | None = Field(default=None, max_length=120)
    direction: TradeDirection
    thesis: str | None = Field(default=None, max_length=8000)
    trigger: str | None = Field(default=None, max_length=4000)
    entry_plan: str | None = Field(default=None, max_length=4000)
    invalidation: str | None = Field(default=None, max_length=4000)
    planned_entry_price: Decimal | None = None
    planned_stop_price: Decimal | None = None
    planned_targets: list[PlannedTarget] = Field(default_factory=list)
    runner_enabled: bool = False
    runner_plan: str | None = Field(default=None, max_length=2000)
    planned_risk_amount: Decimal | None = None
    entry_price: Decimal | None = None
    entry_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_time: datetime | None = None
    exit_reason: str | None = Field(default=None, max_length=60)
    size: Decimal | None = None
    leverage: Decimal | None = None
    fees: Decimal | None = None
    funding: Decimal | None = None
    slippage: Decimal | None = None
    gross_pnl: Decimal | None = None
    net_pnl: Decimal | None = None
    result: TradeResult = TradeResult.OPEN
    notes: str | None = Field(default=None, max_length=8000)
    tags: list[str] = Field(default_factory=list)
    links: JournalTradeLinks = Field(default_factory=JournalTradeLinks)
    external_ref: str | None = Field(default=None, max_length=255)


class JournalTradeAnnotation(StrictModel):
    """Strategy, grade, and notes on a mirrored BloFin demo journal trade.

    Omitted fields stay as they are. An empty string clears strategy, grade,
    or notes. Venue prices, size, fees, and P&L are not accepted here.
    """

    strategy_label: str | None = Field(default=None, max_length=120)
    user_strategy_id: UUID | None = None
    grade: str | None = Field(default=None, max_length=2)
    notes: str | None = Field(default=None, max_length=8000)


class JournalTradeUpdate(StrictModel):
    """Partial update; only provided fields change."""

    status: JournalTradeStatus | None = None
    market_regime: MarketRegime | None = None
    regime_notes: str | None = Field(default=None, max_length=2000)
    setup_id: UUID | None = None
    user_strategy_id: UUID | None = None
    strategy_version_id: UUID | None = None
    strategy_label: str | None = Field(default=None, max_length=120)
    thesis: str | None = Field(default=None, max_length=8000)
    trigger: str | None = Field(default=None, max_length=4000)
    entry_plan: str | None = Field(default=None, max_length=4000)
    invalidation: str | None = Field(default=None, max_length=4000)
    planned_entry_price: Decimal | None = None
    planned_stop_price: Decimal | None = None
    planned_targets: list[PlannedTarget] | None = None
    runner_enabled: bool | None = None
    runner_plan: str | None = Field(default=None, max_length=2000)
    planned_risk_amount: Decimal | None = None
    entry_price: Decimal | None = None
    entry_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_time: datetime | None = None
    exit_reason: str | None = Field(default=None, max_length=60)
    size: Decimal | None = None
    leverage: Decimal | None = None
    fees: Decimal | None = None
    funding: Decimal | None = None
    slippage: Decimal | None = None
    gross_pnl: Decimal | None = None
    net_pnl: Decimal | None = None
    result: TradeResult | None = None
    mfe_price: Decimal | None = None
    mae_price: Decimal | None = None
    mfe_amount: Decimal | None = None
    mae_amount: Decimal | None = None
    available_profit: Decimal | None = None
    realized_vs_available_pct: float | None = None
    excursion_source: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=8000)
    tags: list[str] | None = None
    external_ref: str | None = Field(default=None, max_length=255)


class JournalTradeRead(ORMModel):
    """Full canonical journal trade."""

    id: UUID
    organization_id: UUID
    user_id: UUID
    source: JournalTradeSource
    status: JournalTradeStatus
    symbol: str
    exchange: str | None = None
    timeframe: str
    market_regime: MarketRegime
    regime_notes: str | None = None
    setup_id: UUID | None = None
    user_strategy_id: UUID | None = None
    strategy_version_id: UUID | None = None
    strategy_label: str | None = None
    direction: TradeDirection
    thesis: str | None = None
    trigger: str | None = None
    entry_plan: str | None = None
    invalidation: str | None = None
    planned_entry_price: Decimal | None = None
    planned_stop_price: Decimal | None = None
    planned_targets: list[PlannedTarget] = Field(default_factory=list)
    runner_enabled: bool = False
    runner_plan: str | None = None
    planned_risk_amount: Decimal | None = None
    entry_price: Decimal | None = None
    entry_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_time: datetime | None = None
    exit_reason: str | None = None
    size: Decimal | None = None
    leverage: Decimal | None = None
    fees: Decimal | None = None
    funding: Decimal | None = None
    slippage: Decimal | None = None
    gross_pnl: Decimal | None = None
    net_pnl: Decimal | None = None
    result: TradeResult = TradeResult.OPEN
    mfe_price: Decimal | None = None
    mae_price: Decimal | None = None
    mfe_amount: Decimal | None = None
    mae_amount: Decimal | None = None
    available_profit: Decimal | None = None
    realized_vs_available_pct: float | None = None
    excursion_source: str | None = None
    excursion_data_source: str | None = None
    excursion_is_stale: bool | None = None
    excursion_freshness_note: str | None = None
    excursion_candle_count: int | None = None
    excursion_gaps_detected: int | None = None
    excursion_window_complete: bool | None = None
    excursion_computed_at: datetime | None = None
    notes: str | None = None
    grade: str | None = None
    tags: list[str] = Field(default_factory=list)
    linked_position_id: UUID | None = None
    linked_paper_trade_id: UUID | None = None
    linked_proposal_id: UUID | None = None
    linked_order_id: UUID | None = None
    linked_backtest_trade_id: UUID | None = None
    linked_journal_entry_id: UUID | None = None
    linked_paper_validation_run_id: UUID | None = None
    external_ref: str | None = None
    execution_lifecycle_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def display_name(self) -> str:
        """Trader-facing name, e.g. "Nested · BTCUSDT · Long · 15m"."""
        return display_name(self.strategy_label, self.symbol, self.direction, self.timeframe)


class PaginatedJournalTrades(StrictModel):
    items: list[JournalTradeRead]
    total: int
    limit: int
    offset: int


class JournalTradeEvidenceCreate(StrictModel):
    """Attach evidence (screenshot, chart, note, link, file reference)."""

    kind: JournalEvidenceKind
    ref: str | None = Field(default=None, max_length=1024)
    caption: str | None = Field(default=None, max_length=4000)


class JournalTradeEvidenceRead(ORMModel):
    id: UUID
    journal_trade_id: UUID
    organization_id: UUID
    kind: JournalEvidenceKind
    ref: str | None = None
    caption: str | None = None
    recorded_by: UUID | None = None
    created_at: datetime


class JournalTradeRuleCheckCreate(StrictModel):
    """Record one rule-compliance assessment for a journal trade."""

    rule_key: str = Field(min_length=1, max_length=120)
    rule_source: str | None = Field(default=None, max_length=40)
    status: RuleComplianceStatus = RuleComplianceStatus.UNASSESSED
    notes: str | None = Field(default=None, max_length=4000)
    assessed_at: datetime | None = None


class JournalTradeRuleCheckRead(ORMModel):
    id: UUID
    journal_trade_id: UUID
    organization_id: UUID
    rule_key: str
    rule_source: str | None = None
    status: RuleComplianceStatus
    notes: str | None = None
    assessed_by: UUID | None = None
    assessed_at: datetime | None = None
    created_at: datetime


class JournalTradeObservationCreate(StrictModel):
    """Record a behavioral/process observation for a journal trade."""

    category: JournalObservationCategory
    observation: str = Field(min_length=1, max_length=8000)
    emotion_tags: list[str] = Field(default_factory=list)
    observed_at: datetime | None = None


class JournalTradeObservationRead(ORMModel):
    id: UUID
    journal_trade_id: UUID
    organization_id: UUID
    category: JournalObservationCategory
    observation: str
    emotion_tags: list[str] = Field(default_factory=list)
    recorded_by: UUID | None = None
    observed_at: datetime | None = None
    created_at: datetime


class JournalTradeDetail(StrictModel):
    """Journal trade with its evidence, rule checks, and observations."""

    trade: JournalTradeRead
    manual_demo: ManualDemoAttempt | None = None
    evidence: list[JournalTradeEvidenceRead] = Field(default_factory=list)
    rule_checks: list[JournalTradeRuleCheckRead] = Field(default_factory=list)
    observations: list[JournalTradeObservationRead] = Field(default_factory=list)
