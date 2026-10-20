"""Additive strategy analytics foundation with no writes or active-logic hooks.

JournalTradeRepository supplies the same closed-trade scan as journal statistics.
Its existing pure metric helper owns outcome, PnL, cost, R and excursion math.
The shared PerformanceCalculator owns drawdown and holding-period math; no
PerformanceService snapshot/rollup or secondary trade loader is invoked.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from statistics import median
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ValidationAppError
from app.db.models import JournalTrade, UserStrategy, UserStrategyVersion
from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.repositories.journal_trades import JournalTradeRepository, JournalTradeStatsRow
from app.schemas.common import JournalTradeSource, MarketRegime
from app.schemas.journal_statistics import JournalStatsWarning, JournalStatsWarningCode
from app.schemas.strategy_analytics import (
    NestedMaturityStage,
    StrategyAnalyticsBucket,
    StrategyAnalyticsDimension,
    StrategyAnalyticsFilters,
    StrategyAnalyticsMetrics,
    StrategyAnalyticsReport,
    StrategyAnalyticsSample,
    StrategyAnalyticsWarning,
)
from app.services.journal_statistics_service import _compute_metrics
from app.services.performance.calculator import PerformanceCalculator
from app.services.performance.types import TradeRecord

_ZERO = Decimal("0")
_CHUNK_SIZE = 400
_MONEY_FIELDS = (
    "net_pnl",
    "gross_pnl",
    "fees",
    "funding",
    "slippage",
    "planned_risk_amount",
    "mae_amount",
    "mfe_amount",
)
_COST_FIELDS = ("net_pnl", "gross_pnl", "fees", "funding", "slippage")


def _utc(value: datetime | None) -> datetime | None:
    # SQLite strips timezone information from canonical UTC DateTime columns.
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _costs_reconcile(row: JournalTradeStatsRow) -> bool:
    if (
        row.net_pnl is None
        or row.gross_pnl is None
        or row.fees is None
        or row.funding is None
        or row.slippage is None
    ):
        return False
    return row.gross_pnl - row.fees - row.funding - row.slippage == row.net_pnl


@dataclass(frozen=True)
class _Trade:
    row: JournalTradeStatsRow
    entry_time: datetime | None
    exit_time: datetime | None
    strategy_id: UUID | None
    strategy_version_id: UUID | None
    stage: str | None = None
    link_issue: str | None = None

    def dimension(self, dimension: StrategyAnalyticsDimension) -> str | None:
        match dimension:
            case StrategyAnalyticsDimension.STRATEGY:
                from app.services.blofin_demo_manual_mirror import strategy_group_key

                return strategy_group_key(
                    source=self.row.source,
                    strategy_id=self.strategy_id,
                    strategy_label=self.row.strategy_label,
                )
            case StrategyAnalyticsDimension.STRATEGY_VERSION:
                return str(self.strategy_version_id) if self.strategy_version_id else None
            case StrategyAnalyticsDimension.SYMBOL:
                return self.row.symbol.strip() or None
            case StrategyAnalyticsDimension.TIMEFRAME:
                return self.row.timeframe.strip() or None
            case StrategyAnalyticsDimension.MARKET_REGIME:
                regime = self.row.market_regime
                return regime.value if regime != MarketRegime.UNKNOWN else None
            case StrategyAnalyticsDimension.NESTED_MATURITY_STAGE:
                return self.stage


class StrategyAnalyticsService:
    """Scoped, bounded on-demand reports. No endpoint, persistence or worker wiring.

    Example: ``compute(organization_id=org, user_id=user,
    group_by=(StrategyAnalyticsDimension.STRATEGY_VERSION,
    StrategyAnalyticsDimension.NESTED_MATURITY_STAGE))``.
    """

    def __init__(self, session: Session, *, max_rows: int = 10_000) -> None:
        if max_rows < 1:
            raise ValidationAppError("max_rows must be positive.")
        self._session = session
        self._trades = JournalTradeRepository(session)
        self._max_rows = max_rows
        self._calculator = PerformanceCalculator()

    def compute(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        filters: StrategyAnalyticsFilters | None = None,
        group_by: tuple[StrategyAnalyticsDimension, ...] = (StrategyAnalyticsDimension.STRATEGY,),
        min_sample_size: int = 20,
        limit: int = 50,
        offset: int = 0,
    ) -> StrategyAnalyticsReport:
        if min_sample_size < 1 or limit < 1 or offset < 0:
            raise ValidationAppError(
                "Positive min_sample_size/limit and nonnegative offset required."
            )
        if not group_by or len(set(group_by)) != len(group_by):
            raise ValidationAppError("group_by requires distinct analytics dimensions.")
        if any(dimension not in StrategyAnalyticsDimension for dimension in group_by):
            raise ValidationAppError("Unknown analytics dimension.")
        filters = filters or StrategyAnalyticsFilters()
        # Even a caller's pending mutations must not be autoflushed by these reads.
        with self._session.no_autoflush:
            rows, truncated = self._trades.fetch_stats_rows(
                organization_id=organization_id,
                user_id=user_id,
                # Connectivity tests never enter strategy performance, even if
                # later closed or explicitly requested through a source filter.
                sources=tuple(
                    source
                    for source in JournalTradeSource
                    if source is not JournalTradeSource.MANUAL_DEMO_TEST
                    and (filters.source is None or source is filters.source)
                ),
                symbol=filters.symbol,
                timeframe=filters.timeframe,
                market_regime=filters.market_regime,
                date_from=filters.date_from,
                date_to=filters.date_to,
                max_rows=self._max_rows,
            )
            trades = self._enrich(rows, organization_id=organization_id, user_id=user_id)
        trades = [
            trade
            for trade in trades
            if (filters.strategy_id is None or trade.strategy_id == filters.strategy_id)
            and (
                filters.strategy_version_id is None
                or trade.strategy_version_id == filters.strategy_version_id
            )
            and (
                filters.nested_maturity_stage is None
                or trade.stage == filters.nested_maturity_stage
            )
        ]
        grouped: dict[tuple[str | None, ...], list[_Trade]] = defaultdict(list)
        for trade in trades:
            grouped[tuple(trade.dimension(dimension) for dimension in group_by)].append(trade)
        keys = sorted(
            grouped, key=lambda key: tuple((value is not None, value or "") for value in key)
        )
        return StrategyAnalyticsReport(
            organization_id=organization_id,
            user_id=user_id,
            filters=filters,
            group_by=group_by,
            overall=self._metrics(trades, min_sample_size=min_sample_size, truncated=truncated),
            buckets=[
                StrategyAnalyticsBucket(
                    dimensions=dict(zip(group_by, key, strict=True)),
                    metrics=self._metrics(
                        grouped[key], min_sample_size=min_sample_size, truncated=truncated
                    ),
                )
                for key in keys[offset : offset + limit]
            ],
            total_buckets=len(keys),
            limit=limit,
            offset=offset,
            scanned_trade_count=len(rows),
            truncated=truncated,
            max_rows=self._max_rows,
            min_sample_size=min_sample_size,
            generated_at=datetime.now(UTC),
        )

    def _enrich(
        self, rows: list[JournalTradeStatsRow], *, organization_id: UUID, user_id: UUID
    ) -> list[_Trade]:
        enriched: list[_Trade] = []
        for start in range(0, len(rows), _CHUNK_SIZE):
            chunk = rows[start : start + _CHUNK_SIZE]
            ids = [row.id for row in chunk]
            times = {
                trade_id: (_utc(entry), _utc(exit_))
                for trade_id, entry, exit_ in self._session.execute(
                    select(JournalTrade.id, JournalTrade.entry_time, JournalTrade.exit_time).where(
                        JournalTrade.organization_id == organization_id,
                        JournalTrade.user_id == user_id,
                        JournalTrade.id.in_(ids),
                    )
                )
            }
            links: dict[UUID, list[tuple[UUID, UUID, UUID]]] = defaultdict(list)
            for setup_id, trade_id, strategy_id, version_id in self._session.execute(
                select(
                    BrainSetupRow.id,
                    BrainSetupRow.journal_trade_id,
                    BrainSetupRow.strategy_id,
                    BrainSetupRow.strategy_version_id,
                )
                .join(UserStrategy, UserStrategy.id == BrainSetupRow.strategy_id)
                .join(
                    UserStrategyVersion,
                    (UserStrategyVersion.id == BrainSetupRow.strategy_version_id)
                    & (UserStrategyVersion.strategy_id == UserStrategy.id),
                )
                .where(
                    BrainSetupRow.organization_id == organization_id,
                    UserStrategy.organization_id == organization_id,
                    UserStrategy.user_id == user_id,
                    BrainSetupRow.journal_trade_id.in_(ids),
                )
            ):
                links[trade_id].append((setup_id, strategy_id, version_id))
            stages = self._stages(links, organization_id=organization_id, trade_ids=ids)
            for row in chunk:
                strategy_id, version_id = row.user_strategy_id, row.strategy_version_id
                stage, issue = None, None
                linked = links.get(row.id, [])
                if len(linked) > 1:
                    issue = "ambiguous_brain_link"
                elif linked:
                    setup_id, brain_strategy, brain_version = linked[0]
                    if (strategy_id is not None and strategy_id != brain_strategy) or (
                        version_id is not None and version_id != brain_version
                    ):
                        issue = "conflicting_brain_link"
                    else:
                        strategy_id = strategy_id or brain_strategy
                        version_id = version_id or brain_version
                        stage = stages.get((setup_id, str(row.id)))
                entry, exit_ = times.get(row.id, (None, None))
                enriched.append(_Trade(row, entry, exit_, strategy_id, version_id, stage, issue))
        return enriched

    def _stages(
        self,
        links: dict[UUID, list[tuple[UUID, UUID, UUID]]],
        *,
        organization_id: UUID,
        trade_ids: list[UUID],
    ) -> dict[tuple[UUID, str], str | None]:
        setup_ids = [link[0] for linked in links.values() if len(linked) == 1 for link in linked]
        if not setup_ids:
            return {}
        # First trade-open snapshot per exact setup/journal link, not mutable setup payload.
        journal_ref = BrainSetupEventRow.payload["journal_trade_id"].as_string()
        ranked = (
            select(
                BrainSetupEventRow.setup_id,
                journal_ref.label("journal_ref"),
                BrainSetupEventRow.payload["stage"].as_string().label("stage"),
                func.row_number()
                .over(
                    partition_by=(BrainSetupEventRow.setup_id, journal_ref),
                    order_by=(BrainSetupEventRow.occurred_at, BrainSetupEventRow.id),
                )
                .label("rank"),
            )
            .where(
                BrainSetupEventRow.organization_id == organization_id,
                BrainSetupEventRow.setup_id.in_(setup_ids),
                BrainSetupEventRow.kind == "paper_trade_opened",
                journal_ref.in_([str(trade_id) for trade_id in trade_ids]),
            )
            .subquery()
        )
        return {
            (setup_id, reference): stage if stage in NestedMaturityStage else None
            for setup_id, reference, stage in self._session.execute(
                select(ranked.c.setup_id, ranked.c.journal_ref, ranked.c.stage).where(
                    ranked.c.rank == 1
                )
            )
        }

    def _metrics(
        self, trades: list[_Trade], *, min_sample_size: int, truncated: bool
    ) -> StrategyAnalyticsMetrics:
        rows = [trade.row for trade in trades]
        journal = _compute_metrics(rows)
        missing = {
            field: sum(getattr(row, field) is None for row in rows) for field in _MONEY_FIELDS
        }
        missing.update(
            {
                "entry_time": sum(trade.entry_time is None for trade in trades),
                "exit_time": sum(trade.exit_time is None for trade in trades),
                "result": journal.trade_count - journal.wins - journal.losses - journal.breakeven,
                **{
                    dimension.value: sum(trade.dimension(dimension) is None for trade in trades)
                    for dimension in StrategyAnalyticsDimension
                },
            }
        )
        invalid = {
            "planned_risk_amount": sum(
                row.planned_risk_amount is not None and row.planned_risk_amount <= 0 for row in rows
            ),
            "holding_period": sum(
                trade.entry_time is not None
                and trade.exit_time is not None
                and trade.exit_time < trade.entry_time
                for trade in trades
            ),
        }
        r_values = [
            row.net_pnl / row.planned_risk_amount
            for row in rows
            if row.net_pnl is not None
            and row.planned_risk_amount is not None
            and row.planned_risk_amount > 0
        ]
        timed = [
            trade
            for trade in trades
            if trade.entry_time is not None
            and trade.exit_time is not None
            and trade.exit_time >= trade.entry_time
        ]
        # Holding period does not depend on PnL; this placeholder is never used for PnL metrics.
        duration = self._calculator.calculate(
            [
                TradeRecord(
                    realized_pnl=_ZERO, opened_at=trade.entry_time, closed_at=trade.exit_time
                )
                for trade in timed
            ]
        )
        dated = [
            TradeRecord(realized_pnl=trade.row.net_pnl, closed_at=trade.exit_time)
            for trade in trades
            if trade.row.net_pnl is not None and trade.exit_time is not None
        ]
        drawdown = self._calculator.calculate(dated)
        complete_costs = [
            row for row in rows if all(getattr(row, field) is not None for field in _COST_FIELDS)
        ]
        reconciled = [row for row in complete_costs if _costs_reconcile(row)]
        cost_stats = _compute_metrics(reconciled)
        counts = {
            "win_rate": journal.wins + journal.losses,
            "expectancy": journal.pnl_sample_count,
            "net_pnl_total": journal.pnl_sample_count,
            "average_r": journal.r_sample_count,
            "median_r": len(r_values),
            "profit_factor": journal.pnl_sample_count,
            "maximum_drawdown": len(dated),
            "average_mae_amount": journal.mae_sample_count,
            "average_mfe_amount": journal.mfe_sample_count,
            "average_holding_period_seconds": len(timed),
            "cost_reconciled_net_pnl_total": len(reconciled),
            "cost_reconciled_expectancy": len(reconciled),
        }
        warnings: list[StrategyAnalyticsWarning] = []
        missing = {key: count for key, count in missing.items() if count}
        invalid = {key: count for key, count in invalid.items() if count}
        if missing:
            warnings.append(
                StrategyAnalyticsWarning(
                    code="missing_fields",
                    message="Missing recorded fields are counted in missing_fields.",
                )
            )
        if invalid:
            warnings.append(
                StrategyAnalyticsWarning(
                    code="invalid_fields",
                    message="Invalid risk/timing values are excluded and counted.",
                )
            )
        if len(trades) < min_sample_size:
            warnings.append(
                StrategyAnalyticsWarning(
                    code="insufficient_history",
                    message=f"{len(trades)} closed trades; descriptive minimum: {min_sample_size}.",
                )
            )
        if len(complete_costs) < len(trades):
            warnings.append(
                StrategyAnalyticsWarning(
                    code="costs_unverified",
                    message="Incomplete costs: recorded net is used without assuming zero costs.",
                )
            )
        if len(reconciled) < len(complete_costs):
            warnings.append(
                StrategyAnalyticsWarning(
                    code="costs_inconsistent",
                    message=(
                        f"{len(complete_costs) - len(reconciled)} cost records disagree with net."
                    ),
                )
            )
        for code in ("ambiguous_brain_link", "conflicting_brain_link"):
            count = sum(trade.link_issue == code for trade in trades)
            if count:
                warnings.append(
                    StrategyAnalyticsWarning(
                        code=code,
                        message=f"{count} trades excluded from Brain attribution: {code}.",
                    )
                )
        if truncated:
            journal.warnings.append(
                JournalStatsWarning(
                    code=JournalStatsWarningCode.RESULT_TRUNCATED,
                    message=f"Scan capped at {self._max_rows} oldest trades; later matches absent.",
                )
            )
        return StrategyAnalyticsMetrics(
            **journal.model_dump(),
            median_r=float(median(r_values)) if r_values else None,
            maximum_drawdown=drawdown.max_drawdown if dated else None,
            average_holding_period_seconds=duration.avg_duration_seconds,
            cost_reconciled_net_pnl_total=cost_stats.net_pnl_total,
            cost_reconciled_expectancy=cost_stats.expectancy,
            cost_complete_sample_count=len(complete_costs),
            cost_inconsistent_sample_count=len(complete_costs) - len(reconciled),
            metric_samples={
                metric: StrategyAnalyticsSample(
                    sample_count=count,
                    available=(
                        count > 0
                        and (metric != "profit_factor" or journal.profit_factor is not None)
                    ),
                    insufficient_history=count < min_sample_size,
                )
                for metric, count in counts.items()
            },
            missing_fields=missing,
            invalid_fields=invalid,
            insufficient_history=len(trades) < min_sample_size,
            analytics_warnings=warnings,
        )
