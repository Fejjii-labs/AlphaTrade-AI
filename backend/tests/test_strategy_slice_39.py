"""Slice 39 — paper validation runtime loop and paper bot v1."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
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
from app.schemas.agent import Intent
from app.schemas.common import (
    EntryTriggerType,
    ExitRuleType,
    MembershipRole,
    Timeframe,
)
from app.schemas.paper_validation import PaperValidationMetrics
from app.schemas.structured_rules import EntryRuleBlock, ExitRuleBlock, StructuredRules
from app.security.passwords import hash_password
from app.services.paper_bot_engine import PaperBotEngine
from app.services.paper_validation_promotion import compute_max_drawdown, evaluate_paper_promotion

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000100")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000101")


def _sample_card(**overrides: object) -> dict:
    base = {
        "strategy_name": "Slice39 Test",
        "market_type": "crypto_perp",
        "asset_universe": ["BTCUSDT"],
        "timeframes": ["15m"],
        "entry_conditions": ["Pullback to EMA cluster"],
        "confirmation_conditions": ["RSI reset above 40"],
        "invalidation": ["Close below swing low"],
        "stop_loss": ["2% below entry"],
        "take_profit_plan": ["TP1 at 1R"],
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


def _structured_rules() -> dict:
    return StructuredRules(
        primary_timeframe=Timeframe.M15,
        entry_rules=[EntryRuleBlock(trigger_type=EntryTriggerType.EMA_PULLBACK)],
        exit_rules=[
            ExitRuleBlock(rule_type=ExitRuleType.FIXED_STOP, value=Decimal("2")),
            ExitRuleBlock(rule_type=ExitRuleType.TP_MULTIPLE, r_multiple=Decimal("1")),
        ],
        no_trade_rules=[],
    ).model_dump(mode="json")


@pytest.fixture
def slice39_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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
        jwt_secret="slice39-test-secret-key-min",
        rate_limit_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
    )
    with factory() as session:
        org = Organization(id=ORG_ID, name="Slice39 Org")
        user = User(
            id=USER_ID,
            email="slice39@test.example",
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
            json={"email": "slice39@test.example", "password": "TestPassword123!"},
        )
        token = login.json()["tokens"]["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client, factory
    app.dependency_overrides.clear()


def _create_strategy(client: TestClient) -> str:
    resp = client.post(
        "/strategies",
        json={
            "name": "Slice39 Strategy",
            "setup_type": "htf_trend_pullback",
            "card": _sample_card(),
        },
    )
    assert resp.status_code == 200, resp.text
    strategy_id = resp.json()["id"]
    client.patch(
        f"/strategies/{strategy_id}/structured-rules",
        json=_structured_rules(),
    )
    client.post(
        f"/strategies/{strategy_id}/backtests",
        json={
            "assumptions": {
                "symbol": "BTCUSDT",
                "timeframe": "15m",
                "exchange": "mock",
                "initial_capital": "10000",
                "fees_bps": 10,
                "slippage_bps": 5,
                "risk_per_trade_pct": 1,
            }
        },
    )
    return strategy_id


def test_no_trade_filters_block(slice39_client: tuple[TestClient, sessionmaker]) -> None:
    engine = PaperBotEngine()
    from app.schemas.common import TradeDirection
    from app.services.strategy_rule_adapter import ParsedStrategyRules

    rules = ParsedStrategyRules(
        machine_readable=True,
        limitation=None,
        direction=TradeDirection.LONG,
        entry_mode="pullback_ema",
        stop_pct=Decimal("0.02"),
        tp_r_multiples=(Decimal("1"),),
        use_runner=False,
        matched_tokens=(),
    )
    blocked = engine.evaluate_no_trade_filters(
        rules,
        no_trade_rules=["Skip if funding extreme"],
        funding_rate=Decimal("0.002"),
    )
    assert blocked


def test_max_drawdown_calculated() -> None:
    dd = compute_max_drawdown(
        [Decimal("10000"), Decimal("10500"), Decimal("9800"), Decimal("10200")]
    )
    assert dd > 0


def test_paper_promotion_conservative() -> None:
    metrics = PaperValidationMetrics(
        paper_trades_count=5,
        win_rate=0.5,
        net_pnl=Decimal("100"),
        gross_pnl=Decimal("120"),
        profit_factor=1.2,
        expectancy=Decimal("20"),
        max_drawdown_pct=10.0,
    )
    decision = evaluate_paper_promotion(
        metrics=metrics,
        paper_eligible=True,
        has_critical_lesson_blockers=False,
        severe_overtrading=False,
        min_runtime_days_met=True,
    )
    assert decision.recommendation.value == "insufficient_data"


def test_agent_paper_validation_intent() -> None:
    assert classify_strategy_workflow("Start paper validation for this strategy") == (
        Intent.PAPER_VALIDATION_START
    )
    assert classify_strategy_workflow("Scan this strategy now") == Intent.PAPER_VALIDATION_SCAN
    assert classify_strategy_workflow("What paper signals were found?") == (
        Intent.PAPER_VALIDATION_QUERY
    )


def test_no_real_trading_path(slice39_client: tuple[TestClient, sessionmaker]) -> None:
    client, _ = slice39_client
    health = client.get("/health").json()
    assert health["real_trading_enabled"] is False
    assert health["execution_mode"] == "paper"


def test_fees_slippage_in_engine() -> None:
    from datetime import UTC, datetime

    engine = PaperBotEngine()
    from app.schemas.common import TradeDirection
    from app.services.strategy_rule_adapter import ParsedStrategyRules

    rules = ParsedStrategyRules(
        machine_readable=True,
        limitation=None,
        direction=TradeDirection.LONG,
        entry_mode="pullback_ema",
        stop_pct=Decimal("0.02"),
        tp_r_multiples=(Decimal("1"),),
        use_runner=False,
        matched_tokens=(),
    )
    state = engine.open_trade_state(
        direction=TradeDirection.LONG,
        entry_time=datetime.now(UTC),
        entry_price=Decimal("100"),
        stop_loss=Decimal("98"),
        size=Decimal("1"),
        rules=rules,
        fee_rate=Decimal("0.001"),
        slip_rate=Decimal("0.001"),
    )
    bar = HistoricalCandle(
        symbol="BTCUSDT",
        exchange="mock",
        timeframe="15m",
        open_time=datetime.now(UTC),
        close_time=datetime.now(UTC),
        open=Decimal("99"),
        high=Decimal("101"),
        low=Decimal("97"),
        close=Decimal("98"),
        volume=Decimal("1"),
        source="mock",
    )
    close = engine.monitor_bar(
        state,
        bar,
        fee_rate=Decimal("0.001"),
        slip_rate=Decimal("0.001"),
        timeout_bars=100,
    )
    assert close is not None
    assert close.fees > 0
    assert close.slippage > 0
