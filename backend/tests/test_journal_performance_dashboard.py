"""Journal performance dashboard — calendar buckets, curve, drawdown, and score.

Pure computations run on hand-built stats rows; the API test uses in-memory
SQLite (no Postgres).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import ValidationAppError
from app.repositories.journal_trades import JournalTradeStatsRow
from app.schemas.common import (
    JournalEntryMethod,
    JournalTradeSource,
    JournalTradeStatus,
    MarketRegime,
    TradeDirection,
    TradeResult,
)
from app.schemas.journal_performance_dashboard import JournalDashboardFilters
from app.schemas.journal_statistics import ExecutionActor, SampleConfidence
from app.services.journal_performance_dashboard_service import (
    JournalPerformanceDashboardService,
    ScoreTargets,
    _local_date,
    build_dashboard,
    local_day_bounds,
)
from tests.test_at031_journal_statistics import _BASE, _auth, _build_env, _closed_trade

TARGETS = ScoreTargets(
    win_rate=0.6,
    profit_factor=2.5,
    win_loss_ratio=2.5,
    recovery_factor=3.0,
    drawdown_tolerance=0.5,
)
UTC_TZ = ZoneInfo("UTC")


def _row(
    pnl: str | None,
    when: datetime,
    *,
    result: TradeResult = TradeResult.OPEN,
    source: JournalTradeSource = JournalTradeSource.MANUAL,
) -> JournalTradeStatsRow:
    return JournalTradeStatsRow(
        id=uuid.uuid4(),
        source=source,
        entry_method=JournalEntryMethod.MANUAL,
        symbol="BTCUSDT",
        timeframe="1h",
        market_regime=MarketRegime.TRENDING_UP,
        setup_id=None,
        user_strategy_id=None,
        strategy_version_id=None,
        result=result,
        net_pnl=Decimal(pnl) if pnl is not None else None,
        gross_pnl=None,
        fees=None,
        funding=None,
        slippage=None,
        planned_risk_amount=None,
        mfe_amount=None,
        mae_amount=None,
        available_profit=None,
        realized_vs_available_pct=None,
        direction=TradeDirection.LONG,
        planned_entry_price=None,
        entry_price=None,
        exit_reason=None,
        strategy_label=None,
        effective_time=when,
    )


def _build(rows: list[JournalTradeStatsRow], tz: str = "UTC", truncated: bool = False) -> Any:
    return build_dashboard(
        rows,
        filters=JournalDashboardFilters(timezone=tz),
        tz=ZoneInfo(tz),
        targets=TARGETS,
        truncated=truncated,
        max_rows=5000,
        now=datetime(2026, 7, 31, tzinfo=UTC),
    )


def test_empty_rows_return_nulls_not_zeros() -> None:
    result = _build([])
    assert result.summary.trade_count == 0
    assert result.summary.net_pnl is None
    assert result.summary.profit_factor is None
    assert result.summary.win_rate is None
    assert result.days == []
    assert result.cumulative == []
    assert result.risk.max_drawdown is None
    assert result.risk.recovery_factor is None
    assert result.score.overall is None
    assert all(c.score is None for c in result.score.components)
    assert result.confidence == SampleConfidence.INSUFFICIENT
    assert result.warnings == ["No closed trades match the filters."]


def test_summary_days_curve_drawdown_exact_values() -> None:
    rows = [
        _row("100", datetime(2026, 7, 1, 10, tzinfo=UTC)),
        _row("-50", datetime(2026, 7, 1, 15, tzinfo=UTC)),
        _row("200", datetime(2026, 7, 2, 9, tzinfo=UTC)),
        _row("-150", datetime(2026, 7, 3, 9, tzinfo=UTC)),
        _row("0", datetime(2026, 7, 3, 11, tzinfo=UTC)),
    ]
    result = _build(rows)
    s = result.summary
    assert (s.trade_count, s.wins, s.losses, s.breakeven) == (5, 2, 2, 1)
    assert s.win_rate == 0.5
    assert s.net_pnl == Decimal("100")
    assert s.gross_profit == Decimal("300")
    assert s.gross_loss == Decimal("-200")
    assert s.profit_factor == 1.5
    assert s.average_win == Decimal("150")
    assert s.average_loss == Decimal("-100")
    assert s.win_loss_ratio == 1.5

    assert [(d.date, d.net_pnl, d.trade_count, d.wins, d.losses) for d in result.days] == [
        (date(2026, 7, 1), Decimal("50"), 2, 1, 1),
        (date(2026, 7, 2), Decimal("200"), 1, 1, 0),
        (date(2026, 7, 3), Decimal("-150"), 2, 0, 1),
    ]
    assert result.days[2].breakeven == 1
    assert result.days[2].win_rate == 0.0
    assert [p.cumulative_net_pnl for p in result.cumulative] == [
        Decimal("50"),
        Decimal("250"),
        Decimal("100"),
    ]
    # Per-trade curve: 100, 50, 250, 100, 100 → peak 250, trough 100.
    assert result.risk.max_drawdown == Decimal("150")
    assert result.risk.recovery_factor == pytest.approx(100 / 150)
    # Winning days 50 and 200: 1 - 200/250.
    assert result.risk.consistency == pytest.approx(0.2)
    assert (result.risk.winning_days, result.risk.losing_days) == (2, 1)


def test_scores_normalize_against_targets() -> None:
    rows = [
        _row("100", datetime(2026, 7, 1, 10, tzinfo=UTC)),
        _row("-50", datetime(2026, 7, 1, 15, tzinfo=UTC)),
        _row("200", datetime(2026, 7, 2, 9, tzinfo=UTC)),
        _row("-150", datetime(2026, 7, 3, 9, tzinfo=UTC)),
    ]
    by_key = {c.key: c for c in _build(rows).score.components}
    assert by_key["win_rate"].score == pytest.approx(83.33, abs=0.01)  # 0.5 / 0.6
    assert by_key["profit_factor"].score == 60.0  # 1.5 / 2.5
    assert by_key["win_loss_ratio"].score == 60.0  # 1.5 / 2.5
    assert by_key["recovery_factor"].score == pytest.approx(22.22, abs=0.01)  # 0.667 / 3
    # Drawdown 150 / gross profit 300 = 0.5 → at tolerance → 0.
    assert by_key["max_drawdown"].score == 0.0
    assert by_key["consistency"].score == 20.0
    overall = _build(rows).score.overall
    assert overall == pytest.approx((83.33 + 60 + 60 + 22.22 + 0 + 20) / 6, abs=0.02)


def test_no_losses_keeps_profit_factor_null_but_caps_score() -> None:
    rows = [
        _row("10", datetime(2026, 7, 1, tzinfo=UTC)),
        _row("30", datetime(2026, 7, 2, tzinfo=UTC)),
    ]
    result = _build(rows)
    assert result.summary.profit_factor is None
    assert result.summary.win_loss_ratio is None
    assert result.risk.max_drawdown == Decimal("0")
    assert result.risk.recovery_factor is None
    by_key = {c.key: c for c in result.score.components}
    assert by_key["profit_factor"].score == 100.0
    assert by_key["profit_factor"].value is None
    assert by_key["recovery_factor"].score == 100.0
    assert by_key["max_drawdown"].score == 100.0


def test_missing_pnl_is_counted_but_not_summed() -> None:
    rows = [
        _row(None, datetime(2026, 7, 1, tzinfo=UTC), result=TradeResult.WIN),
        _row(None, datetime(2026, 7, 1, 2, tzinfo=UTC)),
    ]
    result = _build(rows)
    assert result.summary.trade_count == 2
    assert result.summary.pnl_sample_count == 0
    assert result.summary.net_pnl is None
    assert result.summary.wins == 1
    assert result.days[0].net_pnl is None
    assert result.cumulative == []
    assert any("no recorded net PnL" in w for w in result.warnings)


def test_timezone_moves_trade_to_local_day() -> None:
    rows = [_row("5", datetime(2026, 7, 2, 2, 30, tzinfo=UTC))]
    assert _build(rows).days[0].date == date(2026, 7, 2)
    assert _build(rows, tz="America/New_York").days[0].date == date(2026, 7, 1)


def test_naive_effective_time_is_treated_as_utc() -> None:
    rows = [_row("5", datetime(2026, 7, 2, 2, 30))]
    assert _build(rows, tz="Asia/Tokyo").days[0].date == date(2026, 7, 2)


def test_truncation_is_flagged() -> None:
    result = _build([_row("5", datetime(2026, 7, 2, tzinfo=UTC))], truncated=True)
    assert result.truncated is True
    assert any("Capped at 5000" in w for w in result.warnings)


def test_local_day_bounds_cover_whole_local_days() -> None:
    start, end = local_day_bounds(date(2026, 7, 1), date(2026, 7, 31), ZoneInfo("Europe/Paris"))
    assert start == datetime(2026, 6, 30, 22, tzinfo=UTC)
    assert end == datetime(2026, 7, 31, 21, 59, 59, 999999, tzinfo=UTC)


def test_local_day_bounds_keep_repeated_hour_at_midnight_fall_back() -> None:
    # America/Santiago falls back at 2026-04-05 00:00 (-03 → -04): 23:00-23:59 on
    # 04-04 occurs twice, so local 04-04 ends at 04:00 UTC, not 03:00 UTC.
    tz = ZoneInfo("America/Santiago")
    start, end = local_day_bounds(date(2026, 4, 4), date(2026, 4, 4), tz)
    assert start == datetime(2026, 4, 4, 3, tzinfo=UTC)
    assert end == datetime(2026, 4, 5, 3, 59, 59, 999999, tzinfo=UTC)
    second_late_hour = datetime(2026, 4, 5, 3, 30, tzinfo=UTC)
    assert _local_date(second_late_hour, tz) == date(2026, 4, 4)
    assert start <= second_late_hour <= end
    next_start, _ = local_day_bounds(date(2026, 4, 5), None, tz)
    assert next_start == end + timedelta(microseconds=1)


class _FakeTrades:
    def __init__(self, rows: list[JournalTradeStatsRow]) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    def fetch_stats_rows(self, **kwargs: Any) -> tuple[list[JournalTradeStatsRow], bool]:
        self.calls.append(kwargs)
        return self.rows, False


def _service(trades: _FakeTrades, **overrides: object) -> JournalPerformanceDashboardService:
    settings = Settings(**{**_BASE, **overrides})  # type: ignore[arg-type]
    return JournalPerformanceDashboardService(None, settings=settings, trades=trades)


def test_service_passes_utc_bounds_and_actor_sources() -> None:
    trades = _FakeTrades([])
    service = _service(trades, journal_dashboard_default_timezone="Europe/Paris")
    result = service.compute(
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        date_from=date(2026, 7, 1),
        date_to=date(2026, 7, 1),
        execution_actor=ExecutionActor.SYSTEM,
    )
    call = trades.calls[0]
    assert call["date_from"] == datetime(2026, 6, 30, 22, tzinfo=UTC)
    assert call["sources"] == {
        JournalTradeSource.PAPER_VALIDATION,
        JournalTradeSource.SYSTEM,
    }
    assert result.filters.timezone == "Europe/Paris"
    assert result.filters.excluded_sources == [JournalTradeSource.BACKTEST]


def test_service_default_excludes_backtest_but_keeps_live_demo_and_autonomous() -> None:
    trades = _FakeTrades([])
    result = _service(trades).compute(organization_id=uuid.uuid4(), user_id=uuid.uuid4())
    sources = trades.calls[0]["sources"]
    assert JournalTradeSource.BACKTEST not in sources
    assert sources == set(JournalTradeSource) - {JournalTradeSource.BACKTEST}
    assert result.filters.excluded_sources == [JournalTradeSource.BACKTEST]


def test_service_human_actor_sources() -> None:
    trades = _FakeTrades([])
    _service(trades).compute(
        organization_id=uuid.uuid4(), user_id=uuid.uuid4(), execution_actor=ExecutionActor.HUMAN
    )
    assert trades.calls[0]["sources"] == {
        JournalTradeSource.MANUAL,
        JournalTradeSource.IMPORTED,
        JournalTradeSource.PAPER_EXECUTION,
        JournalTradeSource.MANUAL_DEMO_TEST,
        JournalTradeSource.BLOFIN_DEMO_MANUAL,
    }


def test_service_explicit_backtest_source_is_opt_in() -> None:
    trades = _FakeTrades([])
    result = _service(trades).compute(
        organization_id=uuid.uuid4(), user_id=uuid.uuid4(), source=JournalTradeSource.BACKTEST
    )
    assert trades.calls[0]["sources"] == {JournalTradeSource.BACKTEST}
    assert result.filters.excluded_sources == []


def test_service_conflicting_source_and_actor_skips_query() -> None:
    trades = _FakeTrades([_row("5", datetime(2026, 7, 2, tzinfo=UTC))])
    result = _service(trades).compute(
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        source=JournalTradeSource.MANUAL,
        execution_actor=ExecutionActor.SYSTEM,
    )
    assert trades.calls == []
    assert result.summary.trade_count == 0


def test_service_rejects_bad_timezone_and_range() -> None:
    service = _service(_FakeTrades([]))
    org, user = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(ValidationAppError):
        service.compute(organization_id=org, user_id=user, timezone="Mars/Olympus")
    with pytest.raises(ValidationAppError):
        service.compute(
            organization_id=org,
            user_id=user,
            date_from=date(2026, 7, 2),
            date_to=date(2026, 7, 1),
        )


def test_settings_reject_unknown_default_timezone() -> None:
    with pytest.raises(ValueError, match="IANA"):
        Settings(**{**_BASE, "journal_dashboard_default_timezone": "Nowhere/Land"})  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# API (in-memory SQLite)
# --------------------------------------------------------------------------- #


@pytest.fixture
def client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    test_client, factory = _build_env()
    with test_client:
        yield test_client, factory


def test_calendar_endpoint_over_real_rows(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    _closed_trade(factory, net_pnl="40", exit_time=datetime(2026, 7, 10, 12, tzinfo=UTC))
    _closed_trade(factory, net_pnl="-10", exit_time=datetime(2026, 7, 11, 12, tzinfo=UTC))
    _closed_trade(factory, net_pnl="999", exit_time=datetime(2026, 8, 1, 12, tzinfo=UTC))
    _closed_trade(factory, net_pnl="500", status=JournalTradeStatus.OPEN)

    response = test_client.get(
        "/journal/statistics/calendar",
        headers=headers,
        params={"date_from": "2026-07-01", "date_to": "2026-07-31", "tz": "UTC"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["trade_count"] == 2
    assert Decimal(body["summary"]["net_pnl"]) == Decimal("30")
    assert body["summary"]["profit_factor"] == 4.0
    assert [d["date"] for d in body["days"]] == ["2026-07-10", "2026-07-11"]
    assert Decimal(body["cumulative"][-1]["cumulative_net_pnl"]) == Decimal("30")
    assert body["truncated"] is False


def test_calendar_endpoint_excludes_backtest_and_filters_by_actor(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    _closed_trade(factory, net_pnl="40", source=JournalTradeSource.MANUAL)
    _closed_trade(factory, net_pnl="15", source=JournalTradeSource.BLOFIN_DEMO_MANUAL)
    _closed_trade(factory, net_pnl="7", source=JournalTradeSource.PAPER_VALIDATION)
    _closed_trade(factory, net_pnl="3", source=JournalTradeSource.SYSTEM)
    _closed_trade(factory, net_pnl="10000", source=JournalTradeSource.BACKTEST)

    def net(params: dict[str, str]) -> tuple[int, Decimal]:
        response = test_client.get("/journal/statistics/calendar", headers=headers, params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        return body["summary"]["trade_count"], Decimal(body["summary"]["net_pnl"])

    assert net({}) == (4, Decimal("65"))
    assert net({"execution_actor": "human"}) == (2, Decimal("55"))
    assert net({"execution_actor": "system"}) == (2, Decimal("10"))
    assert net({"source": "backtest"}) == (1, Decimal("10000"))
    body = test_client.get("/journal/statistics/calendar", headers=headers).json()
    assert body["filters"]["excluded_sources"] == ["backtest"]


def test_calendar_endpoint_dst_fall_back_trade_lands_in_its_local_day(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    # 23:30 local (second, -04 occurrence) on 2026-04-04 in Santiago.
    _closed_trade(factory, net_pnl="12", exit_time=datetime(2026, 4, 5, 3, 30, tzinfo=UTC))
    params = {"date_from": "2026-04-01", "date_to": "2026-04-04", "tz": "America/Santiago"}
    body = test_client.get("/journal/statistics/calendar", headers=headers, params=params).json()
    assert body["summary"]["trade_count"] == 1
    assert [d["date"] for d in body["days"]] == ["2026-04-04"]
    params = {"date_from": "2026-04-05", "date_to": "2026-04-30", "tz": "America/Santiago"}
    body = test_client.get("/journal/statistics/calendar", headers=headers, params=params).json()
    assert body["summary"]["trade_count"] == 0


def test_calendar_endpoint_requires_auth_and_valid_tz(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, _ = client
    assert test_client.get("/journal/statistics/calendar").status_code == 401
    headers = _auth(test_client, "stats-a@test.example")
    bad = test_client.get(
        "/journal/statistics/calendar", headers=headers, params={"tz": "Mars/Olympus"}
    )
    assert bad.status_code in (400, 422)


def test_calendar_endpoint_is_tenant_isolated(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    _closed_trade(factory, net_pnl="40")
    headers = _auth(test_client, "stats-b@test.example")
    body = test_client.get("/journal/statistics/calendar", headers=headers).json()
    assert body["summary"]["trade_count"] == 0
    assert body["score"]["overall"] is None
