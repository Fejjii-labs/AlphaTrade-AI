"""Slice 40 — paper validation scheduler, observability, and alerts."""

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
from app.db.models import Membership, Organization, User
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
from app.services.paper_validation_promotion import evaluate_paper_promotion

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000200")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000201")


def _sample_card(**overrides: object) -> dict:
    base = {
        "strategy_name": "Slice40 Test",
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
def slice40_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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
        enable_paper_scheduler=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="slice40-test-secret-key-min",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
    )
    with factory() as session:
        org = Organization(id=ORG_ID, name="Slice40 Org")
        user = User(
            id=USER_ID,
            email="slice40@test.example",
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
            json={"email": "slice40@test.example", "password": "TestPassword123!"},
        )
        token = login.json()["tokens"]["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client, factory
    app.dependency_overrides.clear()


def _create_strategy(client: TestClient) -> str:
    resp = client.post(
        "/strategies",
        json={
            "name": "Slice40 Strategy",
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


def _start_run(client: TestClient, strategy_id: str) -> str:
    resp = client.post(
        f"/strategies/{strategy_id}/paper-validation/start",
        json={"runtime_mode": "scan_only"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_promotion_requires_enough_paper_sample() -> None:
    metrics = PaperValidationMetrics(
        paper_trades_count=3,
        win_rate=0.5,
        net_pnl=Decimal("10"),
        profit_factor=1.2,
        expectancy=Decimal("3"),
        max_drawdown_pct=5.0,
    )
    decision = evaluate_paper_promotion(
        metrics=metrics,
        paper_eligible=True,
        has_critical_lesson_blockers=False,
        severe_overtrading=False,
        min_runtime_days_met=True,
        runtime_windows_count=0,
    )
    assert decision.recommendation.value == "insufficient_data"


def test_stale_data_blocks_promotion() -> None:
    metrics = PaperValidationMetrics(
        paper_trades_count=12,
        win_rate=0.5,
        net_pnl=Decimal("50"),
        profit_factor=1.5,
        expectancy=Decimal("4"),
        max_drawdown_pct=8.0,
        stop_respected_count=10,
    )
    decision = evaluate_paper_promotion(
        metrics=metrics,
        paper_eligible=True,
        has_critical_lesson_blockers=False,
        severe_overtrading=False,
        min_runtime_days_met=True,
        runtime_windows_count=3,
        data_stale=True,
    )
    assert "stale" in decision.blockers[0].lower()


def test_agent_routes_scheduler_and_alerts_questions() -> None:
    assert (
        classify_strategy_workflow("Is the paper scheduler running?")
        is Intent.PAPER_SCHEDULER_QUERY
    )
    assert classify_strategy_workflow("What alerts do I have?") is Intent.PAPER_ALERTS_QUERY
    assert (
        classify_strategy_workflow("Why was this strategy skipped?")
        is Intent.PAPER_VALIDATION_QUERY
    )
