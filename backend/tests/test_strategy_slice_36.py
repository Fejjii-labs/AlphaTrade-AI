"""Slice 36 — human vs system v3, structured rules, testability."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
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
    StrategyId,
    Timeframe,
    TradeDirection,
)
from app.schemas.structured_rules import EntryRuleBlock, ExitRuleBlock, StructuredRules
from app.security.passwords import hash_password
from app.services.runner_missed_profit_analyzer import (
    RunnerAnalysisInput,
    RunnerAndMissedProfitAnalyzer,
)
from app.services.stop_loss_refusal_analyzer import (
    StopLossAnalysisInput,
    StopLossRefusalAnalyzer,
)
from app.services.strategy_testability_service import (
    validate_structured_rules,
)
from app.services.structured_rule_resolver import resolve_backtest_rules

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000070")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000071")


def _sample_card(**overrides: object) -> dict:
    base = {
        "strategy_name": "Slice36 Test",
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


def _structured_rules() -> dict:
    return StructuredRules(
        primary_timeframe=Timeframe.H4,
        entry_rules=[EntryRuleBlock(trigger_type=EntryTriggerType.EMA_PULLBACK)],
        exit_rules=[
            ExitRuleBlock(rule_type=ExitRuleType.FIXED_STOP, value=Decimal("2")),
            ExitRuleBlock(rule_type=ExitRuleType.TP_MULTIPLE, r_multiple=Decimal("1")),
        ],
        no_trade_rules=[],
    ).model_dump(mode="json")


@pytest.fixture
def slice36_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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
        jwt_secret="slice36-test-secret-key-min",
        rate_limit_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
    )
    with factory() as session:
        org = Organization(id=ORG_ID, name="Slice36 Org")
        user = User(
            id=USER_ID,
            email="slice36@test.example",
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
            json={"email": "slice36@test.example", "password": "TestPassword123!"},
        )
        assert login.status_code == 200
        token = login.json()["tokens"]["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client, factory
    app.dependency_overrides.clear()


def test_early_exit_analyzer_detects_early_close() -> None:
    analyzer = RunnerAndMissedProfitAnalyzer()
    result = analyzer.analyze(
        RunnerAnalysisInput(
            entry_price=Decimal("100"),
            exit_price=Decimal("105"),
            exit_time=datetime.now(UTC),
            direction=TradeDirection.LONG,
            tp_plan_prices=[Decimal("110"), Decimal("115")],
            runner_enabled=True,
        )
    )
    assert result.early_exit_flag is True


def test_runner_analyzer_conservative_without_candles() -> None:
    analyzer = RunnerAndMissedProfitAnalyzer()
    result = analyzer.analyze(
        RunnerAnalysisInput(
            entry_price=Decimal("100"),
            exit_price=Decimal("108"),
            exit_time=None,
            direction=TradeDirection.LONG,
            tp_plan_prices=[Decimal("110")],
            runner_enabled=True,
        )
    )
    assert (
        any("candle" in note.lower() or "tp" in note.lower() for note in result.limitations)
        or result.missed_profit_estimate is not None
    )


def test_stop_loss_refusal_actual_above_planned() -> None:
    analyzer = StopLossRefusalAnalyzer()
    result = analyzer.analyze(
        StopLossAnalysisInput(
            planned_stop=Decimal("58000"),
            actual_stop=Decimal("58000"),
            planned_loss=Decimal("200"),
            actual_loss=Decimal("350"),
            entry_price=Decimal("60000"),
            exit_price=Decimal("56500"),
            direction=TradeDirection.LONG,
            loss_acceptance_status=None,
            stop_was_placed=True,
            stop_moved_away=False,
            held_for_breakeven=False,
            exit_after_invalidation=False,
        )
    )
    assert result.stop_violation_flag is True
    assert result.avoidable_loss_estimate == Decimal("150.00")


def test_structured_rule_validation() -> None:
    rules = StructuredRules(
        primary_timeframe=Timeframe.H4,
        entry_rules=[EntryRuleBlock(trigger_type=EntryTriggerType.BREAKOUT)],
        exit_rules=[ExitRuleBlock(rule_type=ExitRuleType.FIXED_STOP, value=Decimal("2"))],
        no_trade_rules=[],
    )
    valid, errors, _ = validate_structured_rules(rules)
    assert valid is True
    assert not errors


def test_strategy_testability_score(slice36_client: tuple[TestClient, sessionmaker]) -> None:
    client, _ = slice36_client
    create = client.post(
        "/strategies",
        json={
            "name": "Testability Strat",
            "setup_type": "htf_trend_pullback",
            "card": _sample_card(),
        },
    )
    assert create.status_code == 200
    sid = create.json()["id"]
    score = client.get(f"/strategies/{sid}/testability")
    assert score.status_code == 200
    body = score.json()
    assert body["score"] < 70
    assert any(m["field_key"] == "structured_rules" for m in body["missing_fields"])

    patch = client.patch(
        f"/strategies/{sid}/structured-rules",
        json=_structured_rules(),
    )
    assert patch.status_code == 200
    score2 = client.get(f"/strategies/{sid}/testability")
    assert score2.json()["score"] >= 70
    assert score2.json()["ready_for_backtest"] is True


def test_backtest_prefers_structured_rules() -> None:
    from app.schemas.strategy_library import StrategyCard

    card = StrategyCard.model_validate(_sample_card(entry_conditions=["vague idea only"]))
    structured = StructuredRules(
        primary_timeframe=Timeframe.H4,
        entry_rules=[EntryRuleBlock(trigger_type=EntryTriggerType.EMA_PULLBACK)],
        exit_rules=[
            ExitRuleBlock(rule_type=ExitRuleType.FIXED_STOP, value=Decimal("2")),
            ExitRuleBlock(rule_type=ExitRuleType.TP_MULTIPLE, r_multiple=Decimal("1")),
        ],
        no_trade_rules=[],
    )
    resolved = resolve_backtest_rules(card, StrategyId.MANUAL_REVIEW, structured)
    assert resolved.engine_source.value == "structured"
    assert resolved.rules.machine_readable is True

    vague = resolve_backtest_rules(
        StrategyCard.model_validate(
            _sample_card(
                entry_conditions=["maybe enter"],
                stop_loss=["unclear level"],
                take_profit_plan=["somewhere up"],
                confirmation_conditions=[],
            )
        ),
        StrategyId.MANUAL_REVIEW,
        None,
    )
    assert vague.engine_source.value == "unsupported"


def test_agent_routes_early_exit_question(slice36_client: tuple[TestClient, sessionmaker]) -> None:
    assert classify_strategy_workflow("Did I exit too early?") == Intent.EARLY_EXIT_QUERY


def test_agent_routes_strategy_testability(slice36_client: tuple[TestClient, sessionmaker]) -> None:
    assert classify_strategy_workflow("Is this strategy testable?") == Intent.STRATEGY_TESTABILITY


def test_no_real_trading_path_in_slice36_routes(
    slice36_client: tuple[TestClient, sessionmaker],
) -> None:
    _client, _ = slice36_client
    from app.main import create_app

    app = create_app(
        Settings(
            execution_mode="paper",
            enable_real_trading=False,
            log_json=False,
            provider_mode="mock",
            market_data_provider="mock",
        )
    )
    paths = [getattr(r, "path", "") for r in app.routes]
    assert not any("live-order" in p or "real-execution" in p for p in paths)
