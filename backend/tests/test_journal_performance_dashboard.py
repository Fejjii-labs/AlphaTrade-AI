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
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import ValidationAppError
from app.db.models import JournalTrade, TradePlanRevision
from app.repositories.journal_trades import (
    JournalTradeStatsRow,
    SourceVenueRule,
    VenueRequirement,
)
from app.schemas.common import (
    JournalEntryMethod,
    JournalTradeSource,
    JournalTradeStatus,
    MarketRegime,
    TradeDirection,
    TradeResult,
)
from app.schemas.journal_performance_dashboard import (
    JournalDashboardFilters,
    JournalDashboardScope,
)
from app.schemas.journal_statistics import SampleConfidence
from app.services.journal_performance_dashboard_service import (
    SCOPE_LABELS,
    SCOPE_RULES,
    JournalPerformanceDashboardService,
    ScoreTargets,
    _local_date,
    build_dashboard,
    local_day_bounds,
)
from tests.test_at031_journal_statistics import (
    _BASE,
    ORG_A,
    USER_A,
    _auth,
    _build_env,
    _closed_trade,
)

TARGETS = ScoreTargets(
    win_rate=0.6,
    profit_factor=2.5,
    win_loss_ratio=2.5,
    recovery_factor=3.0,
    drawdown_tolerance=0.5,
)
UTC_TZ = ZoneInfo("UTC")
DEMO = JournalTradeSource.MANUAL_DEMO_TEST


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
        filters=JournalDashboardFilters(
            scope=JournalDashboardScope.DEMO, scope_label="Demo trades (BloFin demo)", timezone=tz
        ),
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
    # Four trades is an insufficient sample: overall is withheld, components stay.
    assert _build(rows).score.overall is None
    fifth = _row("0", datetime(2026, 7, 4, 9, tzinfo=UTC), result=TradeResult.BREAKEVEN)
    overall = _build([*rows, fifth]).score.overall
    assert overall == pytest.approx((83.33 + 60 + 60 + 22.22 + 0 + 20) / 6, abs=0.02)


def test_single_winning_trade_withholds_overall_score() -> None:
    result = _build([_row("50", datetime(2026, 7, 1, tzinfo=UTC))])
    assert result.confidence == SampleConfidence.INSUFFICIENT
    assert result.score.overall is None
    assert {c.key: c.score for c in result.score.components}["win_rate"] == 100.0
    assert "Overall score withheld until at least 5 closed trades." in result.warnings


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

    def fetch_dashboard_rows(self, **kwargs: Any) -> tuple[list[JournalTradeStatsRow], bool]:
        self.calls.append(kwargs)
        return self.rows, False


def _service(trades: _FakeTrades, **overrides: object) -> JournalPerformanceDashboardService:
    settings = Settings(**{**_BASE, **overrides})  # type: ignore[arg-type]
    return JournalPerformanceDashboardService(None, settings=settings, trades=trades)


def test_scope_rules_partition_every_source_and_venue() -> None:
    venues = (VenueRequirement.BLOFIN_DEMO, VenueRequirement.NOT_BLOFIN_DEMO)

    def covers(rule: SourceVenueRule, venue: VenueRequirement) -> bool:
        return rule.venue in (VenueRequirement.ANY, venue)

    for source in JournalTradeSource:
        for venue in venues:
            owners = [
                scope
                for scope, rules in SCOPE_RULES.items()
                for rule in rules
                if source in rule.sources and covers(rule, venue)
            ]
            assert len(owners) == 1, (source, venue, owners)
    assert set(SCOPE_RULES) == set(JournalDashboardScope) == set(SCOPE_LABELS)


def test_scope_rules_match_the_brief() -> None:
    def owner(source: JournalTradeSource, venue: VenueRequirement) -> JournalDashboardScope:
        return next(
            scope
            for scope, rules in SCOPE_RULES.items()
            for rule in rules
            if source in rule.sources and rule.venue in (VenueRequirement.ANY, venue)
        )

    on, off = VenueRequirement.BLOFIN_DEMO, VenueRequirement.NOT_BLOFIN_DEMO
    demo, paper, real = (
        JournalDashboardScope.DEMO,
        JournalDashboardScope.PAPER,
        JournalDashboardScope.REAL,
    )
    assert owner(JournalTradeSource.MANUAL_DEMO_TEST, off) == demo
    assert owner(JournalTradeSource.BLOFIN_DEMO_MANUAL, off) == demo
    assert owner(JournalTradeSource.PAPER_EXECUTION, on) == demo
    assert owner(JournalTradeSource.MANUAL, on) == demo
    assert owner(JournalTradeSource.PAPER_EXECUTION, off) == paper
    assert owner(JournalTradeSource.PAPER_VALIDATION, on) == paper
    assert owner(JournalTradeSource.MANUAL, off) == real
    for hidden in ("backtest", "imported", "system"):
        source = JournalTradeSource(hidden)
        assert owner(source, on) == owner(source, off) == JournalDashboardScope(hidden)


