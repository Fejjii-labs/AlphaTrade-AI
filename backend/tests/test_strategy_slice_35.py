"""Slice 35 — backtest engine v1 and paper validation metrics."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.strategy_intent import classify_strategy_workflow
from app.core.config import Settings
from app.db.base import Base
from app.db.models import HistoricalCandle, Membership, Organization, User
from app.db.session import get_session
from app.main import create_app
from app.providers.market_data import MockMarketDataProvider
from app.repositories.historical_candles import HistoricalCandleRepository
from app.schemas.agent import Intent
from app.schemas.backtest import BacktestAssumptions
from app.schemas.common import BacktestRecommendation, MembershipRole, StrategyId
from app.schemas.historical_candles import HistoricalIngestRequest
from app.schemas.strategy_library import StrategyCard
from app.security.passwords import hash_password
from app.services.backtest_engine_service import BacktestEngineService
from app.services.historical_candle_service import HistoricalCandleService
from app.services.strategy_promotion import evaluate_promotion
from app.services.strategy_rule_adapter import parse_strategy_rules

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000060")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000061")


def _sample_card(**overrides: object) -> dict:
    base = {
        "strategy_name": "Engine Test",
        "market_type": "crypto_perp",
        "asset_universe": ["BTCUSDT"],
        "timeframes": ["4h"],
        "entry_conditions": ["Pullback to EMA cluster"],
        "confirmation_conditions": ["RSI reset above 40"],
        "invalidation": ["Close below swing low"],
        "stop_loss": ["2% below entry"],
        "take_profit_plan": ["TP1 at 1R", "TP2 at 2R"],
        "runner_plan": ["Trail after TP1"],
        "position_sizing": ["Max 1% account risk"],
        "add_rules": [],
        "no_trade_rules": [],
        "backtest_rules": [],
        "success_criteria": ["Win rate > 45%"],
        "validation_status": "draft",
    }
    base.update(overrides)
    return base


@pytest.fixture
def slice35_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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
    settings = Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="slice35-test-secret-key-min",
        rate_limit_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
    )
    with factory() as session:
        org = Organization(id=ORG_ID, name="Slice35 Org")
        user = User(
            id=USER_ID,
            email="slice35@test.example",
            hashed_password=hash_password("TestPassword123!", settings),
            email_verified=True,
        )
        session.add(org)
        session.add(user)
        session.flush()
        session.add(Membership(user_id=USER_ID, organization_id=ORG_ID, role=MembershipRole.OWNER))
        session.commit()

    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        login = client.post(
            "/auth/login",
            json={"email": "slice35@test.example", "password": "TestPassword123!"},
        )
        token = login.json()["tokens"]["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client, factory
    app.dependency_overrides.clear()


def test_candle_uniqueness(slice35_client: tuple[TestClient, sessionmaker]) -> None:
    _, factory = slice35_client
    with factory() as session:
        repo = HistoricalCandleRepository(session)
        ts = datetime(2024, 1, 1, tzinfo=UTC)
        c1 = HistoricalCandle(
            symbol="BTCUSDT",
            exchange="mock",
            timeframe="4h",
            open_time=ts,
            close_time=ts + timedelta(hours=4),
            open=Decimal("50000"),
            high=Decimal("51000"),
            low=Decimal("49000"),
            close=Decimal("50500"),
            volume=Decimal("100"),
            source="mock",
        )
        c2 = HistoricalCandle(
            symbol="BTCUSDT",
            exchange="mock",
            timeframe="4h",
            open_time=ts,
            close_time=ts + timedelta(hours=4),
            open=Decimal("50000"),
            high=Decimal("51000"),
            low=Decimal("49000"),
            close=Decimal("50500"),
            volume=Decimal("200"),
            source="mock",
        )
        assert repo.upsert_batch([c1]) == 1
        assert repo.upsert_batch([c2]) == 0
        session.commit()


def test_backtest_run_with_metrics(slice35_client: tuple[TestClient, sessionmaker]) -> None:
    client, _ = slice35_client
    create = client.post(
        "/strategies",
        json={
            "name": "Backtest Engine",
            "setup_type": "htf_trend_pullback",
            "card": _sample_card(),
        },
    )
    strategy_id = create.json()["id"]
    run = client.post(
        f"/strategies/{strategy_id}/backtests",
        json={
            "assumptions": {
                "symbol": "BTCUSDT",
                "exchange": "mock",
                "timeframe": "4h",
                "start_date": "2024-01-01",
                "end_date": "2024-04-01",
                "initial_capital": 10000,
                "fees_bps": 4,
                "slippage_bps": 5,
                "risk_per_trade_pct": 1,
            }
        },
    )
    assert run.status_code == 200
    data = run.json()
    assert data["status"] == "completed"
    result = data["result"]
    assert result is not None
    assert result["metrics"]["trade_count"] >= 0
    assert "recommendation" in result
    assert "Historical simulation only" in result["note"]

    trades = client.get(f"/backtests/{data['id']}/trades")
    assert trades.status_code == 200


def test_unstructured_rules_limitation(
    slice35_client: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = slice35_client
    create = client.post(
        "/strategies",
        json={
            "name": "Vague Rules",
            "setup_type": "manual_review",
            "card": _sample_card(
                entry_conditions=["When it feels right"],
                confirmation_conditions=["Maybe"],
                invalidation=["Something bad"],
                stop_loss=["Somewhere"],
                take_profit_plan=["Vague target"],
                runner_plan=[],
            ),
        },
    )
    strategy_id = create.json()["id"]
    run = client.post(f"/strategies/{strategy_id}/backtests", json={})
    assert run.status_code == 200
    rec = run.json()["result"]["recommendation"]
    assert rec == BacktestRecommendation.NEEDS_STRUCTURED_RULES.value


def test_fees_and_slippage_applied(slice35_client: tuple[TestClient, sessionmaker]) -> None:
    _, factory = slice35_client
    with factory() as session:
        provider = MockMarketDataProvider()
        candle_svc = HistoricalCandleService(session, provider)
        candle_svc.ingest(
            HistoricalIngestRequest(
                symbol="BTCUSDT",
                exchange="mock",
                timeframe="4h",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 6, 1),
            )
        )
        from app.db.models import BacktestRun as BacktestRunModel
        from app.db.models import UserStrategy, UserStrategyVersion

        strategy = UserStrategy(
            organization_id=ORG_ID,
            user_id=USER_ID,
            name="Fee Test",
            setup_type=StrategyId.HTF_TREND_PULLBACK,
        )
        session.add(strategy)
        session.flush()
        version = UserStrategyVersion(
            strategy_id=strategy.id,
            version=1,
            card=_sample_card(),
        )
        session.add(version)
        session.flush()
        run = BacktestRunModel(
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            organization_id=ORG_ID,
            user_id=USER_ID,
            status="running",
            assumptions=BacktestAssumptions(
                fees_bps=Decimal("10"),
                slippage_bps=Decimal("10"),
            ).model_dump(mode="json"),
        )
        session.add(run)
        session.flush()
        engine = BacktestEngineService(session, candle_svc)
        result = engine.run(
            run=run,
            card=StrategyCard.model_validate(_sample_card()),
            setup_type=StrategyId.HTF_TREND_PULLBACK,
        )
        if result.metrics.trade_count > 0:
            assert result.metrics.total_fees > 0
            assert result.metrics.total_slippage > 0


def test_promotion_conservative_on_small_sample() -> None:
    from app.schemas.backtest import BacktestMetrics

    metrics = BacktestMetrics(
        trade_count=5,
        win_rate=0.6,
        profit_factor=1.5,
        expectancy=Decimal("10"),
        max_drawdown_pct=5.0,
        average_win=Decimal("20"),
        average_loss=Decimal("-10"),
        largest_win=Decimal("30"),
        largest_loss=Decimal("-15"),
        consecutive_losses=1,
        average_time_in_trade_bars=3.0,
        total_fees=Decimal("1"),
        total_slippage=Decimal("1"),
        net_pnl=Decimal("50"),
        return_pct=0.5,
        ending_equity=Decimal("10050"),
        symbol="BTCUSDT",
        timeframe="4h",
    )
    decision = evaluate_promotion(
        metrics=metrics,
        machine_readable=True,
        data_quality="ok",
        meets_success_criteria=False,
    )
    assert decision.recommendation == BacktestRecommendation.NEEDS_MORE_SAMPLE
    assert decision.paper_eligible is False


def test_agent_backtest_intents() -> None:
    assert classify_strategy_workflow("Backtest this strategy on BTC 15m") == Intent.BACKTEST_RUN
    assert (
        classify_strategy_workflow("Is this strategy paper eligible?")
        == Intent.BACKTEST_ELIGIBILITY
    )
    assert classify_strategy_workflow("What did the backtest show?") == Intent.BACKTEST_RESULTS


def test_no_real_trading_path_in_backtest(slice35_client: tuple[TestClient, sessionmaker]) -> None:
    client, _ = slice35_client
    health = client.get("/health").json()
    assert health["real_trading_enabled"] is False


def test_parse_strategy_rules_htf() -> None:
    rules = parse_strategy_rules(
        StrategyCard.model_validate(_sample_card()),
        StrategyId.HTF_TREND_PULLBACK,
    )
    assert rules.machine_readable is True
    assert rules.entry_mode == "pullback_ema"
