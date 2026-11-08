"""Performance analytics service (Slice 62).

Loads closed trades from the persistence layer, normalizes them into
:class:`TradeRecord` values, and delegates all math to the pure
:class:`PerformanceCalculator`. Also persists account snapshots and per-strategy
daily rollups for multi-week tracking.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models import PerformanceSnapshot, Position, StrategyPerformanceDaily
from app.repositories.performance import (
    PerformanceSnapshotRepository,
    StrategyPerformanceDailyRepository,
)
from app.repositories.positions import PositionRepository
from app.schemas.performance import (
    GroupBreakdownSchema,
    PerformanceMetricsSchema,
    PerformanceReport,
)
from app.services.performance.calculator import (
    PerformanceCalculator,
    trade_record_from_human_flag,
)
from app.services.performance.types import (
    GroupBreakdown,
    PerformanceMetrics,
    TradeRecord,
)

_ZERO = Decimal("0")


class PerformanceService:
    """Builds performance reports and persists snapshots from closed positions."""

    def __init__(
        self,
        session: Session,
        *,
        calculator: PerformanceCalculator | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._positions = PositionRepository(session)
        self._snapshots = PerformanceSnapshotRepository(session)
        self._daily = StrategyPerformanceDailyRepository(session)
        self._calc = calculator or PerformanceCalculator()
        self._clock = clock

    def build_report(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> PerformanceReport:
        """Compute the full account report from all closed positions in scope."""
        trades = self._load_trades(organization_id=organization_id, user_id=user_id)
        return PerformanceReport(
            account=_to_metrics_schema(self._calc.calculate(trades)),
            by_strategy=_to_group_schemas(self._calc.breakdown_by_strategy(trades)),
            by_symbol=_to_group_schemas(self._calc.breakdown_by_symbol(trades)),
            by_timeframe=_to_group_schemas(self._calc.breakdown_by_timeframe(trades)),
            by_source=_to_group_schemas(self._calc.breakdown_by_source(trades)),
        )

    def snapshot_account(
        self, *, organization_id: uuid.UUID, user_id: uuid.UUID | None = None
    ) -> PerformanceSnapshot:
        """Persist an account-level performance snapshot and return the row."""
        trades = self._load_trades(organization_id=organization_id, user_id=user_id)
        metrics = self._calc.calculate(trades)
        report = PerformanceReport(
            account=_to_metrics_schema(metrics),
            by_strategy=_to_group_schemas(self._calc.breakdown_by_strategy(trades)),
            by_symbol=_to_group_schemas(self._calc.breakdown_by_symbol(trades)),
            by_timeframe=_to_group_schemas(self._calc.breakdown_by_timeframe(trades)),
            by_source=_to_group_schemas(self._calc.breakdown_by_source(trades)),
        )
        row = PerformanceSnapshot(
            organization_id=organization_id,
            user_id=user_id,
            scope="account",
            as_of=self._clock(),
            trade_count=metrics.trade_count,
            net_pnl=metrics.net_pnl,
            gross_profit=metrics.gross_profit,
            gross_loss=metrics.gross_loss,
            total_fees=metrics.total_fees,
            total_funding=metrics.total_funding,
            win_rate=metrics.win_rate,
            profit_factor=metrics.profit_factor,
            expectancy=metrics.expectancy,
            avg_r_multiple=metrics.avg_r_multiple,
            max_drawdown=metrics.max_drawdown,
            max_drawdown_pct=metrics.max_drawdown_pct,
            metrics=report.model_dump(mode="json"),
        )
        return self._snapshots.add(row)

    def rollup_strategy_daily(
        self, *, day: date_type, organization_id: uuid.UUID
    ) -> list[StrategyPerformanceDaily]:
        """Upsert per-strategy rollups for trades closed on ``day``."""
        trades = [
            t
            for t in self._load_trades(organization_id=organization_id)
            if t.closed_at is not None and t.closed_at.date() == day
        ]
        rows: list[StrategyPerformanceDaily] = []
        for group in self._calc.breakdown_by_strategy(trades):
            metrics = group.metrics
            existing = self._daily.get_for_day(
                organization_id=organization_id, strategy_id=group.key, day=day
            )
            row = existing or StrategyPerformanceDaily(
                organization_id=organization_id, strategy_id=group.key, day=day
            )
            row.trade_count = metrics.trade_count
            row.net_pnl = metrics.net_pnl
            row.win_rate = metrics.win_rate
            row.profit_factor = metrics.profit_factor
            row.expectancy = metrics.expectancy
            row.max_drawdown = metrics.max_drawdown
            row.metrics = _to_metrics_schema(metrics).model_dump(mode="json")
            if existing is None:
                self._daily.add(row)
            else:
                self._session.flush()
            rows.append(row)
        return rows

    # --- internals --------------------------------------------------------- #

    def _load_trades(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> list[TradeRecord]:
        positions = self._positions.list_closed_for_analytics(
            organization_id=organization_id, user_id=user_id
        )
        return [_position_to_trade(p) for p in positions]


def _position_to_trade(position: Position) -> TradeRecord:
    """Normalize a closed :class:`Position` into a :class:`TradeRecord`.

    ``Position`` carries no fees/funding or timeframe; those are reported as zero
    / ``None``. Risk amount is derived from the entry/stop distance for an
    R-multiple where a stop is known.
    """
    strategy_id = position.strategy_id.value if position.strategy_id is not None else None
    risk_amount: Decimal | None = None
    if position.stop_loss is not None and position.entry_price is not None:
        distance = abs(position.entry_price - position.stop_loss)
        size = position.size or _ZERO
        risk = distance * size
        risk_amount = risk if risk > _ZERO else None

    risk_state = position.risk_state or {}
    had_violation = bool(risk_state.get("violation") or risk_state.get("violations"))

    return TradeRecord(
        realized_pnl=position.realized_pnl or _ZERO,
        symbol=position.symbol,
        strategy_id=strategy_id,
        timeframe=None,
        direction=position.direction.value if position.direction is not None else None,
        size=position.size,
        risk_amount=risk_amount,
        opened_at=position.opened_at,
        closed_at=position.closed_at,
        source=trade_record_from_human_flag(strategy_id=strategy_id),
        had_violation=had_violation,
    )


def _to_metrics_schema(metrics: PerformanceMetrics) -> PerformanceMetricsSchema:
    return PerformanceMetricsSchema.model_validate(metrics)


def _to_group_schemas(groups: list[GroupBreakdown]) -> list[GroupBreakdownSchema]:
    return [GroupBreakdownSchema(key=g.key, metrics=_to_metrics_schema(g.metrics)) for g in groups]
