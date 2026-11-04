"""GET /autonomous/settings/bounds: page bounds come from what the server enforces."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Membership, Organization, User
from app.db.session import get_session
from app.main import create_app
from app.schemas.autonomous_trading_settings import AutonomousTradingSettingsUpdate
from app.schemas.common import MembershipRole
from app.schemas.risk import UserRiskSettingsUpdate
from app.security.passwords import hash_password
from app.services.autonomous_settings_bounds import autonomous_settings_bounds
from app.services.autonomous_trading_settings_service import (
    AutonomousSettingsValidationError,
    validate_daily_loss_limit_percent,
    validate_risk_per_trade_percent,
)

ORG = uuid.UUID("00000000-0000-0000-0000-000000000430")
TRADER = uuid.UUID("00000000-0000-0000-0000-000000000431")


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "local",
        "log_json": False,
        "execution_mode": "paper",
        "enable_real_trading": False,
        "database_url": "sqlite+pysqlite:///:memory:",
        "jwt_secret": "autonomous-bounds-test-secret-key-0032",
        "rate_limit_use_redis": False,
        "access_token_denylist_use_redis": False,
        "provider_mode": "mock",
        "market_data_provider": "mock",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_bot_bounds_match_the_validators() -> None:
    bounds = autonomous_settings_bounds(_settings())
    risk = bounds.bot_risk_per_trade_percent
    assert (risk.min, risk.min_exclusive, risk.max) == (Decimal("0"), True, Decimal("5"))
    assert risk.max is not None
    assert validate_risk_per_trade_percent(risk.max) == risk.max
    with pytest.raises(AutonomousSettingsValidationError):
        validate_risk_per_trade_percent(risk.max + Decimal("0.01"))

    daily = bounds.bot_daily_loss_limit_percent
    assert daily.max == Decimal("6")
    assert daily.max is not None
    assert validate_daily_loss_limit_percent(daily.max) == daily.max
    with pytest.raises(AutonomousSettingsValidationError):
        validate_daily_loss_limit_percent(daily.max + Decimal("0.01"))

    trades = bounds.bot_max_trades_per_day
    assert (trades.min, trades.max) == (Decimal("1"), Decimal("1000"))
    assert trades.source == "request_schema"
    assert trades.max is not None
    with pytest.raises(ValidationError):
        AutonomousTradingSettingsUpdate(max_trades_per_day=int(trades.max) + 1)


def test_account_bounds_are_read_from_the_request_schema() -> None:
    bounds = autonomous_settings_bounds(_settings())
    risk = bounds.account_max_risk_per_trade_percent
    assert (risk.min, risk.min_exclusive, risk.max) == (Decimal("0"), True, Decimal("10"))
    assert risk.max is not None
    with pytest.raises(ValidationError):
        UserRiskSettingsUpdate(max_risk_per_trade_percent=risk.max + Decimal("0.01"))
    trades = bounds.account_max_trades_per_day
    assert (trades.min, trades.min_exclusive, trades.max) == (Decimal("1"), False, None)


def test_stop_loss_band_reports_configured_and_hard_limits() -> None:
    band = autonomous_settings_bounds(_settings()).stop_loss_band
    assert band.configured_min_percent == Decimal("1")
    assert band.configured_max_percent == Decimal("5")
    assert (band.floor_percent, band.ceiling_percent) == (Decimal("1"), Decimal("5"))
    assert band.configured_valid is True
    assert band.editable is False
    assert band.source == "environment"

    narrowed = autonomous_settings_bounds(
        _settings(
            governed_demo_stop_loss_min_percent=Decimal("2"),
            governed_demo_stop_loss_max_percent=Decimal("3"),
        )
    ).stop_loss_band
    assert (narrowed.configured_min_percent, narrowed.configured_max_percent) == (
        Decimal("2"),
        Decimal("3"),
    )
    assert narrowed.configured_valid is True

    outside = autonomous_settings_bounds(
        _settings(governed_demo_stop_loss_max_percent=Decimal("8"))
    ).stop_loss_band
    assert outside.configured_valid is False


def test_leverage_and_profile_ceilings() -> None:
    bounds = autonomous_settings_bounds(_settings(governed_demo_max_leverage=Decimal("8")))
    assert bounds.leverage.demo_hard_cap == Decimal("20")
    assert bounds.leverage.profile_max_leverage_ceiling == Decimal("20")
    assert bounds.leverage.demo_effective_max == Decimal("8")
    # Config may only lower the demo cap.
    raised = autonomous_settings_bounds(_settings(governed_demo_max_leverage=Decimal("50")))
    assert raised.leverage.demo_effective_max == Decimal("20")

    by_kind = {p.kind: p for p in bounds.profiles}
    assert set(by_kind) == {"internal_paper", "demo"}
    assert by_kind["demo"].notional_percent_ceiling == Decimal("2000")
    assert by_kind["demo"].risk_per_trade_percent_ceiling == Decimal("5")
    assert by_kind["demo"].aggregate_open_risk_percent_ceiling == Decimal("6")
    assert all(p.editable is False for p in bounds.profiles)
    assert bounds.real_trading_enabled is False


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = _settings()
    with factory() as session:
        session.add(Organization(id=ORG, name="Bounds Org"))
        session.add(
            User(
                id=TRADER,
                email="trader-430@test.example",
                hashed_password=hash_password("TestPassword123!", settings),
                timezone="UTC",
            )
        )
        session.flush()
        session.add(Membership(user_id=TRADER, organization_id=ORG, role=MembershipRole.TRADER))
        session.commit()
    app = create_app(settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    yield TestClient(app)
    engine.dispose()


def test_bounds_endpoint_is_readable_by_non_owner_and_requires_auth(client: TestClient) -> None:
    assert client.get("/autonomous/settings/bounds").status_code == 401
    login = client.post(
        "/auth/login", json={"email": "trader-430@test.example", "password": "TestPassword123!"}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}
    resp = client.get("/autonomous/settings/bounds", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["bot_risk_per_trade_percent"]["max"] == "5"
    assert body["stop_loss_band"]["editable"] is False
    assert body["real_trading_enabled"] is False
    assert [p["kind"] for p in body["profiles"]] == ["internal_paper", "demo"]
    # Read-only: no write verb on this path.
    assert client.patch("/autonomous/settings/bounds", headers=headers, json={}).status_code == 405
