"""Journal performance dashboard contract — calendar, curve, and score read model.

Deterministic, record-only aggregates over the caller's CLOSED canonical
``journal_trades``. ``None`` means "not computable from recorded data" and is
never reported as zero. No field here is estimated, fetched, or sampled.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field

from app.schemas.common import JournalTradeSource, StrictModel
from app.schemas.journal_statistics import SampleConfidence


class JournalDashboardScope(StrEnum):
    """Account scope: exactly one per response, never mixed.

    ``demo``, ``paper`` and ``real`` are the account scopes; ``backtest``,
    ``imported`` and ``system`` are only read when selected explicitly.
    """

    DEMO = "demo"
    PAPER = "paper"
    REAL = "real"
    BACKTEST = "backtest"
    IMPORTED = "imported"
    SYSTEM = "system"


class JournalDashboardFilters(StrictModel):
    """Filters echoed back so the response is self-describing."""

    scope: JournalDashboardScope
    scope_label: str = Field(description='Display label, e.g. "Demo trades (BloFin demo)".')
    date_from: date | None = None
    date_to: date | None = None
    timezone: str
    source: JournalTradeSource | None = Field(
        default=None, description="Optional single-source narrowing inside the scope."
    )
    included_sources: list[JournalTradeSource] = Field(
        default_factory=list,
        description="Sources the scope reads; venue-split sources are filtered further.",
    )


class JournalDashboardSummary(StrictModel):
    """Headline KPIs over the filtered closed trades."""

    trade_count: int = 0
    pnl_sample_count: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    # Over decided trades (wins + losses); breakeven excluded — same rule as AT-031.
    win_rate: float | None = None
    net_pnl: Decimal | None = None
    gross_profit: Decimal | None = None
    gross_loss: Decimal | None = None  # negative or zero
    profit_factor: float | None = None  # None without losing trades
    average_win: Decimal | None = None
    average_loss: Decimal | None = None  # negative
    win_loss_ratio: float | None = None  # avg win / |avg loss|


class JournalDashboardDay(StrictModel):
    """One local calendar day with at least one closed trade."""

    date: date
    net_pnl: Decimal | None = None  # None when no trade that day recorded PnL
    trade_count: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float | None = None


class JournalDashboardCurvePoint(StrictModel):
    """End-of-day cumulative net PnL (days with recorded PnL only)."""

    date: date
    daily_net_pnl: Decimal
    cumulative_net_pnl: Decimal


class JournalDashboardRisk(StrictModel):
    """Drawdown family over the per-trade cumulative net PnL curve (baseline 0)."""

    max_drawdown: Decimal | None = None  # positive magnitude; 0 when never below a peak
    recovery_factor: float | None = None  # net PnL / max drawdown; None without drawdown
    # 1 - (largest winning day / sum of winning days); None with < 2 winning days.
    consistency: float | None = None
    winning_days: int = 0
    losing_days: int = 0


class JournalDashboardScoreComponent(StrictModel):
    """One radar axis, normalized 0-100 against a configured target."""

    key: str
    label: str
    value: float | None = None  # raw metric value (ratio/fraction)
    score: float | None = Field(default=None, ge=0, le=100)
    target: float | None = None
    note: str | None = None


class JournalDashboardScore(StrictModel):
    """Equal-weight mean of the computable components.

    ``overall`` is None when no component is computable or when sample
    confidence is insufficient (< 5 closed trades); components stay populated.
    """

    overall: float | None = Field(default=None, ge=0, le=100)
    components: list[JournalDashboardScoreComponent]
    missing_components: list[str] = Field(default_factory=list)


class JournalPerformanceDashboardResponse(StrictModel):
    filters: JournalDashboardFilters
    summary: JournalDashboardSummary
    days: list[JournalDashboardDay]
    cumulative: list[JournalDashboardCurvePoint]
    risk: JournalDashboardRisk
    score: JournalDashboardScore
    confidence: SampleConfidence
    truncated: bool
    max_rows: int
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime
