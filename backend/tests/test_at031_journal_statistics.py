"""AT-031 — journal statistics & setup analytics v1 (grouped deterministic aggregates)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    JournalTrade,
    Membership,
    Organization,
    SetupDefinition,
    User,
    UserStrategy,
    UserStrategyVersion,
)
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import (
    JournalTradeSource,
    JournalTradeStatus,
    MarketRegime,
    MembershipRole,
    StrategyId,
    TradeDirection,
    TradeResult,
)
from app.security.passwords import hash_password
from app.security.rate_limit import reset_rate_limiter

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000009101")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000009102")
USER_A = uuid.UUID("00000000-0000-0000-0000-000000009111")
USER_B = uuid.UUID("00000000-0000-0000-0000-000000009112")
VIEWER_A = uuid.UUID("00000000-0000-0000-0000-000000009113")

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "journal-stats-test-secret-abc-32ch!",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
    "alert_delivery_enabled": False,
    "telegram_alerts_enabled": False,
    "worker_enabled": False,
    "market_watcher_enabled": False,
}


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


def _build_env(
    settings_overrides: dict[str, object] | None = None,
) -> tuple[TestClient, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(**{**_BASE, **(settings_overrides or {})})  # type: ignore[arg-type]

    with factory() as session:
        session.add(Organization(id=ORG_A, name="Stats Org A"))
        session.add(Organization(id=ORG_B, name="Stats Org B"))
        for user_id, email in (
            (USER_A, "stats-a@test.example"),
            (USER_B, "stats-b@test.example"),
            (VIEWER_A, "stats-viewer@test.example"),
        ):
            session.add(
                User(
                    id=user_id,
                    email=email,
                    hashed_password=hash_password("SecurePass123!", settings),
                    email_verified=True,
                )
            )
        session.flush()
        session.add(Membership(user_id=USER_A, organization_id=ORG_A, role=MembershipRole.OWNER))
        session.add(Membership(user_id=USER_B, organization_id=ORG_B, role=MembershipRole.OWNER))
        session.add(Membership(user_id=VIEWER_A, organization_id=ORG_A, role=MembershipRole.VIEWER))
        session.commit()

    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    return TestClient(app), factory


@pytest.fixture
def client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    test_client, factory = _build_env()
    with test_client:
        yield test_client, factory


def _auth(client: TestClient, email: str, password: str = "SecurePass123!") -> dict[str, str]:
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    token = login.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _closed_trade(
    factory: sessionmaker[Session],
    *,
    organization_id: uuid.UUID = ORG_A,
    user_id: uuid.UUID = USER_A,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    source: JournalTradeSource = JournalTradeSource.MANUAL,
    status: JournalTradeStatus = JournalTradeStatus.CLOSED,
    market_regime: MarketRegime = MarketRegime.TRENDING_UP,
    result: TradeResult = TradeResult.OPEN,
    net_pnl: str | None = None,
    gross_pnl: str | None = None,
    fees: str | None = None,
    funding: str | None = None,
    slippage: str | None = None,
    planned_risk_amount: str | None = None,
    mfe_amount: str | None = None,
    mae_amount: str | None = None,
    available_profit: str | None = None,
    exit_time: datetime | None = datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
    setup_id: uuid.UUID | None = None,
    user_strategy_id: uuid.UUID | None = None,
    strategy_version_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert one journal trade directly (statistics read recorded values only)."""

    def _dec(value: str | None) -> Decimal | None:
        return Decimal(value) if value is not None else None

    with factory() as session:
        row = JournalTrade(
            organization_id=organization_id,
            user_id=user_id,
            source=source,
            status=status,
            symbol=symbol,
            timeframe=timeframe,
            market_regime=market_regime,
            direction=TradeDirection.LONG,
            result=result,
            net_pnl=_dec(net_pnl),
            gross_pnl=_dec(gross_pnl),
            fees=_dec(fees),
            funding=_dec(funding),
            slippage=_dec(slippage),
            planned_risk_amount=_dec(planned_risk_amount),
            mfe_amount=_dec(mfe_amount),
            mae_amount=_dec(mae_amount),
            available_profit=_dec(available_profit),
            entry_time=datetime(2026, 7, 10, 8, 0, tzinfo=UTC),
            exit_time=exit_time,
            setup_id=setup_id,
            user_strategy_id=user_strategy_id,
            strategy_version_id=strategy_version_id,
            tags=[],
            planned_targets=[],
        )
        session.add(row)
        session.commit()
        return row.id


