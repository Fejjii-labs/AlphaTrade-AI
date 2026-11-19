"""Slice 34 — agent routing, backtest foundation, workflow UX."""

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
    LossAcceptanceStatus,
    MembershipRole,
    ProposalStatus,
    RiskSeverity,
    StrategyId,
    Timeframe,
    TradeDirection,
)
from app.schemas.position_sizing import LossAcceptanceRequest
from app.schemas.proposal import ExitCriteria, TakeProfitLevel, TradeProposal
from app.security.passwords import hash_password
from app.services.agent_service import AgentInvokeContext, build_agent_service
from app.services.execution_eligibility import paper_execution_eligibility
from app.services.loss_acceptance_service import LossAcceptanceService

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000050")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000051")


def _sample_card(**overrides: object) -> dict:
    base = {
        "strategy_name": "HTF Pullback v1",
        "market_type": "crypto_perp",
        "asset_universe": ["BTCUSDT"],
        "timeframes": ["4h", "1h"],
        "entry_conditions": ["Pullback to EMA cluster"],
        "confirmation_conditions": ["RSI reset above 40"],
        "invalidation": ["Close below swing low"],
        "stop_loss": ["Below invalidation swing"],
        "take_profit_plan": ["TP1 at prior high"],
        "runner_plan": ["Trail after TP1"],
        "position_sizing": ["Max 1% account risk"],
        "add_rules": ["No adds until TP1"],
        "no_trade_rules": ["Skip if funding extreme"],
        "backtest_rules": ["Placeholder — not run"],
        "success_criteria": ["Win rate > 45% in paper"],
        "validation_status": "draft",
    }
    base.update(overrides)
    return base


