"""Journal statistics service (AT-031 — Journal Statistics & Setup Analytics v1).

Deterministic, record-only aggregates over canonical ``journal_trades``
(AT-030). Reads recorded values only — no live market I/O, no execution
authority, no mutation. Every metric family carries its own sample count and
results carry explicit confidence labels and warnings, so partial data and
small samples are always visible (docs truthfulness / conservative behavior).

Classification rules (deterministic, documented):

- Only CLOSED trades enter statistics; outcome metrics are undefined for
  planned/open/cancelled rows.
- Win/loss/breakeven uses the recorded ``result``; a closed trade left at
  ``result=open`` falls back to the sign of its recorded ``net_pnl`` (the same
  arithmetic AT-030 uses when closing), otherwise it stays undecided.
- Rule compliance per trade is the worst recorded assessment:
  ``violated`` > ``partial`` > ``compliant`` (any ``followed``) > ``unassessed``.
- Human-vs-system execution is derived from the trade source by decision
  authority: manual, imported, human-approved proposal-flow paper
  executions, manual demo tests, and BloFin demo hand trades are ``human``;
  paper-validation, backtest, and system-generated trades are ``system``.
  A source with no mapping is left out of the aggregate and a warning is logged.
- AT-036 extends cohort comparison with scorecards, decision-quality metrics,
  dimension buckets, setup/regime breakdowns, and frontend navigation links.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from urllib.parse import urlencode

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationAppError
from app.db.models import SetupDefinition, UserStrategy, UserStrategyVersion
from app.repositories.journal_trades import JournalTradeRepository, JournalTradeStatsRow
from app.schemas.backtest import (
    ComparisonBreakdown,
    ComparisonBreakdownDimension,
    ComparisonDimensionBucket,
    ComparisonLinks,
    ComparisonScorecard,
    DecisionQualityMetrics,
    JournalComparisonCohort,
    JournalComparisonCohortResult,
    JournalComparisonFilters,
    JournalComparisonResponse,
)
from app.schemas.common import JournalTradeSource, RuleComplianceStatus, TradeDirection, TradeResult
from app.schemas.journal_statistics import (
    ExecutionActor,
    JournalStatsBucket,
    JournalStatsFilters,
    JournalStatsGroupBy,
    JournalStatsResponse,
    JournalStatsWarning,
    JournalStatsWarningCode,
    JournalTradeStatsMetrics,
    SampleConfidence,
    TradeRuleCompliance,
)

logger = structlog.get_logger("services.journal_statistics")

_UNASSIGNED_KEY: Final = "unassigned"
_UNASSIGNED_LABEL: Final = "Unassigned"

# Sample-size thresholds for the coarse confidence label.
_CONFIDENCE_LOW: Final = 5
_CONFIDENCE_MODERATE: Final = 20
_CONFIDENCE_HIGH: Final = 50

# Early-exit threshold: capture below 50% of available profit (AT-036).
_EARLY_EXIT_CAPTURE_PCT: Final = 50.0

# Human-vs-system execution classification by decision authority (see module docstring).
_EXECUTION_ACTOR_BY_SOURCE: Final[dict[JournalTradeSource, ExecutionActor]] = {
    JournalTradeSource.MANUAL: ExecutionActor.HUMAN,
    JournalTradeSource.IMPORTED: ExecutionActor.HUMAN,
    JournalTradeSource.PAPER_EXECUTION: ExecutionActor.HUMAN,
    JournalTradeSource.MANUAL_DEMO_TEST: ExecutionActor.HUMAN,
    JournalTradeSource.BLOFIN_DEMO_MANUAL: ExecutionActor.HUMAN,
    JournalTradeSource.PAPER_VALIDATION: ExecutionActor.SYSTEM,
    JournalTradeSource.BACKTEST: ExecutionActor.SYSTEM,
    JournalTradeSource.SYSTEM: ExecutionActor.SYSTEM,
}

_COMPARISON_COHORTS: Final[
    tuple[tuple[JournalComparisonCohort, frozenset[JournalTradeSource]], ...]
] = (
    (
        JournalComparisonCohort.HUMAN,
        frozenset(
            {
                JournalTradeSource.MANUAL,
                JournalTradeSource.IMPORTED,
                JournalTradeSource.PAPER_EXECUTION,
                JournalTradeSource.MANUAL_DEMO_TEST,
                JournalTradeSource.BLOFIN_DEMO_MANUAL,
            }
        ),
    ),
    (
        JournalComparisonCohort.PAPER_SYSTEM,
        frozenset({JournalTradeSource.PAPER_VALIDATION}),
    ),
    (
        JournalComparisonCohort.BACKTEST,
        frozenset({JournalTradeSource.BACKTEST}),
    ),
)

_SCORECARD_ACTORS: Final[tuple[tuple[ExecutionActor, frozenset[JournalTradeSource]], ...]] = (
    (
        ExecutionActor.HUMAN,
        frozenset(
            {
                JournalTradeSource.MANUAL,
                JournalTradeSource.IMPORTED,
                JournalTradeSource.PAPER_EXECUTION,
                JournalTradeSource.MANUAL_DEMO_TEST,
                JournalTradeSource.BLOFIN_DEMO_MANUAL,
            }
        ),
    ),
    (
        ExecutionActor.SYSTEM,
        frozenset(
            {
                JournalTradeSource.PAPER_VALIDATION,
                JournalTradeSource.BACKTEST,
                JournalTradeSource.SYSTEM,
            }
        ),
    ),
)


class JournalStatisticsService:
    """Grouped, filterable statistics over canonical journal trades."""

    def __init__(self, session: Session, *, max_rows: int) -> None:
        self._session = session
        self._trades = JournalTradeRepository(session)
        self._max_rows = max_rows

    def compute(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        group_by: JournalStatsGroupBy,
        filters: JournalStatsFilters,
        limit: int = 50,
        offset: int = 0,
    ) -> JournalStatsResponse:
        if (
            filters.date_from is not None
            and filters.date_to is not None
            and filters.date_from > filters.date_to
        ):
            raise ValidationAppError("date_from must not be after date_to.")

        rows, truncated = self._trades.fetch_stats_rows(
            organization_id=organization_id,
            user_id=user_id,
            source=filters.source,
            entry_method=filters.entry_method,
            symbol=filters.symbol,
            timeframe=filters.timeframe,
            market_regime=filters.market_regime,
            setup_id=filters.setup_id,
            user_strategy_id=filters.user_strategy_id,
            strategy_version_id=filters.strategy_version_id,
            date_from=filters.date_from,
            date_to=filters.date_to,
            max_rows=self._max_rows,
        )
        compliance_by_trade = self._load_compliance(
            organization_id=organization_id, user_id=user_id, filters=filters, rows=rows
        )

        annotated: list[tuple[JournalTradeStatsRow, TradeRuleCompliance, ExecutionActor]] = []
        for row in rows:
            actor = _EXECUTION_ACTOR_BY_SOURCE.get(row.source)
            if actor is None:
                logger.warning(
                    "journal_statistics_unmapped_source",
                    source=row.source.value,
                )
                continue
            annotated.append(
                (
                    row,
                    compliance_by_trade.get(row.id, TradeRuleCompliance.UNASSESSED),
                    actor,
                )
            )
        if filters.rule_compliance is not None:
            annotated = [a for a in annotated if a[1] == filters.rule_compliance]
        if filters.execution_actor is not None:
            annotated = [a for a in annotated if a[2] == filters.execution_actor]

        overall = _compute_metrics([a[0] for a in annotated])
        if truncated:
            overall.warnings.append(
                JournalStatsWarning(
                    code=JournalStatsWarningCode.RESULT_TRUNCATED,
                    message=(
                        f"Result capped at {self._max_rows} closed trades; aggregates cover "
                        "only the oldest trades in range. Narrow the date range or filters."
                    ),
                )
            )

        buckets = self._build_buckets(annotated, group_by)
        total_buckets = len(buckets)
        page = buckets[offset : offset + limit]

        return JournalStatsResponse(
            group_by=group_by,
            filters=filters,
            overall=overall,
            buckets=page,
            total_buckets=total_buckets,
            limit=limit,
            offset=offset,
            truncated=truncated,
            max_rows=self._max_rows,
            generated_at=datetime.now(UTC),
        )

    def compare_cohorts(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        filters: JournalComparisonFilters,
        breakdown_limit: int = 10,
    ) -> JournalComparisonResponse:
        """Human-vs-system performance + decision-quality comparison (AT-034/AT-036)."""
        if (
            filters.date_from is not None
            and filters.date_to is not None
            and filters.date_from > filters.date_to
        ):
            raise ValidationAppError("date_from must not be after date_to.")

        sources: frozenset[JournalTradeSource] | None = (
            frozenset({filters.source}) if filters.source is not None else None
        )
        rows, truncated = self._trades.fetch_stats_rows(
            organization_id=organization_id,
            user_id=user_id,
            sources=sources,
            entry_method=filters.entry_method,
            symbol=filters.symbol,
            timeframe=filters.timeframe,
            market_regime=filters.market_regime,
            setup_id=filters.setup_id,
            user_strategy_id=filters.strategy_id,
            strategy_version_id=filters.strategy_version_id,
            date_from=filters.date_from,
            date_to=filters.date_to,
            max_rows=self._max_rows,
        )
        compliance_by_trade = self._load_comparison_compliance(
            organization_id=organization_id,
            user_id=user_id,
            filters=filters,
            sources=sources,
            rows=rows,
        )

        cohorts = [
            _cohort_result(
                cohort,
                [row for row in rows if row.source in cohort_sources],
                truncated=truncated,
            )
            for cohort, cohort_sources in _COMPARISON_COHORTS
        ]
        scorecards = [
            _scorecard_result(
                actor,
                [row for row in rows if row.source in actor_sources],
                truncated=truncated,
            )
            for actor, actor_sources in _SCORECARD_ACTORS
        ]

        by_entry_method = _dimension_buckets_by_key(
            rows,
            key_fn=lambda r: (r.entry_method.value, None, r.entry_method.value),
        )
        by_source = _dimension_buckets_by_key(
            rows,
            key_fn=lambda r: (r.source.value, None, r.source.value),
        )
        rule_compliance = _dimension_buckets_by_key(
            rows,
            key_fn=lambda r: (
                compliance_by_trade.get(r.id, TradeRuleCompliance.UNASSESSED).value,
                None,
                compliance_by_trade.get(r.id, TradeRuleCompliance.UNASSESSED).value,
            ),
        )

        decision_quality = _compute_decision_quality(rows)
        breakdowns = self._comparison_breakdowns(rows, limit=breakdown_limit)
        links = _comparison_links(filters)
        confidence = _confidence(len(rows))
        warnings = _rollup_comparison_warnings(
            truncated=truncated,
            max_rows=self._max_rows,
            overall_count=len(rows),
            scorecards=scorecards,
            decision_quality=decision_quality,
        )

        return JournalComparisonResponse(
            filters=filters,
            cohorts=cohorts,
            scorecards=scorecards,
            by_entry_method=by_entry_method,
            by_source=by_source,
            rule_compliance=rule_compliance,
            decision_quality=decision_quality,
            breakdowns=breakdowns,
            links=links,
            confidence=confidence,
            warnings=warnings,
            max_rows=self._max_rows,
            generated_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _load_compliance(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        filters: JournalStatsFilters,
        rows: list[JournalTradeStatsRow],
    ) -> dict[uuid.UUID, TradeRuleCompliance]:
        if not rows:
            return {}
        pairs = self._trades.fetch_rule_check_status_pairs(
            organization_id=organization_id,
            user_id=user_id,
            source=filters.source,
            entry_method=filters.entry_method,
            symbol=filters.symbol,
            timeframe=filters.timeframe,
            market_regime=filters.market_regime,
            setup_id=filters.setup_id,
            user_strategy_id=filters.user_strategy_id,
            strategy_version_id=filters.strategy_version_id,
            date_from=filters.date_from,
            date_to=filters.date_to,
        )
        return _compliance_map_from_pairs(pairs)

    def _load_comparison_compliance(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        filters: JournalComparisonFilters,
        sources: Collection[JournalTradeSource] | None,
        rows: list[JournalTradeStatsRow],
    ) -> dict[uuid.UUID, TradeRuleCompliance]:
        if not rows:
            return {}
        pairs = self._trades.fetch_rule_check_status_pairs(
            organization_id=organization_id,
            user_id=user_id,
            sources=sources,
            entry_method=filters.entry_method,
            symbol=filters.symbol,
            timeframe=filters.timeframe,
            market_regime=filters.market_regime,
            setup_id=filters.setup_id,
            user_strategy_id=filters.strategy_id,
            strategy_version_id=filters.strategy_version_id,
            date_from=filters.date_from,
            date_to=filters.date_to,
        )
        return _compliance_map_from_pairs(pairs)

    def _comparison_breakdowns(
        self,
        rows: list[JournalTradeStatsRow],
        *,
        limit: int,
    ) -> list[ComparisonBreakdown]:
        setup_labels = self._resolve_labels(rows, JournalStatsGroupBy.SETUP_VERSION)
        setup_buckets = _dimension_buckets_by_key(
            rows,
            key_fn=lambda r: (
                (str(r.setup_id), r.setup_id, setup_labels.get(r.setup_id, str(r.setup_id)))
                if r.setup_id is not None
                else (_UNASSIGNED_KEY, None, _UNASSIGNED_LABEL)
            ),
            limit=limit,
        )
        regime_buckets = _dimension_buckets_by_key(
            rows,
            key_fn=lambda r: (r.market_regime.value, None, r.market_regime.value),
            limit=limit,
        )
        return [
            ComparisonBreakdown(
                dimension=ComparisonBreakdownDimension.SETUP,
                buckets=setup_buckets,
            ),
            ComparisonBreakdown(
                dimension=ComparisonBreakdownDimension.MARKET_REGIME,
                buckets=regime_buckets,
            ),
        ]

    def _build_buckets(
        self,
        annotated: list[tuple[JournalTradeStatsRow, TradeRuleCompliance, ExecutionActor]],
        group_by: JournalStatsGroupBy,
    ) -> list[JournalStatsBucket]:
        labels = self._resolve_labels([a[0] for a in annotated], group_by)
        grouped: dict[tuple[str, uuid.UUID | None, str], list[JournalTradeStatsRow]] = defaultdict(
            list
        )
        for row, compliance, actor in annotated:
            grouped[_group_key(row, group_by, compliance, actor, labels)].append(row)

        buckets = [
            JournalStatsBucket(
                key=key,
                group_id=group_id,
                label=label,
                metrics=_compute_metrics(bucket_rows),
            )
            for (key, group_id, label), bucket_rows in grouped.items()
        ]
        # Deterministic order: largest sample first, then label, then key.
        buckets.sort(key=lambda b: (-b.metrics.trade_count, b.label.lower(), b.key))
        return buckets

    def _resolve_labels(
        self,
        rows: list[JournalTradeStatsRow],
        group_by: JournalStatsGroupBy,
    ) -> dict[uuid.UUID, str]:
        """Batch-resolve display labels for the id-based grouping dimensions."""
        if group_by in (JournalStatsGroupBy.SETUP, JournalStatsGroupBy.SETUP_VERSION):
            setup_ids = {row.setup_id for row in rows if row.setup_id is not None}
            if not setup_ids:
                return {}
            setup_stmt = select(
                SetupDefinition.id, SetupDefinition.name, SetupDefinition.version
            ).where(SetupDefinition.id.in_(setup_ids))
            setup_rows = self._session.execute(setup_stmt).all()
            if group_by is JournalStatsGroupBy.SETUP:
                return {sid: name for sid, name, _version in setup_rows}
            return {sid: f"{name} v{version}" for sid, name, version in setup_rows}
        if group_by is JournalStatsGroupBy.STRATEGY:
            strategy_ids = {row.user_strategy_id for row in rows if row.user_strategy_id}
            if not strategy_ids:
                return {}
            strategy_stmt = select(UserStrategy.id, UserStrategy.name).where(
                UserStrategy.id.in_(strategy_ids)
            )
            return {sid: name for sid, name in self._session.execute(strategy_stmt).all()}  # noqa: C416
        if group_by is JournalStatsGroupBy.STRATEGY_VERSION:
            version_ids = {row.strategy_version_id for row in rows if row.strategy_version_id}
            if not version_ids:
                return {}
            version_stmt = (
                select(UserStrategyVersion.id, UserStrategy.name, UserStrategyVersion.version)
                .join(UserStrategy, UserStrategyVersion.strategy_id == UserStrategy.id)
                .where(UserStrategyVersion.id.in_(version_ids))
            )
            return {
                vid: f"{name} v{version}"
                for vid, name, version in self._session.execute(version_stmt).all()
            }
        return {}


# --------------------------------------------------------------------------- #
# Pure, deterministic computation helpers (unit-testable without a session)
# --------------------------------------------------------------------------- #


def _compliance_map_from_pairs(
    pairs: list[tuple[uuid.UUID, RuleComplianceStatus]],
) -> dict[uuid.UUID, TradeRuleCompliance]:
    statuses: dict[uuid.UUID, set[RuleComplianceStatus]] = defaultdict(set)
    for trade_id, status in pairs:
        statuses[trade_id].add(status)
    return {trade_id: _classify_compliance(recorded) for trade_id, recorded in statuses.items()}


def _classify_compliance(recorded: set[RuleComplianceStatus]) -> TradeRuleCompliance:
    """Worst recorded assessment wins; never infer compliance from silence."""
    if RuleComplianceStatus.VIOLATED in recorded:
        return TradeRuleCompliance.VIOLATED
    if RuleComplianceStatus.PARTIAL in recorded:
        return TradeRuleCompliance.PARTIAL
    if RuleComplianceStatus.FOLLOWED in recorded:
        return TradeRuleCompliance.COMPLIANT
    return TradeRuleCompliance.UNASSESSED


def _group_key(
    row: JournalTradeStatsRow,
    group_by: JournalStatsGroupBy,
    compliance: TradeRuleCompliance,
    actor: ExecutionActor,
    labels: dict[uuid.UUID, str],
) -> tuple[str, uuid.UUID | None, str]:
    """(key, group_id, label) for one trade under the requested dimension."""
    match group_by:
        case JournalStatsGroupBy.OVERALL:
            return ("overall", None, "All trades")
        case JournalStatsGroupBy.SETUP:
            if row.setup_id is None or row.setup_id not in labels:
                return (_UNASSIGNED_KEY, None, _UNASSIGNED_LABEL)
            name = labels[row.setup_id]
            return (name, None, name)  # groups all versions sharing the setup name
        case JournalStatsGroupBy.SETUP_VERSION:
            if row.setup_id is None:
                return (_UNASSIGNED_KEY, None, _UNASSIGNED_LABEL)
            return (str(row.setup_id), row.setup_id, labels.get(row.setup_id, str(row.setup_id)))
        case JournalStatsGroupBy.STRATEGY:
            if row.user_strategy_id is None:
                return (_UNASSIGNED_KEY, None, _UNASSIGNED_LABEL)
            return (
                str(row.user_strategy_id),
                row.user_strategy_id,
                labels.get(row.user_strategy_id, str(row.user_strategy_id)),
            )
        case JournalStatsGroupBy.STRATEGY_VERSION:
            if row.strategy_version_id is None:
                return (_UNASSIGNED_KEY, None, _UNASSIGNED_LABEL)
            return (
                str(row.strategy_version_id),
                row.strategy_version_id,
                labels.get(row.strategy_version_id, str(row.strategy_version_id)),
            )
        case JournalStatsGroupBy.SYMBOL:
            return (row.symbol, None, row.symbol)
        case JournalStatsGroupBy.TIMEFRAME:
            return (row.timeframe, None, row.timeframe)
        case JournalStatsGroupBy.MARKET_REGIME:
            return (row.market_regime.value, None, row.market_regime.value)
        case JournalStatsGroupBy.SOURCE:
            return (row.source.value, None, row.source.value)
        case JournalStatsGroupBy.ENTRY_METHOD:
            return (row.entry_method.value, None, row.entry_method.value)
        case JournalStatsGroupBy.RULE_COMPLIANCE:
            return (compliance.value, None, compliance.value)
        case JournalStatsGroupBy.EXECUTION_ACTOR:
            return (actor.value, None, actor.value)


def _decide_result(row: JournalTradeStatsRow) -> TradeResult | None:
    """Recorded result, else deterministic net-PnL sign fallback, else undecided."""
    if row.result != TradeResult.OPEN:
        return row.result
    if row.net_pnl is None:
        return None
    if row.net_pnl > 0:
        return TradeResult.WIN
    if row.net_pnl < 0:
        return TradeResult.LOSS
    return TradeResult.BREAKEVEN


def _confidence(trade_count: int) -> SampleConfidence:
    if trade_count < _CONFIDENCE_LOW:
        return SampleConfidence.INSUFFICIENT
    if trade_count < _CONFIDENCE_MODERATE:
        return SampleConfidence.LOW
    if trade_count < _CONFIDENCE_HIGH:
        return SampleConfidence.MODERATE
    return SampleConfidence.HIGH


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _mean_float(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _capture_pct(row: JournalTradeStatsRow) -> float | None:
    """Realized-vs-available % from recorded value or AT-031 derivation."""
    if row.net_pnl is None or row.available_profit is None:
        return None
    if row.realized_vs_available_pct is not None:
        return row.realized_vs_available_pct
    if row.available_profit != 0:
        return float(row.net_pnl / row.available_profit * Decimal("100"))
    return None


def _entry_timing_pct(row: JournalTradeStatsRow) -> float | None:
    """Signed % distance of actual entry vs planned; positive = worse fill."""
    if row.planned_entry_price is None or row.entry_price is None:
        return None
    planned = row.planned_entry_price
    if planned == 0:
        return None
    if row.direction is TradeDirection.LONG:
        return float((row.entry_price - planned) / planned * Decimal("100"))
    return float((planned - row.entry_price) / planned * Decimal("100"))


def _compute_decision_quality(rows: list[JournalTradeStatsRow]) -> DecisionQualityMetrics:
    """Decision-quality aggregates from recorded journal fields only (AT-036)."""
    trade_count = len(rows)
    warnings: list[JournalStatsWarning] = []
    if trade_count == 0:
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.NO_CLOSED_TRADES,
                message="No closed trades match the filters; no decision quality computable.",
            )
        )
        return DecisionQualityMetrics(warnings=warnings)

    timing_pcts = [pct for row in rows if (pct := _entry_timing_pct(row)) is not None]
    capture_pcts = [pct for row in rows if (pct := _capture_pct(row)) is not None]
    early_exit_count = sum(1 for pct in capture_pcts if pct < _EARLY_EXIT_CAPTURE_PCT)
    missed_profits = [
        row.available_profit - row.net_pnl
        for row in rows
        if row.available_profit is not None
        and row.net_pnl is not None
        and row.available_profit > row.net_pnl
    ]

    if 0 < len(timing_pcts) < trade_count:
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.PARTIAL_TIMING_DATA,
                message=(
                    f"Entry timing computable on {len(timing_pcts)} of {trade_count} trades; "
                    "averages cover only those with planned and actual entry prices."
                ),
            )
        )
    if 0 < len(capture_pcts) < trade_count:
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.PARTIAL_CAPTURE_DATA,
                message=(
                    f"Capture / early-exit data on {len(capture_pcts)} of {trade_count} trades; "
                    "early-exit rate covers only those with available and realized profit."
                ),
            )
        )
    if 0 < len(missed_profits) < trade_count:
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.PARTIAL_MISSED_PROFIT_DATA,
                message=(
                    f"Missed profit computable on {len(missed_profits)} of {trade_count} trades; "
                    "average covers only those with available profit exceeding net PnL."
                ),
            )
        )
    if trade_count < _CONFIDENCE_MODERATE:
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.LOW_SAMPLE,
                message=(
                    f"Only {trade_count} closed trade(s); treat decision-quality metrics as "
                    "anecdotal, not as evidence of an edge."
                ),
            )
        )

    return DecisionQualityMetrics(
        timing_sample_count=len(timing_pcts),
        average_entry_timing_pct=_mean_float(timing_pcts),
        early_exit_sample_count=len(capture_pcts),
        early_exit_count=early_exit_count if capture_pcts else None,
        early_exit_rate=(early_exit_count / len(capture_pcts) if capture_pcts else None),
        missed_profit_sample_count=len(missed_profits),
        average_missed_profit=_mean(missed_profits),
        average_capture_pct=_mean_float(capture_pcts),
        warnings=warnings,
    )


def _cohort_result(
    cohort: JournalComparisonCohort,
    rows: list[JournalTradeStatsRow],
    *,
    truncated: bool,
) -> JournalComparisonCohortResult:
    metrics = _compute_metrics(rows)
    if truncated:
        metrics.warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.RESULT_TRUNCATED,
                message=(
                    "Underlying comparison sample was capped; this cohort may omit newer "
                    "closed trades. Narrow the date range or filters."
                ),
            )
        )
    return JournalComparisonCohortResult(
        cohort=cohort,
        metrics=metrics,
        sample_count=metrics.trade_count,
        truncated=truncated,
    )


def _scorecard_result(
    actor: ExecutionActor,
    rows: list[JournalTradeStatsRow],
    *,
    truncated: bool,
) -> ComparisonScorecard:
    metrics = _compute_metrics(rows)
    decision_quality = _compute_decision_quality(rows)
    if truncated:
        metrics.warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.RESULT_TRUNCATED,
                message=(
                    "Underlying comparison sample was capped; this scorecard may omit newer "
                    "closed trades. Narrow the date range or filters."
                ),
            )
        )
    return ComparisonScorecard(
        actor=actor,
        metrics=metrics,
        decision_quality=decision_quality,
        sample_count=metrics.trade_count,
        truncated=truncated,
    )


def _dimension_buckets_by_key(
    rows: list[JournalTradeStatsRow],
    *,
    key_fn: Callable[[JournalTradeStatsRow], tuple[str, uuid.UUID | None, str]],
    limit: int | None = None,
) -> list[ComparisonDimensionBucket]:
    grouped: dict[tuple[str, uuid.UUID | None, str], list[JournalTradeStatsRow]] = defaultdict(list)
    for row in rows:
        key, group_id, label = key_fn(row)
        grouped[(key, group_id, label)].append(row)

    buckets = [
        ComparisonDimensionBucket(
            key=key,
            group_id=group_id,
            label=label,
            metrics=_compute_metrics(bucket_rows),
            sample_count=len(bucket_rows),
        )
        for (key, group_id, label), bucket_rows in grouped.items()
    ]
    # Contract: trade_count desc, then key.
    buckets.sort(key=lambda b: (-b.metrics.trade_count, b.key))
    if limit is not None:
        return buckets[:limit]
    return buckets


def _comparison_links(filters: JournalComparisonFilters) -> ComparisonLinks:
    params: list[tuple[str, str]] = []
    if filters.strategy_id is not None:
        params.append(("strategy_id", str(filters.strategy_id)))
    if filters.strategy_version_id is not None:
        params.append(("strategy_version_id", str(filters.strategy_version_id)))
    if filters.setup_id is not None:
        params.append(("setup_id", str(filters.setup_id)))
    if filters.symbol is not None:
        params.append(("symbol", filters.symbol))
    if filters.timeframe is not None:
        params.append(("timeframe", filters.timeframe))
    if filters.date_from is not None:
        params.append(("date_from", filters.date_from.isoformat()))
    if filters.date_to is not None:
        params.append(("date_to", filters.date_to.isoformat()))
    if filters.market_regime is not None:
        params.append(("market_regime", filters.market_regime.value))
    if filters.entry_method is not None:
        params.append(("entry_method", filters.entry_method.value))
    if filters.source is not None:
        params.append(("source", filters.source.value))

    query = urlencode(params)
    comparison_path = f"/journal/comparison?{query}" if query else "/journal/comparison"

    stats_params: list[tuple[str, str]] = []
    if filters.strategy_id is not None:
        stats_params.append(("user_strategy_id", str(filters.strategy_id)))
    if filters.strategy_version_id is not None:
        stats_params.append(("strategy_version_id", str(filters.strategy_version_id)))
    if filters.setup_id is not None:
        stats_params.append(("setup_id", str(filters.setup_id)))
    if filters.symbol is not None:
        stats_params.append(("symbol", filters.symbol))
    if filters.timeframe is not None:
        stats_params.append(("timeframe", filters.timeframe))
    if filters.market_regime is not None:
        stats_params.append(("market_regime", filters.market_regime.value))
    if filters.entry_method is not None:
        stats_params.append(("entry_method", filters.entry_method.value))
    if filters.source is not None:
        stats_params.append(("source", filters.source.value))
    if filters.date_from is not None:
        stats_params.append(("date_from", filters.date_from.isoformat()))
    if filters.date_to is not None:
        stats_params.append(("date_to", filters.date_to.isoformat()))
    stats_query = urlencode(stats_params)
    stats_path = f"/journal/statistics?{stats_query}" if stats_query else "/journal/statistics"

    if filters.strategy_id is not None:
        backtests_path = f"/backtests?strategy_id={filters.strategy_id}"
    else:
        backtests_path = "/backtests"

    return ComparisonLinks(
        journal_trades_path="/journal",
        journal_statistics_path=stats_path,
        journal_comparison_path=comparison_path,
        backtests_path=backtests_path,
        research_validation_path="/research-validation",
        paper_validation_candidates_path="/paper-validation/candidates",
    )


def _rollup_comparison_warnings(
    *,
    truncated: bool,
    max_rows: int,
    overall_count: int,
    scorecards: list[ComparisonScorecard],
    decision_quality: DecisionQualityMetrics,
) -> list[JournalStatsWarning]:
    """Top-level warning rollup with stable first-seen order and deduped codes."""
    ordered: list[JournalStatsWarning] = []
    seen: set[JournalStatsWarningCode] = set()

    def _add(warning: JournalStatsWarning) -> None:
        if warning.code in seen:
            return
        seen.add(warning.code)
        ordered.append(warning)

    if overall_count == 0:
        _add(
            JournalStatsWarning(
                code=JournalStatsWarningCode.NO_CLOSED_TRADES,
                message="No closed trades match the filters; no comparison computable.",
            )
        )
    elif overall_count < _CONFIDENCE_MODERATE:
        _add(
            JournalStatsWarning(
                code=JournalStatsWarningCode.LOW_SAMPLE,
                message=(
                    f"Only {overall_count} closed trade(s); treat comparison metrics as "
                    "anecdotal, not as evidence of an edge."
                ),
            )
        )
    if truncated:
        _add(
            JournalStatsWarning(
                code=JournalStatsWarningCode.RESULT_TRUNCATED,
                message=(
                    f"Result capped at {max_rows} closed trades; aggregates cover only the "
                    "oldest trades in range. Narrow the date range or filters."
                ),
            )
        )
    for warning in decision_quality.warnings:
        _add(warning)
    for scorecard in scorecards:
        for warning in scorecard.metrics.warnings:
            _add(warning)
        for warning in scorecard.decision_quality.warnings:
            _add(warning)
    return ordered


def _compute_metrics(rows: list[JournalTradeStatsRow]) -> JournalTradeStatsMetrics:
    """Deterministic aggregate metrics for one set of closed journal trades."""
    trade_count = len(rows)
    warnings: list[JournalStatsWarning] = []
    if trade_count == 0:
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.NO_CLOSED_TRADES,
                message="No closed trades match the filters; no statistics computable.",
            )
        )
        return JournalTradeStatsMetrics(confidence=SampleConfidence.INSUFFICIENT, warnings=warnings)

    results = [_decide_result(row) for row in rows]
    wins = sum(1 for r in results if r == TradeResult.WIN)
    losses = sum(1 for r in results if r == TradeResult.LOSS)
    breakeven = sum(1 for r in results if r == TradeResult.BREAKEVEN)
    decided = wins + losses
    win_rate = wins / decided if decided else None

    pnl_rows = [row for row in rows if row.net_pnl is not None]
    net_pnls = [row.net_pnl for row in pnl_rows if row.net_pnl is not None]
    winners = [pnl for pnl in net_pnls if pnl > 0]
    losers = [pnl for pnl in net_pnls if pnl < 0]
    gross_win = sum(winners, Decimal("0"))
    gross_loss = sum(losers, Decimal("0"))
    gross_pnls = [row.gross_pnl for row in rows if row.gross_pnl is not None]

    r_values = [
        row.net_pnl / row.planned_risk_amount
        for row in pnl_rows
        if row.net_pnl is not None
        and row.planned_risk_amount is not None
        and row.planned_risk_amount > 0
    ]

    fees = [row.fees for row in rows if row.fees is not None]
    fundings = [row.funding for row in rows if row.funding is not None]
    slippages = [row.slippage for row in rows if row.slippage is not None]
    cost_sample_count = sum(
        1
        for row in rows
        if row.fees is not None or row.funding is not None or row.slippage is not None
    )
    cost_parts = [
        total
        for total in (
            sum(fees, Decimal("0")) if fees else None,
            sum(fundings, Decimal("0")) if fundings else None,
            sum(slippages, Decimal("0")) if slippages else None,
        )
        if total is not None
    ]

    mfe_values = [row.mfe_amount for row in rows if row.mfe_amount is not None]
    mae_values = [row.mae_amount for row in rows if row.mae_amount is not None]

    capture_rows = [
        row for row in rows if row.net_pnl is not None and row.available_profit is not None
    ]
    capture_pcts: list[float] = []
    for row in capture_rows:
        if row.realized_vs_available_pct is not None:
            capture_pcts.append(row.realized_vs_available_pct)
        elif row.available_profit and row.net_pnl is not None:
            capture_pcts.append(float(row.net_pnl / row.available_profit * Decimal("100")))

    confidence = _confidence(trade_count)
    if confidence in (SampleConfidence.INSUFFICIENT, SampleConfidence.LOW):
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.LOW_SAMPLE,
                message=(
                    f"Only {trade_count} closed trade(s); treat these statistics as anecdotal, "
                    "not as evidence of an edge."
                ),
            )
        )
    if decided == 0:
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.NO_DECIDED_TRADES,
                message="No trades with a decided win/loss result; win rate is undefined.",
            )
        )
    if len(pnl_rows) < trade_count:
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.MISSING_PNL,
                message=(
                    f"{trade_count - len(pnl_rows)} of {trade_count} closed trades have no "
                    "recorded net PnL and are excluded from PnL aggregates."
                ),
            )
        )
    if pnl_rows and len(r_values) < len(pnl_rows):
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.MISSING_RISK,
                message=(
                    f"Average R uses {len(r_values)} of {len(pnl_rows)} trades with PnL; the "
                    "rest have no positive planned risk amount recorded."
                ),
            )
        )
    if winners and not losers:
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.NO_LOSING_TRADES,
                message="No losing trades in sample; profit factor is undefined.",
            )
        )
    if (0 < len(mfe_values) < trade_count) or (0 < len(mae_values) < trade_count):
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.PARTIAL_EXCURSION_DATA,
                message=(
                    f"MFE recorded on {len(mfe_values)} and MAE on {len(mae_values)} of "
                    f"{trade_count} trades; excursion aggregates cover only those trades."
                ),
            )
        )
    if 0 < len(capture_rows) < trade_count:
        warnings.append(
            JournalStatsWarning(
                code=JournalStatsWarningCode.PARTIAL_CAPTURE_DATA,
                message=(
                    f"Available-vs-realized profit recorded on {len(capture_rows)} of "
                    f"{trade_count} trades; capture aggregates cover only those trades."
                ),
            )
        )

    return JournalTradeStatsMetrics(
        trade_count=trade_count,
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        win_rate=win_rate,
        pnl_sample_count=len(pnl_rows),
        net_pnl_total=sum(net_pnls, Decimal("0")) if net_pnls else None,
        gross_pnl_total=sum(gross_pnls, Decimal("0")) if gross_pnls else None,
        expectancy=_mean(net_pnls),
        average_winner=_mean(winners),
        average_loser=_mean(losers),
        profit_factor=(float(gross_win / -gross_loss) if losers and gross_loss != 0 else None),
        r_sample_count=len(r_values),
        average_r=(float(_mean(r_values) or 0) if r_values else None),
        cost_sample_count=cost_sample_count,
        fees_total=sum(fees, Decimal("0")) if fees else None,
        funding_total=sum(fundings, Decimal("0")) if fundings else None,
        slippage_total=sum(slippages, Decimal("0")) if slippages else None,
        total_costs=sum(cost_parts, Decimal("0")) if cost_parts else None,
        mfe_sample_count=len(mfe_values),
        average_mfe_amount=_mean(mfe_values),
        mae_sample_count=len(mae_values),
        average_mae_amount=_mean(mae_values),
        capture_sample_count=len(capture_rows),
        available_profit_total=(
            sum(
                (row.available_profit for row in capture_rows if row.available_profit),
                Decimal("0"),
            )
            if capture_rows
            else None
        ),
        realized_on_available_total=(
            sum((row.net_pnl for row in capture_rows if row.net_pnl is not None), Decimal("0"))
            if capture_rows
            else None
        ),
        average_realized_vs_available_pct=(
            sum(capture_pcts) / len(capture_pcts) if capture_pcts else None
        ),
        confidence=confidence,
        warnings=warnings,
    )