def test_service_defaults_to_demo_scope_and_utc_bounds() -> None:
    trades = _FakeTrades([])
    service = _service(trades, journal_dashboard_default_timezone="Europe/Paris")
    result = service.compute(
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        date_from=date(2026, 7, 1),
        date_to=date(2026, 7, 1),
    )
    call = trades.calls[0]
    assert call["date_from"] == datetime(2026, 6, 30, 22, tzinfo=UTC)
    assert call["rules"] == SCOPE_RULES[JournalDashboardScope.DEMO]
    assert result.filters.scope == JournalDashboardScope.DEMO
    assert result.filters.scope_label == "Demo trades (BloFin demo)"
    assert result.filters.timezone == "Europe/Paris"
    assert set(result.filters.included_sources) == {
        JournalTradeSource.MANUAL_DEMO_TEST,
        JournalTradeSource.BLOFIN_DEMO_MANUAL,
        JournalTradeSource.PAPER_EXECUTION,
        JournalTradeSource.MANUAL,
    }


def test_service_source_override_narrows_inside_the_scope() -> None:
    trades = _FakeTrades([])
    result = _service(trades).compute(
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scope=JournalDashboardScope.PAPER,
        source=JournalTradeSource.PAPER_EXECUTION,
    )
    assert trades.calls[0]["rules"] == (
        SourceVenueRule(
            frozenset({JournalTradeSource.PAPER_EXECUTION}), VenueRequirement.NOT_BLOFIN_DEMO
        ),
    )
    assert result.filters.included_sources == [JournalTradeSource.PAPER_EXECUTION]


def test_service_rejects_source_outside_the_scope() -> None:
    trades = _FakeTrades([])
    with pytest.raises(ValidationAppError, match="not part of"):
        _service(trades).compute(
            organization_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            scope=JournalDashboardScope.DEMO,
            source=JournalTradeSource.BACKTEST,
        )
    assert trades.calls == []


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
    _closed_trade(
        factory, source=DEMO, net_pnl="40", exit_time=datetime(2026, 7, 10, 12, tzinfo=UTC)
    )
    _closed_trade(
        factory, source=DEMO, net_pnl="-10", exit_time=datetime(2026, 7, 11, 12, tzinfo=UTC)
    )
    _closed_trade(
        factory, source=DEMO, net_pnl="999", exit_time=datetime(2026, 8, 1, 12, tzinfo=UTC)
    )
    _closed_trade(factory, source=DEMO, net_pnl="500", status=JournalTradeStatus.OPEN)

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


def _set_columns(factory: sessionmaker[Session], trade_id: uuid.UUID, **values: object) -> None:
    with factory() as session:
        row = session.get(JournalTrade, trade_id)
        assert row is not None
        for key, value in values.items():
            setattr(row, key, value)
        session.commit()


def _plan_revision(factory: sessionmaker[Session], *, venue: str) -> uuid.UUID:
    """Minimal immutable plan revision carrying only the venue (FKs off: test-only)."""
    revision_id = uuid.uuid4()
    manual = venue == "BLOFIN_DEMO"
    with factory() as session:
        session.execute(text("PRAGMA foreign_keys=OFF"))
        session.add(
            TradePlanRevision(
                id=revision_id,
                plan_id=uuid.uuid4(),
                organization_id=ORG_A,
                user_id=USER_A,
                account_id=uuid.uuid4(),
                schema_version="ManualDemoTradePlanV1" if manual else "CanonicalTradePlanContentV1",
                operation="SUBMIT_ENTRY",
                plan_authority="manual_demo_test" if manual else "paper_validation",
                strategy_version_id=None if manual else uuid.uuid4(),
                setup_definition_id=None if manual else uuid.uuid4(),
                candidate_id=None if manual else uuid.uuid4(),
                expected_account_mode="NET",
                permission_attestation_id=uuid.uuid4(),
                permission_attestation_version="v1",
                execution_venue=venue,
                execution_instrument="BTC-USDT",
                execution_policy_version="manual-blofin-demo/v1" if manual else "paper/v1",
                valid_from=datetime(2026, 7, 1, tzinfo=UTC),
                valid_until=datetime(2026, 8, 1, tzinfo=UTC),
                semantic_payload={},
                correlation_id=uuid.uuid4(),
                content_hash=revision_id.hex * 2,
            )
        )
        session.commit()
        session.execute(text("PRAGMA foreign_keys=ON"))
    return revision_id