@pytest.fixture
def slice34_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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
        jwt_secret="slice34-test-secret-key-min",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
    )
    with factory() as session:
        org = Organization(id=ORG_ID, name="Slice34 Org")
        user = User(
            id=USER_ID,
            email="slice34@test.example",
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
            json={"email": "slice34@test.example", "password": "TestPassword123!"},
        )
        assert login.status_code == 200
        token = login.json()["tokens"]["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client, factory
    app.dependency_overrides.clear()


def _agent_with_session(factory: sessionmaker[Session]) -> object:
    settings = Settings(
        execution_mode="paper",
        enable_real_trading=False,
        log_json=False,
        provider_mode="mock",
        market_data_provider="mock",
    )
    with factory() as session:
        return build_agent_service(settings=settings, session=session), session


def test_strategy_intent_detection() -> None:
    card_msg = "Build me a strategy card for this idea"
    assert classify_strategy_workflow(card_msg) == Intent.STRATEGY_CARD
    assert classify_strategy_workflow("Analyze BTC long using my strategy") == Intent.PRE_TRADE
    size_msg = "Calculate position size for this setup"
    assert classify_strategy_workflow(size_msg) == Intent.POSITION_SIZE
    inv_msg = "What is my invalidation and stop loss?"
    assert classify_strategy_workflow(inv_msg) == Intent.INVALIDATION_QUERY
    assert classify_strategy_workflow("Is this loss acceptable?") == Intent.LOSS_ACCEPTANCE
    hvs_msg = "Compare my trade to the system plan"
    assert classify_strategy_workflow(hvs_msg) == Intent.HUMAN_VS_SYSTEM
    levels_msg = "What manual levels do I have for BTC?"
    assert classify_strategy_workflow(levels_msg) == Intent.MANUAL_LEVELS
    status_msg = "Which of my strategies are validated?"
    assert classify_strategy_workflow(status_msg) == Intent.STRATEGY_STATUS
    bt_msg = "What strategy needs backtesting next?"
    assert classify_strategy_workflow(bt_msg) == Intent.BACKTEST_QUEUE


def test_agent_routes_strategy_card(slice34_client: tuple[TestClient, sessionmaker]) -> None:
    _, factory = slice34_client
    service, _ = _agent_with_session(factory)
    response = service.run(
        "Build me a strategy card for HTF pullback idea",
        AgentInvokeContext(
            request_id="slice34-strategy-card",
            user_id=USER_ID,
            organization_id=ORG_ID,
        ),
    )
    tool = next(o for o in response.tool_outputs if o.tool_name == "strategy_library_tool")
    assert tool.success


def test_agent_routes_pretrade(slice34_client: tuple[TestClient, sessionmaker]) -> None:
    _, factory = slice34_client
    service, _ = _agent_with_session(factory)
    response = service.run(
        "Analyze BTC long using my strategy",
        AgentInvokeContext(
            request_id="slice34-pretrade",
            user_id=USER_ID,
            organization_id=ORG_ID,
        ),
        symbol="BTCUSDT",
    )
    tool = next(o for o in response.tool_outputs if o.tool_name == "pretrade_analysis_tool")
    assert tool.success


def test_agent_routes_position_sizing(slice34_client: tuple[TestClient, sessionmaker]) -> None:
    _, factory = slice34_client
    service, _ = _agent_with_session(factory)
    response = service.run(
        "Calculate position size for this setup",
        AgentInvokeContext(
            request_id="slice34-sizing",
            user_id=USER_ID,
            organization_id=ORG_ID,
        ),
    )
    assert any(o.tool_name == "position_sizing_tool" for o in response.tool_outputs)


def test_agent_reports_parked_trade_comparison(
    slice34_client: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = slice34_client
    journal = client.post(
        "/journal/entries",
        json={
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "direction": "long",
            "entry_rationale": "Test",
            "lessons": "Plan followed",
            "emotions": ["calm"],
        },
    )
    trade_id = journal.json()["id"]
    service, _ = _agent_with_session(factory)
    response = service.run(
        f"Compare my trade to the system plan {trade_id}",
        AgentInvokeContext(
            request_id="slice34-hvs",
            user_id=USER_ID,
            organization_id=ORG_ID,
        ),
    )
    assert "Trade comparison is available in the journal." in response.reply
    assert all(output.tool_name == "rag_retriever" for output in response.tool_outputs)
    assert response.narrative_meta is None


def test_strategy_update_and_version(slice34_client: tuple[TestClient, sessionmaker]) -> None:
    client, _ = slice34_client
    create = client.post(
        "/strategies",
        json={
            "name": "Editable",
            "setup_type": "htf_trend_pullback",
            "card": _sample_card(),
        },
    )
    strategy_id = create.json()["id"]
    updated = client.patch(
        f"/strategies/{strategy_id}",
        json={"name": "Editable v2", "card": _sample_card(strategy_name="Updated")},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Editable v2"

    version = client.post(
        f"/strategies/{strategy_id}/versions",
        json={"card": _sample_card(strategy_name="Version 3", validation_status="in_review")},
    )
    assert version.status_code == 200
    assert version.json()["version"] >= 2


def test_backtest_run_create_and_list(slice34_client: tuple[TestClient, sessionmaker]) -> None:
    client, _ = slice34_client
    create = client.post(
        "/strategies",
        json={
            "name": "Backtest Me",
            "setup_type": "htf_trend_pullback",
            "card": _sample_card(validation_status="in_review"),
        },
    )
    strategy_id = create.json()["id"]
    run = client.post(f"/strategies/{strategy_id}/backtests", json={})
    assert run.status_code == 200
    assert run.json()["status"] in {"completed", "queued"}
    assert run.json().get("result") is not None

    listing = client.get(f"/strategies/{strategy_id}/backtests")
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1

    detail = client.get(f"/backtests/{run.json()['id']}")
    assert detail.status_code == 200


def test_loss_acceptance_blocks_paper_execution() -> None:
    proposal = TradeProposal(
        id=uuid.uuid4(),
        organization_id=ORG_ID,
        user_id=USER_ID,
        strategy_id=StrategyId.HTF_TREND_PULLBACK,
        symbol="BTCUSDT",
        timeframe=Timeframe.H4,
        direction=TradeDirection.LONG,
        entry_price=Decimal("60000"),
        position_size=Decimal("0.01"),
        leverage=Decimal("3"),
        exit=ExitCriteria(
            invalidation="test",
            stop_loss=Decimal("58000"),
            take_profits=[TakeProfitLevel(price=Decimal("62000"), size_fraction=0.5)],
        ),
        confidence=0.7,
        risk_level=RiskSeverity.MEDIUM,
        rationale="test",
        status=ProposalStatus.PENDING_APPROVAL,
        loss_acceptance_required=True,
        loss_acceptance_status=LossAcceptanceStatus.PENDING,
        created_at=datetime.now(UTC),
    )
    allowed, reason = paper_execution_eligibility(proposal, None)
    assert allowed is False
    assert "loss acceptance" in reason.lower()

    service = LossAcceptanceService()
    rejected = service.evaluate(
        planned_loss_amount=Decimal("100"),
        request=LossAcceptanceRequest(planned_loss_amount=Decimal("100"), accepted=False),
    )
    assert rejected.can_execute_paper is False


def test_no_real_trading_path(settings: Settings) -> None:
    assert settings.enable_real_trading is False
    assert settings.execution_mode.value == "paper"