def _stats(
    client: TestClient,
    headers: dict[str, str],
    **params: object,
) -> dict[str, Any]:
    response = client.get("/journal/statistics", headers=headers, params=params)  # type: ignore[arg-type]
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _add_rule_check(
    client: TestClient,
    headers: dict[str, str],
    trade_id: uuid.UUID,
    status: str,
    rule_key: str = "respect-stop",
) -> None:
    response = client.post(
        f"/journal/trades/{trade_id}/rule-checks",
        headers=headers,
        json={"rule_key": rule_key, "status": status},
    )
    assert response.status_code == 201, response.text


# --------------------------------------------------------------------------- #
# Authorization & RBAC
# --------------------------------------------------------------------------- #


def test_statistics_require_auth(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, _ = client
    assert test_client.get("/journal/statistics").status_code == 401


def test_viewer_can_read_statistics(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, _ = client
    headers = _auth(test_client, "stats-viewer@test.example")
    body = _stats(test_client, headers)
    assert body["group_by"] == "overall"
    assert body["overall"]["trade_count"] == 0


# --------------------------------------------------------------------------- #
# Empty samples & edge cases
# --------------------------------------------------------------------------- #


def test_empty_sample_is_explicit(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, _ = client
    headers = _auth(test_client, "stats-a@test.example")
    body = _stats(test_client, headers)
    overall = body["overall"]
    assert overall["trade_count"] == 0
    assert overall["win_rate"] is None
    assert overall["expectancy"] is None
    assert overall["net_pnl_total"] is None
    assert overall["confidence"] == "insufficient"
    assert [w["code"] for w in overall["warnings"]] == ["no_closed_trades"]
    assert body["buckets"] == []
    assert body["total_buckets"] == 0
    assert body["truncated"] is False


def test_non_closed_trades_are_excluded(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    for status in (
        JournalTradeStatus.PLANNED,
        JournalTradeStatus.OPEN,
        JournalTradeStatus.CANCELLED,
    ):
        _closed_trade(factory, status=status, net_pnl="100", result=TradeResult.WIN)
    _closed_trade(factory, net_pnl="50", result=TradeResult.WIN)

    body = _stats(test_client, headers)
    assert body["overall"]["trade_count"] == 1
    assert body["overall"]["wins"] == 1


def test_invalid_date_range_rejected(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, _ = client
    headers = _auth(test_client, "stats-a@test.example")
    response = test_client.get(
        "/journal/statistics",
        headers=headers,
        params={"date_from": "2026-07-20T00:00:00Z", "date_to": "2026-07-01T00:00:00Z"},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Exact calculations
# --------------------------------------------------------------------------- #


def test_overall_metrics_exact_values(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    # Five closed trades with fully specified outcomes:
    #   +300 (R=3), -100 (R=-1), +100 (R=2), breakeven (no risk), -200 (R=-2)
    _closed_trade(
        factory,
        result=TradeResult.WIN,
        net_pnl="300",
        gross_pnl="310",
        fees="6",
        funding="2",
        slippage="2",
        planned_risk_amount="100",
    )
    _closed_trade(
        factory,
        result=TradeResult.LOSS,
        net_pnl="-100",
        gross_pnl="-95",
        fees="3",
        funding="1",
        slippage="1",
        planned_risk_amount="100",
    )
    _closed_trade(factory, result=TradeResult.WIN, net_pnl="100", planned_risk_amount="50")
    _closed_trade(factory, result=TradeResult.BREAKEVEN, net_pnl="0")
    _closed_trade(factory, result=TradeResult.LOSS, net_pnl="-200", planned_risk_amount="100")

    overall = _stats(test_client, headers)["overall"]
    assert overall["trade_count"] == 5
    assert overall["wins"] == 2
    assert overall["losses"] == 2
    assert overall["breakeven"] == 1
    assert overall["win_rate"] == pytest.approx(0.5)
    assert overall["pnl_sample_count"] == 5
    assert Decimal(overall["net_pnl_total"]) == Decimal("100")
    assert Decimal(overall["gross_pnl_total"]) == Decimal("215")
    assert Decimal(overall["expectancy"]) == Decimal("20")
    assert Decimal(overall["average_winner"]) == Decimal("200")
    assert Decimal(overall["average_loser"]) == Decimal("-150")
    assert overall["profit_factor"] == pytest.approx(400 / 300)
    assert overall["r_sample_count"] == 4
    assert overall["average_r"] == pytest.approx(0.5)
    assert overall["cost_sample_count"] == 2
    assert Decimal(overall["fees_total"]) == Decimal("9")
    assert Decimal(overall["funding_total"]) == Decimal("3")
    assert Decimal(overall["slippage_total"]) == Decimal("3")
    assert Decimal(overall["total_costs"]) == Decimal("15")
    assert overall["confidence"] == "low"
    codes = [w["code"] for w in overall["warnings"]]
    assert "low_sample" in codes
    assert "missing_risk" in codes


def test_result_falls_back_to_net_pnl_sign(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    # Closed but result left at "open": deterministic sign fallback applies.
    _closed_trade(factory, result=TradeResult.OPEN, net_pnl="-75")
    _closed_trade(factory, result=TradeResult.OPEN, net_pnl="25")
    _closed_trade(factory, result=TradeResult.OPEN, net_pnl="0")
    _closed_trade(factory, result=TradeResult.OPEN, net_pnl=None)  # stays undecided

    overall = _stats(test_client, headers)["overall"]
    assert overall["trade_count"] == 4
    assert overall["wins"] == 1
    assert overall["losses"] == 1
    assert overall["breakeven"] == 1
    assert overall["win_rate"] == pytest.approx(0.5)
    assert "missing_pnl" in [w["code"] for w in overall["warnings"]]


def test_profit_factor_undefined_without_losses(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    _closed_trade(factory, result=TradeResult.WIN, net_pnl="120")
    _closed_trade(factory, result=TradeResult.WIN, net_pnl="80")

    overall = _stats(test_client, headers)["overall"]
    assert overall["profit_factor"] is None
    assert overall["win_rate"] == pytest.approx(1.0)
    assert "no_losing_trades" in [w["code"] for w in overall["warnings"]]


def test_excursion_and_capture_aggregates_only_from_recorded_values(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    _closed_trade(
        factory,
        result=TradeResult.WIN,
        net_pnl="300",
        mfe_amount="500",
        mae_amount="-50",
        available_profit="500",
    )
    _closed_trade(factory, result=TradeResult.WIN, net_pnl="100", available_profit="400")
    _closed_trade(factory, result=TradeResult.LOSS, net_pnl="-100")  # no excursion data

    overall = _stats(test_client, headers)["overall"]
    assert overall["mfe_sample_count"] == 1
    assert Decimal(overall["average_mfe_amount"]) == Decimal("500")
    assert overall["mae_sample_count"] == 1
    assert Decimal(overall["average_mae_amount"]) == Decimal("-50")
    assert overall["capture_sample_count"] == 2
    assert Decimal(overall["available_profit_total"]) == Decimal("900")
    assert Decimal(overall["realized_on_available_total"]) == Decimal("400")
    # 300/500 = 60% and 100/400 = 25% -> mean 42.5%
    assert overall["average_realized_vs_available_pct"] == pytest.approx(42.5)
    codes = [w["code"] for w in overall["warnings"]]
    assert "partial_excursion_data" in codes
    assert "partial_capture_data" in codes


def test_high_confidence_without_low_sample_warning(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    for i in range(50):
        _closed_trade(
            factory,
            result=TradeResult.WIN if i % 2 == 0 else TradeResult.LOSS,
            net_pnl="10" if i % 2 == 0 else "-5",
        )

    overall = _stats(test_client, headers)["overall"]
    assert overall["trade_count"] == 50
    assert overall["confidence"] == "high"
    assert "low_sample" not in [w["code"] for w in overall["warnings"]]


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #


def _seed_setup(factory: sessionmaker[Session], *, name: str, version: int = 1) -> uuid.UUID:
    with factory() as session:
        setup = SetupDefinition(
            name=name,
            strategy_id=StrategyId.HTF_TREND_PULLBACK,
            category="trend",
            version=version,
        )
        session.add(setup)
        session.commit()
        return setup.id


def _seed_strategy_with_versions(
    factory: sessionmaker[Session],
    *,
    name: str,
    organization_id: uuid.UUID = ORG_A,
    user_id: uuid.UUID = USER_A,
    versions: int = 2,
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    with factory() as session:
        strategy = UserStrategy(
            organization_id=organization_id,
            user_id=user_id,
            name=name,
            setup_type=StrategyId.LIQUIDITY_SWEEP_REVERSAL,
        )
        session.add(strategy)
        session.flush()
        version_ids: list[uuid.UUID] = []
        for number in range(1, versions + 1):
            version = UserStrategyVersion(
                strategy_id=strategy.id, version=number, card={"name": name}
            )
            session.add(version)
            session.flush()
            version_ids.append(version.id)
        session.commit()
        return strategy.id, version_ids


def test_group_by_setup_and_setup_version(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    sweep_v1 = _seed_setup(factory, name="Sweep reversal", version=1)
    sweep_v2 = _seed_setup(factory, name="Sweep reversal", version=2)
    _closed_trade(factory, setup_id=sweep_v1, result=TradeResult.WIN, net_pnl="100")
    _closed_trade(factory, setup_id=sweep_v2, result=TradeResult.LOSS, net_pnl="-40")
    _closed_trade(factory, setup_id=sweep_v2, result=TradeResult.WIN, net_pnl="60")
    _closed_trade(factory, result=TradeResult.WIN, net_pnl="10")  # no setup

    by_setup = _stats(test_client, headers, group_by="setup")
    assert by_setup["total_buckets"] == 2
    merged = next(b for b in by_setup["buckets"] if b["key"] == "Sweep reversal")
    assert merged["metrics"]["trade_count"] == 3
    unassigned = next(b for b in by_setup["buckets"] if b["key"] == "unassigned")
    assert unassigned["metrics"]["trade_count"] == 1

    by_version = _stats(test_client, headers, group_by="setup_version")
    assert by_version["total_buckets"] == 3
    v2 = next(b for b in by_version["buckets"] if b["group_id"] == str(sweep_v2))
    assert v2["label"] == "Sweep reversal v2"
    assert v2["metrics"]["trade_count"] == 2
    assert Decimal(v2["metrics"]["net_pnl_total"]) == Decimal("20")


def test_group_by_strategy_and_strategy_version(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    strategy_id, (v1, v2) = _seed_strategy_with_versions(factory, name="London sweep")
    _closed_trade(
        factory,
        user_strategy_id=strategy_id,
        strategy_version_id=v1,
        result=TradeResult.LOSS,
        net_pnl="-30",
    )
    _closed_trade(
        factory,
        user_strategy_id=strategy_id,
        strategy_version_id=v2,
        result=TradeResult.WIN,
        net_pnl="90",
    )

    by_strategy = _stats(test_client, headers, group_by="strategy")
    assert by_strategy["total_buckets"] == 1
    bucket = by_strategy["buckets"][0]
    assert bucket["label"] == "London sweep"
    assert bucket["group_id"] == str(strategy_id)
    assert bucket["metrics"]["trade_count"] == 2

    by_version = _stats(test_client, headers, group_by="strategy_version")
    assert by_version["total_buckets"] == 2
    v2_bucket = next(b for b in by_version["buckets"] if b["group_id"] == str(v2))
    assert v2_bucket["label"] == "London sweep v2"
    assert v2_bucket["metrics"]["wins"] == 1


def test_group_by_symbol_regime_source_and_pagination(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    for symbol in ("BTCUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"):
        _closed_trade(factory, symbol=symbol, result=TradeResult.WIN, net_pnl="10")
    _closed_trade(
        factory,
        symbol="ETHUSDT",
        market_regime=MarketRegime.RANGING,
        source=JournalTradeSource.IMPORTED,
        result=TradeResult.LOSS,
        net_pnl="-10",
    )

    by_symbol = _stats(test_client, headers, group_by="symbol", limit=2)
    assert by_symbol["total_buckets"] == 3
    assert len(by_symbol["buckets"]) == 2
    # Deterministic order: largest sample first, then label.
    assert by_symbol["buckets"][0]["key"] == "BTCUSDT"
    assert by_symbol["buckets"][1]["key"] == "ETHUSDT"
    page_two = _stats(test_client, headers, group_by="symbol", limit=2, offset=2)
    assert [b["key"] for b in page_two["buckets"]] == ["SOLUSDT"]

    by_regime = _stats(test_client, headers, group_by="market_regime")
    assert {b["key"]: b["metrics"]["trade_count"] for b in by_regime["buckets"]} == {
        "trending_up": 4,
        "ranging": 1,
    }

    by_source = _stats(test_client, headers, group_by="source")
    assert {b["key"]: b["metrics"]["trade_count"] for b in by_source["buckets"]} == {
        "manual": 4,
        "imported": 1,
    }


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #


def test_dimension_filters(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    setup_id = _seed_setup(factory, name="Demand zone")
    _closed_trade(
        factory,
        symbol="BTCUSDT",
        timeframe="1h",
        setup_id=setup_id,
        result=TradeResult.WIN,
        net_pnl="100",
    )
    _closed_trade(factory, symbol="ETHUSDT", timeframe="4h", result=TradeResult.LOSS, net_pnl="-50")

    assert _stats(test_client, headers, symbol="BTCUSDT")["overall"]["trade_count"] == 1
    assert _stats(test_client, headers, timeframe="4h")["overall"]["trade_count"] == 1
    assert _stats(test_client, headers, setup_id=str(setup_id))["overall"]["trade_count"] == 1
    assert _stats(test_client, headers, market_regime="trending_up")["overall"]["trade_count"] == 2


def test_date_range_filtering(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    _closed_trade(
        factory,
        result=TradeResult.WIN,
        net_pnl="100",
        exit_time=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
    )
    _closed_trade(
        factory,
        result=TradeResult.LOSS,
        net_pnl="-50",
        exit_time=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
    )

    june = _stats(
        test_client,
        headers,
        date_from="2026-06-01T00:00:00Z",
        date_to="2026-06-30T23:59:59Z",
    )
    assert june["overall"]["trade_count"] == 1
    assert june["overall"]["wins"] == 1

    july = _stats(test_client, headers, date_from="2026-07-01T00:00:00Z")
    assert july["overall"]["trade_count"] == 1
    assert july["overall"]["losses"] == 1


def test_rule_compliance_classification_filter_and_grouping(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    compliant = _closed_trade(factory, result=TradeResult.WIN, net_pnl="100")
    violated = _closed_trade(factory, result=TradeResult.LOSS, net_pnl="-80")
    partial = _closed_trade(factory, result=TradeResult.WIN, net_pnl="40")
    _closed_trade(factory, result=TradeResult.WIN, net_pnl="10")  # unassessed

    _add_rule_check(test_client, headers, compliant, "followed")
    # Worst assessment wins: followed + violated -> violated.
    _add_rule_check(test_client, headers, violated, "followed", rule_key="wait-for-close")
    _add_rule_check(test_client, headers, violated, "violated")
    _add_rule_check(test_client, headers, partial, "partial")

    grouped = _stats(test_client, headers, group_by="rule_compliance")
    counts = {b["key"]: b["metrics"]["trade_count"] for b in grouped["buckets"]}
    assert counts == {"compliant": 1, "violated": 1, "partial": 1, "unassessed": 1}

    violated_only = _stats(test_client, headers, rule_compliance="violated")["overall"]
    assert violated_only["trade_count"] == 1
    assert Decimal(violated_only["net_pnl_total"]) == Decimal("-80")

    compliant_only = _stats(test_client, headers, rule_compliance="compliant")["overall"]
    assert compliant_only["trade_count"] == 1
    assert Decimal(compliant_only["net_pnl_total"]) == Decimal("100")


def test_execution_actor_classification(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    for source in (
        JournalTradeSource.MANUAL,
        JournalTradeSource.IMPORTED,
        JournalTradeSource.PAPER_EXECUTION,
    ):
        _closed_trade(factory, source=source, result=TradeResult.WIN, net_pnl="10")
    for source in (
        JournalTradeSource.PAPER_VALIDATION,
        JournalTradeSource.BACKTEST,
        JournalTradeSource.SYSTEM,
    ):
        _closed_trade(factory, source=source, result=TradeResult.LOSS, net_pnl="-10")

    grouped = _stats(test_client, headers, group_by="execution_actor")
    counts = {b["key"]: b["metrics"]["trade_count"] for b in grouped["buckets"]}
    assert counts == {"human": 3, "system": 3}

    human = _stats(test_client, headers, execution_actor="human")["overall"]
    assert human["trade_count"] == 3
    assert human["wins"] == 3
    system = _stats(test_client, headers, execution_actor="system")["overall"]
    assert system["trade_count"] == 3
    assert system["losses"] == 3


def test_manual_demo_test_is_a_human_channel(
    client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    _closed_trade(
        factory,
        source=JournalTradeSource.MANUAL_DEMO_TEST,
        result=TradeResult.WIN,
        net_pnl="12",
    )

    grouped = _stats(test_client, headers, group_by="execution_actor")
    counts = {b["key"]: b["metrics"]["trade_count"] for b in grouped["buckets"]}
    assert counts == {"human": 1}
    human = _stats(test_client, headers, execution_actor="human")["overall"]
    assert human["trade_count"] == 1
    assert Decimal(human["net_pnl_total"]) == Decimal("12")


def test_unknown_source_is_excluded_from_compute(
    client: tuple[TestClient, sessionmaker[Session]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import journal_statistics_service as stats_module

    mapped = dict(stats_module._EXECUTION_ACTOR_BY_SOURCE)
    del mapped[JournalTradeSource.MANUAL]
    monkeypatch.setattr(stats_module, "_EXECUTION_ACTOR_BY_SOURCE", mapped)

    test_client, factory = client
    headers = _auth(test_client, "stats-a@test.example")
    _closed_trade(factory, source=JournalTradeSource.MANUAL, result=TradeResult.WIN, net_pnl="50")
    _closed_trade(factory, source=JournalTradeSource.SYSTEM, result=TradeResult.LOSS, net_pnl="-4")

    overall = _stats(test_client, headers)["overall"]
    assert overall["trade_count"] == 1
    assert Decimal(overall["net_pnl_total"]) == Decimal("-4")
    grouped = _stats(test_client, headers, group_by="execution_actor")
    counts = {b["key"]: b["metrics"]["trade_count"] for b in grouped["buckets"]}
    assert counts == {"system": 1}


# --------------------------------------------------------------------------- #
# Tenant isolation & bounded results
# --------------------------------------------------------------------------- #


def test_tenant_isolation(client: tuple[TestClient, sessionmaker[Session]]) -> None:
    test_client, factory = client
    headers_a = _auth(test_client, "stats-a@test.example")
    headers_b = _auth(test_client, "stats-b@test.example")
    _closed_trade(factory, result=TradeResult.WIN, net_pnl="100")
    _closed_trade(
        factory,
        organization_id=ORG_B,
        user_id=USER_B,
        result=TradeResult.LOSS,
        net_pnl="-999",
    )

    overall_a = _stats(test_client, headers_a)["overall"]
    assert overall_a["trade_count"] == 1
    assert Decimal(overall_a["net_pnl_total"]) == Decimal("100")

    overall_b = _stats(test_client, headers_b)["overall"]
    assert overall_b["trade_count"] == 1
    assert Decimal(overall_b["net_pnl_total"]) == Decimal("-999")


def test_result_truncation_is_flagged() -> None:
    test_client, factory = _build_env({"journal_stats_max_rows": 100})
    with test_client:
        headers = _auth(test_client, "stats-a@test.example")
        with factory() as session:
            for index in range(101):
                session.add(
                    JournalTrade(
                        organization_id=ORG_A,
                        user_id=USER_A,
                        source=JournalTradeSource.MANUAL,
                        status=JournalTradeStatus.CLOSED,
                        symbol="BTCUSDT",
                        timeframe="1h",
                        direction=TradeDirection.LONG,
                        result=TradeResult.WIN,
                        net_pnl=Decimal("1"),
                        exit_time=datetime(2026, 7, 1, 0, 0, index % 60, tzinfo=UTC),
                        tags=[],
                        planned_targets=[],
                    )
                )
            session.commit()

        body = _stats(test_client, headers)
        assert body["truncated"] is True
        assert body["max_rows"] == 100
        assert body["overall"]["trade_count"] == 100
        assert "result_truncated" in [w["code"] for w in body["overall"]["warnings"]]