def test_calendar_endpoint_scopes_never_mix(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    demo_plan = _plan_revision(factory, venue="BLOFIN_DEMO")
    paper_plan = _plan_revision(factory, venue="PAPER_INTERNAL")
    # Demo: 1 + 2 + 4 + 8 + 16 = 31
    _closed_trade(factory, net_pnl="1", source=JournalTradeSource.MANUAL_DEMO_TEST)
    _closed_trade(factory, net_pnl="2", source=JournalTradeSource.BLOFIN_DEMO_MANUAL)
    auto_demo = _closed_trade(factory, net_pnl="4", source=JournalTradeSource.PAPER_EXECUTION)
    _set_columns(factory, auto_demo, trade_plan_revision_id=demo_plan)
    reconciled = _closed_trade(factory, net_pnl="8", source=JournalTradeSource.MANUAL)
    _set_columns(factory, reconciled, external_ref="blofin-demo-history:42")
    logged_demo = _closed_trade(factory, net_pnl="16", source=JournalTradeSource.MANUAL)
    _set_columns(factory, logged_demo, exchange="blofin_demo")
    # Paper: 100 + 200 + 400 = 700
    _closed_trade(factory, net_pnl="100", source=JournalTradeSource.PAPER_VALIDATION)
    _closed_trade(factory, net_pnl="200", source=JournalTradeSource.PAPER_EXECUTION)
    internal = _closed_trade(factory, net_pnl="400", source=JournalTradeSource.PAPER_EXECUTION)
    _set_columns(factory, internal, trade_plan_revision_id=paper_plan)
    # Real: 1000 + 2000
    _closed_trade(factory, net_pnl="1000", source=JournalTradeSource.MANUAL)
    real_elsewhere = _closed_trade(factory, net_pnl="2000", source=JournalTradeSource.MANUAL)
    _set_columns(factory, real_elsewhere, exchange="BLOFIN")
    # Opt-in only.
    _closed_trade(factory, net_pnl="10000", source=JournalTradeSource.BACKTEST)
    _closed_trade(factory, net_pnl="20000", source=JournalTradeSource.IMPORTED)
    _closed_trade(factory, net_pnl="40000", source=JournalTradeSource.SYSTEM)

    def get(params: dict[str, str]) -> dict[str, Any]:
        response = test_client.get("/journal/statistics/calendar", headers=headers, params=params)
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def net(params: dict[str, str]) -> tuple[int, Decimal]:
        body = get(params)
        return body["summary"]["trade_count"], Decimal(body["summary"]["net_pnl"])

    default = get({})
    assert default["filters"]["scope"] == "demo"
    assert default["filters"]["scope_label"] == "Demo trades (BloFin demo)"
    assert (default["summary"]["trade_count"], Decimal(default["summary"]["net_pnl"])) == (
        5,
        Decimal("31"),
    )
    assert net({"scope": "demo"}) == (5, Decimal("31"))
    assert net({"scope": "paper"}) == (3, Decimal("700"))
    assert net({"scope": "real"}) == (2, Decimal("3000"))
    assert net({"scope": "backtest"}) == (1, Decimal("10000"))
    assert net({"scope": "imported"}) == (1, Decimal("20000"))
    assert net({"scope": "system"}) == (1, Decimal("40000"))
    assert net({"scope": "demo", "source": "paper_execution"}) == (1, Decimal("4"))
    assert net({"scope": "paper", "source": "paper_execution"}) == (2, Decimal("600"))
    bad = test_client.get(
        "/journal/statistics/calendar",
        headers=headers,
        params={"scope": "real", "source": "backtest"},
    )
    assert bad.status_code in (400, 422)
    assert (
        test_client.get(
            "/journal/statistics/calendar", headers=headers, params={"scope": "live"}
        ).status_code
        == 422
    )


def test_calendar_endpoint_dst_fall_back_trade_lands_in_its_local_day(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    # 23:30 local (second, -04 occurrence) on 2026-04-04 in Santiago.
    _closed_trade(
        factory, source=DEMO, net_pnl="12", exit_time=datetime(2026, 4, 5, 3, 30, tzinfo=UTC)
    )
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
    _closed_trade(factory, source=DEMO, net_pnl="40")
    headers = _auth(test_client, "stats-b@test.example")
    body = test_client.get("/journal/statistics/calendar", headers=headers).json()
    assert body["summary"]["trade_count"] == 0
    assert body["score"]["overall"] is None
