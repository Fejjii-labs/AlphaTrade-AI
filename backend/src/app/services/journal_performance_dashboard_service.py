"""Journal performance dashboard — calendar, cumulative curve, and score.

Deterministic, record-only read model over the caller's CLOSED canonical
``journal_trades`` (same bounded projection as AT-031 statistics). No live
market I/O, no execution authority, no mutation.

Rules (documented so every number can be re-derived by hand):

- Win/loss/breakeven uses the recorded ``result`` with the AT-031 net-PnL sign
  fallback; win rate is wins / (wins + losses).
- Trades are bucketed by the local calendar date of their effective time
  (exit, else entry, else creation) in the requested IANA timezone.
- Max drawdown is the largest peak-to-trough drop of the per-trade cumulative
  net PnL curve, starting from a 0 baseline, in effective-time order.
- Recovery factor = net PnL / max drawdown (None when there is no drawdown).
- Consistency = 1 - (largest winning day / sum of winning days), defined only
  with at least two winning days.
- Radar scores normalize each metric to 0-100 against a configured target;
  the overall score is the equal-weight mean of computable components.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ValidationAppError
from app.repositories.journal_trades import JournalTradeRepository, JournalTradeStatsRow
from app.schemas.common import JournalTradeSource, TradeResult
from app.schemas.journal_performance_dashboard import (
    JournalDashboardCurvePoint,
    JournalDashboardDay,
    JournalDashboardFilters,
    JournalDashboardRisk,
    JournalDashboardScore,
    JournalDashboardScoreComponent,
    JournalDashboardSummary,
    JournalPerformanceDashboardResponse,
)
from app.schemas.journal_statistics import ExecutionActor, SampleConfidence
from app.services.journal_statistics_service import (
    _EXECUTION_ACTOR_BY_SOURCE,
    _confidence,
    _decide_result,
)

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class ScoreTargets:
    """Normalization targets: a metric at its target scores 100."""

    win_rate: float
    profit_factor: float
    win_loss_ratio: float
    recovery_factor: float
    drawdown_tolerance: float

    @classmethod
    def from_settings(cls, settings: Settings) -> ScoreTargets:
        return cls(
            win_rate=settings.journal_dashboard_target_win_rate,
            profit_factor=settings.journal_dashboard_target_profit_factor,
            win_loss_ratio=settings.journal_dashboard_target_win_loss_ratio,
            recovery_factor=settings.journal_dashboard_target_recovery_factor,
            drawdown_tolerance=settings.journal_dashboard_drawdown_tolerance,
        )


class _StatsRowSource(Protocol):
    def fetch_stats_rows(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        sources: Collection[JournalTradeSource] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        max_rows: int,
    ) -> tuple[list[JournalTradeStatsRow], bool]: ...


def resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationAppError(f"Unknown IANA timezone: {name!r}.") from exc


def local_day_bounds(
    date_from: date | None, date_to: date | None, tz: ZoneInfo
) -> tuple[datetime | None, datetime | None]:
    """Inclusive local-day range → UTC instants (end is the last microsecond)."""
    start = datetime.combine(date_from, time.min, tz).astimezone(UTC) if date_from else None
    end = (
        (
            datetime.combine(date_to + timedelta(days=1), time.min, tz) - timedelta(microseconds=1)
        ).astimezone(UTC)
        if date_to
        else None
    )
    return start, end


def _sources_for(
    source: JournalTradeSource | None, actor: ExecutionActor | None
) -> frozenset[JournalTradeSource] | None:
    if actor is None:
        return frozenset({source}) if source is not None else None
    actor_sources = frozenset(s for s, a in _EXECUTION_ACTOR_BY_SOURCE.items() if a == actor)
    if source is not None:
        return actor_sources & {source}
    return actor_sources


class JournalPerformanceDashboardService:
    """Calendar/curve/score read model over canonical journal trades."""

    def __init__(
        self,
        session: Session | None,
        *,
        settings: Settings,
        trades: _StatsRowSource | None = None,
    ) -> None:
        if trades is None:
            if session is None:
                raise ValueError("session or trades repository required")
            trades = JournalTradeRepository(session)
        self._trades = trades
        self._max_rows = settings.journal_stats_max_rows
        self._default_tz = settings.journal_dashboard_default_timezone
        self._targets = ScoreTargets.from_settings(settings)

    def compute(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        date_from: date | None = None,
        date_to: date | None = None,
        timezone: str | None = None,
        source: JournalTradeSource | None = None,
        execution_actor: ExecutionActor | None = None,
    ) -> JournalPerformanceDashboardResponse:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise ValidationAppError("date_from must not be after date_to.")
        tz_name = timezone or self._default_tz
        tz = resolve_timezone(tz_name)
        start, end = local_day_bounds(date_from, date_to, tz)
        sources = _sources_for(source, execution_actor)
        filters = JournalDashboardFilters(
            date_from=date_from,
            date_to=date_to,
            timezone=tz_name,
            source=source,
            execution_actor=execution_actor,
        )
        if sources is not None and not sources:
            rows: list[JournalTradeStatsRow] = []
            truncated = False
        else:
            rows, truncated = self._trades.fetch_stats_rows(
                organization_id=organization_id,
                user_id=user_id,
                sources=sources,
                date_from=start,
                date_to=end,
                max_rows=self._max_rows,
            )
        return build_dashboard(
            rows,
            filters=filters,
            tz=tz,
            targets=self._targets,
            truncated=truncated,
            max_rows=self._max_rows,
        )


# --------------------------------------------------------------------------- #
# Pure computation (unit-testable without a database)
# --------------------------------------------------------------------------- #


def _local_date(moment: datetime, tz: ZoneInfo) -> date:
    # SQLite returns naive datetimes; persisted times are UTC.
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(tz).date()


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize(rows: list[JournalTradeStatsRow]) -> JournalDashboardSummary:
    results = [_decide_result(row) for row in rows]
    wins = sum(1 for r in results if r == TradeResult.WIN)
    losses = sum(1 for r in results if r == TradeResult.LOSS)
    breakeven = sum(1 for r in results if r == TradeResult.BREAKEVEN)
    pnls = [row.net_pnl for row in rows if row.net_pnl is not None]
    if not pnls:
        return JournalDashboardSummary(
            trade_count=len(rows),
            wins=wins,
            losses=losses,
            breakeven=breakeven,
            win_rate=_ratio(wins, wins + losses),
        )
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p < 0]
    gross_profit = sum(winners, _ZERO)
    gross_loss = sum(losers, _ZERO)
    average_win = gross_profit / len(winners) if winners else None
    average_loss = gross_loss / len(losers) if losers else None
    return JournalDashboardSummary(
        trade_count=len(rows),
        pnl_sample_count=len(pnls),
        wins=wins,
        losses=losses,
        breakeven=breakeven,
        win_rate=_ratio(wins, wins + losses),
        net_pnl=sum(pnls, _ZERO),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=float(gross_profit / -gross_loss) if gross_loss < 0 else None,
        average_win=average_win,
        average_loss=average_loss,
        win_loss_ratio=(
            float(average_win / -average_loss)
            if average_win is not None and average_loss is not None
            else None
        ),
    )


def bucket_days(rows: list[JournalTradeStatsRow], tz: ZoneInfo) -> list[JournalDashboardDay]:
    grouped: dict[date, list[JournalTradeStatsRow]] = defaultdict(list)
    for row in rows:
        if row.effective_time is not None:
            grouped[_local_date(row.effective_time, tz)].append(row)
    days: list[JournalDashboardDay] = []
    for day in sorted(grouped):
        day_rows = grouped[day]
        results = [_decide_result(row) for row in day_rows]
        wins = sum(1 for r in results if r == TradeResult.WIN)
        losses = sum(1 for r in results if r == TradeResult.LOSS)
        pnls = [row.net_pnl for row in day_rows if row.net_pnl is not None]
        days.append(
            JournalDashboardDay(
                date=day,
                net_pnl=sum(pnls, _ZERO) if pnls else None,
                trade_count=len(day_rows),
                wins=wins,
                losses=losses,
                breakeven=sum(1 for r in results if r == TradeResult.BREAKEVEN),
                win_rate=_ratio(wins, wins + losses),
            )
        )
    return days


def cumulative_curve(days: list[JournalDashboardDay]) -> list[JournalDashboardCurvePoint]:
    points: list[JournalDashboardCurvePoint] = []
    running = _ZERO
    for day in days:
        if day.net_pnl is None:
            continue
        running += day.net_pnl
        points.append(
            JournalDashboardCurvePoint(
                date=day.date, daily_net_pnl=day.net_pnl, cumulative_net_pnl=running
            )
        )
    return points


def risk_metrics(
    rows: list[JournalTradeStatsRow],
    days: list[JournalDashboardDay],
    net_pnl: Decimal | None,
) -> JournalDashboardRisk:
    pnls = [row.net_pnl for row in rows if row.net_pnl is not None]
    winning = [d.net_pnl for d in days if d.net_pnl is not None and d.net_pnl > 0]
    losing_days = sum(1 for d in days if d.net_pnl is not None and d.net_pnl < 0)
    consistency: float | None = None
    if len(winning) >= 2:
        consistency = float(1 - max(winning) / sum(winning, _ZERO))
    if not pnls:
        return JournalDashboardRisk(
            consistency=consistency, winning_days=len(winning), losing_days=losing_days
        )
    running = peak = max_dd = _ZERO
    for pnl in pnls:
        running += pnl
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    recovery = float(net_pnl / max_dd) if net_pnl is not None and max_dd > 0 else None
    return JournalDashboardRisk(
        max_drawdown=max_dd,
        recovery_factor=recovery,
        consistency=consistency,
        winning_days=len(winning),
        losing_days=losing_days,
    )


def _scaled(value: float, target: float) -> float:
    return round(max(0.0, min(value / target, 1.0)) * 100, 2)


def score(
    summary: JournalDashboardSummary,
    risk: JournalDashboardRisk,
    targets: ScoreTargets,
) -> JournalDashboardScore:
    has_pnl = summary.pnl_sample_count > 0
    no_losers = has_pnl and summary.gross_loss == _ZERO
    components: list[JournalDashboardScoreComponent] = []

    def add(
        key: str,
        label: str,
        value: float | None,
        score_value: float | None,
        target: float | None,
        note: str | None = None,
    ) -> None:
        components.append(
            JournalDashboardScoreComponent(
                key=key, label=label, value=value, score=score_value, target=target, note=note
            )
        )

    win_rate = summary.win_rate
    add(
        "win_rate",
        "Win %",
        win_rate,
        _scaled(win_rate, targets.win_rate) if win_rate is not None else None,
        targets.win_rate,
    )

    pf = summary.profit_factor
    if pf is not None:
        add(
            "profit_factor",
            "Profit factor",
            pf,
            _scaled(pf, targets.profit_factor),
            targets.profit_factor,
        )
    elif no_losers and (summary.gross_profit or _ZERO) > 0:
        add(
            "profit_factor",
            "Profit factor",
            None,
            100.0,
            targets.profit_factor,
            "No losing trades; capped at target.",
        )
    else:
        add("profit_factor", "Profit factor", None, None, targets.profit_factor)

    ratio = summary.win_loss_ratio
    if ratio is not None:
        add(
            "win_loss_ratio",
            "Avg win/loss",
            ratio,
            _scaled(ratio, targets.win_loss_ratio),
            targets.win_loss_ratio,
        )
    elif summary.average_win is not None:
        add(
            "win_loss_ratio",
            "Avg win/loss",
            None,
            100.0,
            targets.win_loss_ratio,
            "No losing trades; capped at target.",
        )
    elif summary.average_loss is not None:
        add(
            "win_loss_ratio",
            "Avg win/loss",
            None,
            0.0,
            targets.win_loss_ratio,
            "No winning trades.",
        )
    else:
        add("win_loss_ratio", "Avg win/loss", None, None, targets.win_loss_ratio)

    rf = risk.recovery_factor
    if rf is not None:
        add(
            "recovery_factor",
            "Recovery factor",
            rf,
            _scaled(rf, targets.recovery_factor),
            targets.recovery_factor,
        )
    elif risk.max_drawdown == _ZERO and (summary.net_pnl or _ZERO) > 0:
        add(
            "recovery_factor",
            "Recovery factor",
            None,
            100.0,
            targets.recovery_factor,
            "No drawdown recorded; capped at target.",
        )
    else:
        add("recovery_factor", "Recovery factor", None, None, targets.recovery_factor)

    dd = risk.max_drawdown
    gross_profit = summary.gross_profit
    if dd is None:
        add("max_drawdown", "Max drawdown", None, None, targets.drawdown_tolerance)
    elif gross_profit is not None and gross_profit > 0:
        share = float(dd / gross_profit)
        add(
            "max_drawdown",
            "Max drawdown",
            share,
            round(max(0.0, 1 - min(share / targets.drawdown_tolerance, 1.0)) * 100, 2),
            targets.drawdown_tolerance,
            "Drawdown as a share of gross profit.",
        )
    elif dd > 0:
        add(
            "max_drawdown",
            "Max drawdown",
            None,
            0.0,
            targets.drawdown_tolerance,
            "Drawdown with no gross profit.",
        )
    else:
        add("max_drawdown", "Max drawdown", None, None, targets.drawdown_tolerance)

    consistency = risk.consistency
    add(
        "consistency",
        "Consistency",
        consistency,
        round(max(0.0, consistency) * 100, 2) if consistency is not None else None,
        None,
        "Needs at least two winning days." if consistency is None else None,
    )

    scored = [c.score for c in components if c.score is not None]
    return JournalDashboardScore(
        overall=round(sum(scored) / len(scored), 2) if scored else None,
        components=components,
        missing_components=[c.key for c in components if c.score is None],
    )


def build_dashboard(
    rows: list[JournalTradeStatsRow],
    *,
    filters: JournalDashboardFilters,
    tz: ZoneInfo,
    targets: ScoreTargets,
    truncated: bool,
    max_rows: int,
    now: datetime | None = None,
) -> JournalPerformanceDashboardResponse:
    summary = summarize(rows)
    days = bucket_days(rows, tz)
    risk = risk_metrics(rows, days, summary.net_pnl)
    confidence = _confidence(len(rows))
    warnings: list[str] = []
    if not rows:
        warnings.append("No closed trades match the filters.")
    elif confidence in (SampleConfidence.INSUFFICIENT, SampleConfidence.LOW):
        warnings.append(f"Only {len(rows)} closed trade(s); treat these figures as anecdotal.")
    if summary.pnl_sample_count < summary.trade_count:
        warnings.append(
            f"{summary.trade_count - summary.pnl_sample_count} closed trade(s) have no "
            "recorded net PnL and are excluded from PnL figures."
        )
    if truncated:
        warnings.append(
            f"Capped at {max_rows} closed trades; figures cover only the oldest trades "
            "in range. Narrow the date range."
        )
    return JournalPerformanceDashboardResponse(
        filters=filters,
        summary=summary,
        days=days,
        cumulative=cumulative_curve(days),
        risk=risk,
        score=score(summary, risk, targets),
        confidence=confidence,
        truncated=truncated,
        max_rows=max_rows,
        warnings=warnings,
        generated_at=now or datetime.now(UTC),
    )
