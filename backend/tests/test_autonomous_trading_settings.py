"""Autonomous trading settings API, validation, and enforcement (issue #284)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.dependencies import get_session
from app.db.base import Base
from app.db.models import AuditLog, Membership, Organization, User, UserStrategy
from app.main import create_app
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.common import AuditEventType, MembershipRole, StrategyId
from app.security.passwords import hash_password
from app.services.autonomous_trading_guard import (
    autonomous_daily_loss_exceeded,
    autonomous_max_trades_reached,
    autonomous_precheck_refusal,
)
from app.services.autonomous_trading_settings_service import (
    AutonomousSettingsValidationError,
    ResolvedAutonomousSettings,
    validate_daily_loss_limit_percent,
    validate_max_trades_per_day,
    validate_risk_per_trade_percent,
)
from app.services.risk.daily_risk_accounting import AuthoritativeDailySnapshot
from app.services.risk_policy.grader import Grade, GradeDecision
from app.services.risk_policy.plan_sizing import ActiveProfile, size_for_profile
from app.services.risk_policy.profile import PolicyKind, sofien_aggressive_profile
from app.workers.watcher_paper_targets import PaperScanTarget

ORG = uuid.UUID("00000000-0000-0000-0000-000000000284")
OWNER = uuid.UUID("00000000-0000-0000-0000-000000000285")
TRADER = uuid.UUID("00000000-0000-0000-0000-000000000286")
STRATEGY = uuid.UUID("00000000-0000-0000-0000-000000000287")


@pytest.fixture
def settings_db() -> Iterator[tuple[sessionmaker[Session], Settings]]:
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
        jwt_secret="autonomous-settings-test-secret-key-32",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
    )
    with factory() as session:
        session.add(Organization(id=ORG, name="Autonomous Org"))
        session.add(
            User(
                id=OWNER,
                email="owner-284@test.example",
                hashed_password=hash_password("TestPassword123!", settings),
                timezone="UTC",
            )
        )
        session.add(
            User(
                id=TRADER,
                email="trader-284@test.example",
                hashed_password=hash_password("TestPassword123!", settings),
                timezone="UTC",
            )
        )
        session.flush()
        session.add(Membership(user_id=OWNER, organization_id=ORG, role=MembershipRole.OWNER))
        session.add(Membership(user_id=TRADER, organization_id=ORG, role=MembershipRole.TRADER))
        session.add(
            UserStrategy(
                id=STRATEGY,
                organization_id=ORG,
                user_id=OWNER,
                name="Nested BTC",
                setup_type=StrategyId.HTF_TREND_PULLBACK,
                enabled=True,
            )
        )
        WatcherWatchlistRepository(session).replace(
            ORG,
            [("BTCUSDT", True), ("ETHUSDT", False)],
            expected_revision=0,
        )
        session.commit()
    yield factory, settings
    engine.dispose()


@pytest.fixture
def client(settings_db: tuple[sessionmaker[Session], Settings]) -> TestClient:
    factory, settings = settings_db
    app = create_app(settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    return TestClient(app)


def _token(client: TestClient, email: str) -> dict[str, str]:
    login = client.post(
        "/auth/login",
        json={"email": email, "password": "TestPassword123!"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}


def test_validation_edges() -> None:
    assert validate_daily_loss_limit_percent(Decimal("3")) == Decimal("3")
    with pytest.raises(AutonomousSettingsValidationError) as exc:
        validate_daily_loss_limit_percent(Decimal("0"))
    assert exc.value.code == "daily_loss_limit_non_positive"
    with pytest.raises(AutonomousSettingsValidationError) as exc:
        validate_daily_loss_limit_percent(Decimal("-1"))
    assert exc.value.code == "daily_loss_limit_non_positive"
    with pytest.raises(AutonomousSettingsValidationError) as exc:
        validate_daily_loss_limit_percent(Decimal("7"))
    assert exc.value.code == "daily_loss_limit_exceeds_ceiling"

    assert validate_max_trades_per_day(3) == 3
    with pytest.raises(AutonomousSettingsValidationError):
        validate_max_trades_per_day(0)
    with pytest.raises(AutonomousSettingsValidationError):
        validate_max_trades_per_day(-2)

    assert validate_risk_per_trade_percent(None) is None
    assert validate_risk_per_trade_percent(Decimal("2.5")) == Decimal("2.5")
    with pytest.raises(AutonomousSettingsValidationError) as exc:
        validate_risk_per_trade_percent(Decimal("6"))
    assert exc.value.code == "risk_per_trade_exceeds_ceiling"
    with pytest.raises(AutonomousSettingsValidationError):
        validate_risk_per_trade_percent(Decimal("0"))


def test_get_defaults_and_patch(client: TestClient) -> None:
    headers = _token(client, "owner-284@test.example")
    resp = client.get("/autonomous/settings", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["using_defaults"] is True
    assert body["daily_loss_limit_percent"] == "3"
    assert body["max_trades_per_day"] == 5
    assert body["risk_per_trade_percent"] is None
    assert "BTCUSDT" in body["symbol_options"]

    saved = client.patch(
        "/autonomous/settings",
        headers=headers,
        json={
            "daily_loss_limit_percent": "4",
            "max_trades_per_day": 2,
            "risk_per_trade_percent": "2",
            "strategy_id": str(STRATEGY),
            "symbol": "BTCUSDT",
        },
    )
    assert saved.status_code == 200
    assert saved.json()["using_defaults"] is False
    assert saved.json()["risk_per_trade_percent"] == "2"

    cleared = client.patch(
        "/autonomous/settings",
        headers=headers,
        json={"clear_risk_per_trade_percent": True},
    )
    assert cleared.status_code == 200
    assert cleared.json()["risk_per_trade_percent"] is None


def test_patch_rejects_ceiling_and_bad_symbol(client: TestClient) -> None:
    headers = _token(client, "owner-284@test.example")
    over_daily = client.patch(
        "/autonomous/settings",
        headers=headers,
        json={"daily_loss_limit_percent": "6.1"},
    )
    assert over_daily.status_code == 422
    assert over_daily.json()["detail"] == "daily_loss_limit_exceeds_ceiling"

    over_risk = client.patch(
        "/autonomous/settings",
        headers=headers,
        json={"risk_per_trade_percent": "5.01"},
    )
    assert over_risk.status_code == 422

    bad_symbol = client.patch(
        "/autonomous/settings",
        headers=headers,
        json={"symbol": "SOLUSDT"},
    )
    assert bad_symbol.status_code == 422
    assert bad_symbol.json()["detail"] == "symbol_not_watched"


def test_owner_scope_for_writes(client: TestClient, settings_db) -> None:
    trader_headers = _token(client, "trader-284@test.example")
    denied = client.patch(
        "/autonomous/settings",
        headers=trader_headers,
        json={"max_trades_per_day": 1},
    )
    assert denied.status_code == 403

    owner_headers = _token(client, "owner-284@test.example")
    ok = client.patch(
        "/autonomous/settings",
        headers=owner_headers,
        json={"max_trades_per_day": 4},
    )
    assert ok.status_code == 200

    factory, _ = settings_db
    with factory() as session:
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == AuditEventType.AUTONOMOUS_TRADING_SETTINGS_UPDATED
            )
        )
        assert audit is not None


def _snapshot(**updates: object) -> AuthoritativeDailySnapshot:
    values: dict[str, object] = {
        "day": date(2026, 10, 20),
        "realized_pnl": Decimal("0"),
        "unrealized_pnl": Decimal("0"),
        "open_exposure_notional": Decimal("0"),
        "trade_count": 0,
        "daily_locked": False,
        "daily_loss_limit": None,
        "max_trades_per_day": None,
        "account_equity": Decimal("10000"),
        "row": SimpleNamespace(),
    }
    values.update(updates)
    return AuthoritativeDailySnapshot(**values)  # type: ignore[arg-type]


def _target(**updates: object) -> PaperScanTarget:
    values: dict[str, object] = {
        "organization_id": ORG,
        "user_id": OWNER,
        "strategy_id": STRATEGY,
        "strategy_version_id": uuid4(),
        "compiled_setup_definition_id": uuid4(),
        "compiled_content_hash": "a" * 64,
        "fusion_policy_version": "test",
        "symbol": "BTCUSDT",
    }
    values.update(updates)
    return PaperScanTarget(**values)  # type: ignore[arg-type]


def test_enforcement_guards() -> None:
    resolved = ResolvedAutonomousSettings(
        daily_loss_limit_percent=Decimal("2"),
        max_trades_per_day=2,
        risk_per_trade_percent=None,
        strategy_id=STRATEGY,
        symbol="BTCUSDT",
        using_defaults=True,
    )
    assert autonomous_precheck_refusal(resolved, _target(), _snapshot()) is None
    assert (
        autonomous_precheck_refusal(
            resolved,
            _target(symbol="ETHUSDT"),
            _snapshot(),
        )
        == "autonomous_symbol_mismatch"
    )
    assert autonomous_daily_loss_exceeded(
        resolved,
        _snapshot(realized_pnl=Decimal("-250"), account_equity=Decimal("10000")),
    )
    assert not autonomous_daily_loss_exceeded(
        resolved,
        _snapshot(realized_pnl=Decimal("-100"), account_equity=Decimal("10000")),
    )
    assert autonomous_max_trades_reached(resolved, _snapshot(trade_count=2))
    assert not autonomous_max_trades_reached(resolved, _snapshot(trade_count=1))
    assert (
        autonomous_precheck_refusal(
            resolved,
            _target(),
            _snapshot(trade_count=2),
        )
        == "autonomous_max_trades_per_day"
    )


def test_risk_override_replaces_grade_cap() -> None:
    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    active = ActiveProfile(profile=profile, version_id=uuid4())
    grade = GradeDecision(
        Grade.A_PLUS, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None
    )
    common = {
        "active": active,
        "grade": grade,
        "equity": Decimal("10000"),
        "entry": Decimal("100"),
        "stop": Decimal("90"),
        "target": Decimal("120"),
        "direction": "long",
        "atr": Decimal("2"),
        "lot_step": Decimal("0.00000001"),
        "min_quantity": Decimal("0.00000001"),
        "open_risk_percent": Decimal("0"),
        "open_notional": Decimal("0"),
        "open_positions": 0,
        "remaining_daily_budget_percent": Decimal("6"),
        "breaker_refusing": False,
        "leverage": Decimal("1"),
        "execution_channel": "internal_paper",
    }
    graded = size_for_profile(**common)
    override = size_for_profile(**common, fixed_risk_percent=Decimal("2"))
    invalid = size_for_profile(**common, fixed_risk_percent=Decimal("0"))
    assert not isinstance(graded, str)
    assert not isinstance(override, str)
    assert invalid == "risk_override_invalid"
    assert graded.risk_cash_ceiling == Decimal("500")
    assert override.risk_cash_ceiling == Decimal("200")
    assert override.quantity < graded.quantity
