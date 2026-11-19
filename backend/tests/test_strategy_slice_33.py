"""Slice 33 — strategy library, pre-trade, sizing, manual levels, human vs system."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Membership, Organization, User
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import MembershipRole
from app.schemas.position_sizing import PositionSizingRequest
from app.schemas.strategy_library import StrategyCard
from app.security.passwords import hash_password
from app.services.loss_acceptance_service import LossAcceptanceService
from app.services.position_sizing_service import PositionSizingService

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000040")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000041")


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
        "take_profit_plan": ["TP1 at prior high", "TP2 at extension"],
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
def slice33_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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
        jwt_secret="slice33-test-secret-key-min",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
    )
    with factory() as session:
        org = Organization(id=ORG_ID, name="Slice33 Org")
        user = User(
            id=USER_ID,
            email="slice33@test.example",
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
            json={"email": "slice33@test.example", "password": "TestPassword123!"},
        )
        assert login.status_code == 200
        token = login.json()["tokens"]["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client, factory
    app.dependency_overrides.clear()


def test_strategy_card_validation() -> None:
    with pytest.raises(ValueError, match="entry_conditions"):
        StrategyCard.model_validate(_sample_card(entry_conditions=[]))


def test_strategy_crud_and_versions(slice33_client: tuple[TestClient, sessionmaker]) -> None:
    client, _ = slice33_client
    create = client.post(
        "/strategies",
        json={
            "name": "My Pullback",
            "setup_type": "htf_trend_pullback",
            "card": _sample_card(),
        },
    )
    assert create.status_code == 200, create.text
    strategy_id = create.json()["id"]
    assert create.json()["latest_card"]["strategy_name"] == "HTF Pullback v1"

    listing = client.get("/strategies")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    detail = client.get(f"/strategies/{strategy_id}")
    assert detail.status_code == 200

    version = client.post(
        f"/strategies/{strategy_id}/versions",
        json={"card": _sample_card(strategy_name="HTF Pullback v2", validation_status="in_review")},
    )
    assert version.status_code == 200
    assert version.json()["version"] == 2

    versions = client.get(f"/strategies/{strategy_id}/versions")
    assert versions.status_code == 200
    assert versions.json()["total"] == 2


def test_pretrade_analysis_schema(slice33_client: tuple[TestClient, sessionmaker]) -> None:
    client, _ = slice33_client
    response = client.post(
        "/pretrade/analyze",
        json={
            "symbol": "BTCUSDT",
            "exchange": "mock",
            "direction": "long",
            "account_size": "10000",
            "max_risk_per_trade": "1",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "bullish_factors" in body
    assert "final_recommendation" in body
    assert body["position_size"] is not None
    assert 0 <= body["setup_confidence_score"] <= 100


def test_position_sizing_formula() -> None:
    service = PositionSizingService()
    result = service.calculate(
        PositionSizingRequest(
            entry_price=Decimal("60000"),
            invalidation_level=Decimal("59000"),
            account_balance=Decimal("10000"),
            max_risk_percent=Decimal("1"),
            confidence_score=75,
            take_profit_price=Decimal("62000"),
        )
    )
    assert result.stop_loss_distance == Decimal("1000")
    assert result.maximum_acceptable_loss == Decimal("100")
    assert result.notional_position_size == Decimal("0.1")
    assert result.required_breakeven_win_rate is not None
    assert abs(result.required_breakeven_win_rate - (1 / (1 + 2))) < 0.01


def test_confidence_based_sizing_bands() -> None:
    service = PositionSizingService()
    low = service.calculate(
        PositionSizingRequest(
            entry_price=Decimal("100"),
            invalidation_level=Decimal("95"),
            account_balance=Decimal("10000"),
            max_risk_percent=Decimal("1"),
            confidence_score=35,
        )
    )
    assert low.final_recommendation.value == "no_trade"
    assert low.confidence_adjusted_size == Decimal("0")

    mid = service.calculate(
        PositionSizingRequest(
            entry_price=Decimal("100"),
            invalidation_level=Decimal("95"),
            account_balance=Decimal("10000"),
            max_risk_percent=Decimal("1"),
            confidence_score=50,
        )
    )
    assert mid.final_recommendation.value == "watch"


def test_loss_acceptance_gate() -> None:
    from app.schemas.position_sizing import LossAcceptanceRequest

    service = LossAcceptanceService()
    accepted = service.evaluate(
        planned_loss_amount=Decimal("100"),
        request=LossAcceptanceRequest(planned_loss_amount=Decimal("100"), accepted=True),
    )
    assert accepted.can_execute_paper is True

    rejected = service.evaluate(
        planned_loss_amount=Decimal("100"),
        request=LossAcceptanceRequest(planned_loss_amount=Decimal("100"), accepted=False),
    )
    assert rejected.can_execute_paper is False
    assert "skip" in rejected.recommendation.lower()


def test_risk_size_endpoint(slice33_client: tuple[TestClient, sessionmaker]) -> None:
    client, _ = slice33_client
    response = client.post(
        "/risk/size",
        json={
            "entry_price": "60000",
            "invalidation_level": "58500",
            "account_balance": "10000",
            "max_risk_percent": "1",
            "confidence_score": 70,
        },
    )
    assert response.status_code == 200
    assert Decimal(response.json()["planned_loss_amount"]) == Decimal("100")


def test_no_real_trading_path(settings: Settings) -> None:
    assert settings.enable_real_trading is False
    assert settings.execution_mode.value == "paper"
