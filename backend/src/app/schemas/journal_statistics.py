"""Journal statistics schemas (AT-031 — Journal Statistics & Setup Analytics v1).

Deterministic, record-only aggregates over canonical ``journal_trades`` (AT-030).
Statistics are computed exclusively from recorded values — never from live market
I/O — and carry explicit sample-size confidence and data-coverage warnings so
small or partially populated samples cannot be mistaken for robust evidence.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from app.schemas.common import (
    JournalEntryMethod,
    JournalTradeSource,
    MarketRegime,
    StrictModel,
)

# --------------------------------------------------------------------------- #
# Dimensions
# --------------------------------------------------------------------------- #


class JournalStatsGroupBy(StrEnum):
    """Grouping dimension for journal trade statistics."""

    OVERALL = "overall"
    SETUP = "setup"  # by setup name (all versions combined)
    SETUP_VERSION = "setup_version"  # by setup definition id (immutable name+version)
    STRATEGY = "strategy"  # by user strategy
    STRATEGY_VERSION = "strategy_version"  # by immutable user strategy version
    SYMBOL = "symbol"
    TIMEFRAME = "timeframe"
    MARKET_REGIME = "market_regime"
    SOURCE = "source"
    ENTRY_METHOD = "entry_method"  # AT-033: how the journal row was created
    RULE_COMPLIANCE = "rule_compliance"
    EXECUTION_ACTOR = "execution_actor"


class TradeRuleCompliance(StrEnum):
    """Derived per-trade rule-compliance classification.

    Precedence (conservative, worst assessment wins):
    ``violated`` > ``partial`` > ``compliant`` > ``unassessed``. A trade with no
    rule checks, or only ``unassessed``/``not_applicable`` checks, is
    ``unassessed`` — never silently counted as compliant.
    """

    COMPLIANT = "compliant"
    PARTIAL = "partial"
    VIOLATED = "violated"
    UNASSESSED = "unassessed"


class ExecutionActor(StrEnum):
    """Derived human-vs-system execution dimension.

    Classified by decision authority over the recorded trade:
    ``human`` — manual entries, imported human history, human-approved
    proposal-flow paper executions, manual demo tests, and BloFin demo hand
    trades; ``system`` — automated paper-validation, backtest, and
    system-generated trades. An unmapped source is omitted.
    """

    HUMAN = "human"
    SYSTEM = "system"


class SampleConfidence(StrEnum):
    """Coarse confidence label derived from closed-trade sample size."""

    INSUFFICIENT = "insufficient"  # < 5 closed trades
    LOW = "low"  # 5-19
    MODERATE = "moderate"  # 20-49
    HIGH = "high"  # >= 50


class JournalStatsWarningCode(StrEnum):
    """Machine-readable warning codes attached to statistics results."""

    LOW_SAMPLE = "low_sample"
    NO_CLOSED_TRADES = "no_closed_trades"
    NO_DECIDED_TRADES = "no_decided_trades"
    MISSING_PNL = "missing_pnl"
    MISSING_RISK = "missing_risk"
    NO_LOSING_TRADES = "no_losing_trades"
    PARTIAL_EXCURSION_DATA = "partial_excursion_data"
    PARTIAL_CAPTURE_DATA = "partial_capture_data"
    PARTIAL_TIMING_DATA = "partial_timing_data"
    PARTIAL_MISSED_PROFIT_DATA = "partial_missed_profit_data"
    RESULT_TRUNCATED = "result_truncated"


class JournalStatsWarning(StrictModel):
    """One confidence/data-coverage warning."""

    code: JournalStatsWarningCode
    message: str


# --------------------------------------------------------------------------- #
# Filters (echoed back so results are self-describing)
# --------------------------------------------------------------------------- #


class JournalStatsFilters(StrictModel):
    """Filters applied to a statistics computation (closed trades only)."""

    source: JournalTradeSource | None = None
    entry_method: JournalEntryMethod | None = None
    symbol: str | None = Field(default=None, max_length=30)
    timeframe: str | None = Field(default=None, max_length=8)
    market_regime: MarketRegime | None = None
    setup_id: UUID | None = None
    user_strategy_id: UUID | None = None
    strategy_version_id: UUID | None = None
    rule_compliance: TradeRuleCompliance | None = None
    execution_actor: ExecutionActor | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


class JournalTradeStatsMetrics(StrictModel):
    """Deterministic aggregate metrics for one set of closed journal trades.

    Monetary aggregates are computed only over trades with the relevant value
    recorded; each family carries its own sample count so partial coverage is
    always visible. ``None`` means "not computable from recorded data" — it is
    never silently reported as zero.
    """

    trade_count: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    # Win rate over decided trades (wins + losses); breakeven excluded.
    win_rate: float | None = None

    # PnL family (trades with net_pnl recorded).
    pnl_sample_count: int = 0
    net_pnl_total: Decimal | None = None
    gross_pnl_total: Decimal | None = None
    expectancy: Decimal | None = None  # mean net PnL per trade with recorded PnL
    average_winner: Decimal | None = None
    average_loser: Decimal | None = None
    profit_factor: float | None = None  # gross wins / |gross losses|; None without losses

    # R-multiple family (trades with net_pnl and planned_risk_amount > 0).
    r_sample_count: int = 0
    average_r: float | None = None

    # Cost impact (sums over recorded values).
    cost_sample_count: int = 0
    fees_total: Decimal | None = None
    funding_total: Decimal | None = None
    slippage_total: Decimal | None = None
    total_costs: Decimal | None = None

    # Excursions — only when deterministic recorded values exist.
    mfe_sample_count: int = 0
    average_mfe_amount: Decimal | None = None
    mae_sample_count: int = 0
    average_mae_amount: Decimal | None = None

    # Available vs realized profit (trades with net_pnl and available_profit).
    capture_sample_count: int = 0
    available_profit_total: Decimal | None = None
    realized_on_available_total: Decimal | None = None
    average_realized_vs_available_pct: float | None = None

    confidence: SampleConfidence = SampleConfidence.INSUFFICIENT
    warnings: list[JournalStatsWarning] = Field(default_factory=list)


class JournalStatsBucket(StrictModel):
    """Metrics for one group (e.g. one setup version, one symbol)."""

    # Stable key: UUID string, enum value, raw dimension value, or "unassigned".
    key: str
    group_id: UUID | None = None
    label: str
    metrics: JournalTradeStatsMetrics


class JournalStatsResponse(StrictModel):
    """Grouped journal trade statistics with overall aggregate and provenance."""

    group_by: JournalStatsGroupBy
    filters: JournalStatsFilters
    overall: JournalTradeStatsMetrics
    buckets: list[JournalStatsBucket]
    total_buckets: int
    limit: int
    offset: int
    # True when the bounded row cap was hit; aggregates then cover only the
    # oldest max_rows closed trades in range and must be treated as partial.
    truncated: bool = False
    max_rows: int
    generated_at: datetime
