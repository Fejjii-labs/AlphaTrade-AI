"""Autonomous trading settings API, validation, and enforcement (issue #284)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
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
from app.db.autonomous_trading_settings import AutonomousTradingSettingsRow
from app.db.base import Base
from app.db.models import AuditLog, Membership, Organization, User, UserStrategy
from app.main import create_app
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.common import AuditEventType, MembershipRole, StrategyId
from app.security.passwords import hash_password
from app.services.audit_service import AuditService
from app.services.automated_paper_loop import AutomatedPaperLoop, AutomatedPaperLoopProof
from app.services.autonomous_trading_guard import (
    autonomous_daily_loss_exceeded,
    autonomous_max_trades_reached,
    autonomous_precheck_refusal,
)
from app.services.autonomous_trading_settings_service import (
    AutonomousSettingsValidationError,
    AutonomousTradingSettingsService,
    ResolvedAutonomousSettings,
    validate_daily_loss_limit_percent,
    validate_max_trades_per_day,
    validate_risk_per_trade_percent,
)
from app.services.risk.daily_risk_accounting import AuthoritativeDailySnapshot
from app.services.risk_policy.grader import Grade, GradeDecision
from app.services.risk_policy.plan_sizing import (
    PROFILE_SIZING_GRADE,
    ActiveProfile,
    size_for_profile,
)
from app.services.risk_policy.profile import (
    PolicyKind,
    placeholder_profile,
    sofien_aggressive_profile,
)
from app.signal_fusion.memory import FrozenClock
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
    assert Decimal(saved.json()["risk_per_trade_percent"]) == Decimal("2")

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
        is None
    )
    configured = replace(resolved, using_defaults=False)
    assert (
        autonomous_precheck_refusal(
            configured,
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
            _snapshot(trade_count=2, realized_pnl=Decimal("-400")),
        )
        is None
    )
    assert (
        autonomous_precheck_refusal(
            configured,
            _target(),
            _snapshot(trade_count=2),
        )
        == "autonomous_max_trades_per_day"
    )
    assert (
        autonomous_precheck_refusal(
            configured,
            _target(),
            _snapshot(realized_pnl=Decimal("-250"), account_equity=Decimal("10000")),
        )
        == "autonomous_daily_loss_limit"
    )


def test_risk_override_replaces_grade_cap() -> None:
    profile = sofien_aggressive_profile(PolicyKind.INTERNAL_PAPER)
    active = ActiveProfile(profile=profile, version_id=uuid4())
    grade = GradeDecision(Grade.A_PLUS, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None)
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
    explicit_empty = size_for_profile(**common, fixed_risk_percent=None)
    override = size_for_profile(**common, fixed_risk_percent=Decimal("3"))
    invalid = size_for_profile(**common, fixed_risk_percent=Decimal("0"))
    above_ceiling = size_for_profile(**common, fixed_risk_percent=Decimal("6"))
    tight = ActiveProfile(
        profile=placeholder_profile(PolicyKind.INTERNAL_PAPER),
        version_id=uuid4(),
    )
    above_profile = size_for_profile(
        **{**common, "active": tight},
        fixed_risk_percent=Decimal("2"),
    )
    c_grade = GradeDecision(Grade.C, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None)
    c_unset = size_for_profile(**{**common, "grade": c_grade})
    c_override = size_for_profile(**{**common, "grade": c_grade}, fixed_risk_percent=Decimal("4"))
    assert not isinstance(graded, str)
    assert not isinstance(explicit_empty, str)
    assert not isinstance(override, str)
    assert not isinstance(c_override, str)
    assert explicit_empty.quantity == graded.quantity
    assert explicit_empty.risk_cash_ceiling == graded.risk_cash_ceiling == Decimal("500")
    assert override.risk_cash_ceiling == Decimal("300")
    assert invalid == "risk_override_invalid"
    assert above_ceiling == "risk_override_exceeds_ceiling"
    assert above_profile == "risk_override_exceeds_profile_max"
    assert c_unset == "grade_not_tradable"
    assert c_override.risk_cash_ceiling == Decimal("400")


def _grade_cap(terms: object) -> Decimal:
    inputs = getattr(terms, "calculation_inputs", ())
    for item in inputs:
        if getattr(item, "name", None) == PROFILE_SIZING_GRADE:
            value = item.result_value
            assert isinstance(value, Decimal)
            return value
    raise AssertionError("profile sizing grade was not recorded")


def _paper_plan(
    monkeypatch: pytest.MonkeyPatch,
    fixed: Decimal | None,
    *,
    grade: Grade = Grade.A_PLUS,
    profile_kind: str = "sofien",
) -> object:
    from app.services.automated_paper_loop import _plan_terms
    from app.signal_fusion.first_slice_adapter import default_first_slice_evaluation_params
    from tests.support.phase5_market import EVALUATED_AT
    from tests.support.phase7_trade_plan import make_world

    world = make_world()
    now = EVALUATED_AT
    decision = GradeDecision(grade, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None)
    monkeypatch.setattr(
        "app.services.automated_paper_loop.first_slice_short_invalidation_price",
        lambda **_kwargs: Decimal("110"),
    )
    monkeypatch.setattr(
        "app.services.automated_paper_loop._grade_for_plan",
        lambda *_args, **_kwargs: (decision, Decimal("1")),
    )
    if profile_kind == "sofien":
        from app.services.risk_policy.profile import profile_with

        profile = profile_with(
            PolicyKind.INTERNAL_PAPER,
            profile_name="override_probe",
            risk_percent_by_grade={
                Grade.A_PLUS: Decimal("5"),
                Grade.A: Decimal("3"),
                Grade.B_PLUS: Decimal("2"),
                Grade.B: Decimal("1"),
                Grade.C: Decimal("0"),
            },
            max_leverage=Decimal("20"),
            default_leverage=Decimal("1"),
            daily_loss_limit_percent=Decimal("6"),
            max_aggregate_open_risk_percent=Decimal("6"),
            max_open_positions=3,
            max_notional_per_trade_percent=Decimal("100"),
            max_aggregate_notional_percent=Decimal("200"),
            paper_demo_data_collection=True,
        )
    else:
        profile = placeholder_profile(PolicyKind.INTERNAL_PAPER)
    assembled = SimpleNamespace(
        current_price=SimpleNamespace(
            price=Decimal("100"),
            source_time=now - timedelta(seconds=1),
            provider_symbol="BTCUSDT",
        ),
        trigger_bar=object(),
        evaluated_at=now,
        bundle=SimpleNamespace(bars_15m=[], tick_size=Decimal("0.1")),
    )
    policy = SimpleNamespace(
        authored_spec=object(),
        evaluation_params=default_first_slice_evaluation_params(),
        strategy_id=uuid4(),
        organization_id=world.candidate.organization_id,
        strategy_version_id=world.candidate.strategy_version_id,
    )
    return _plan_terms(
        candidate=world.candidate,
        assembled=assembled,  # type: ignore[arg-type]
        policy=policy,  # type: ignore[arg-type]
        account_id=uuid4(),
        equity=Decimal("10000"),
        now=now,
        eligibility_valid_until=world.candidate.valid_until,
        eligibility_id=uuid4(),
        active_profile=ActiveProfile(profile=profile, version_id=uuid4()),
        open_positions=0,
        remaining_daily_budget_percent=Decimal("6"),
        fixed_risk_percent=fixed,
    )


def test_paper_plan_terms_risk_override(monkeypatch: pytest.MonkeyPatch) -> None:
    unset = _paper_plan(monkeypatch, None)
    three = _paper_plan(monkeypatch, Decimal("3"))
    assert not isinstance(unset, str)
    assert not isinstance(three, str)
    assert _grade_cap(unset) == Decimal("5")
    assert _grade_cap(three) == Decimal("3")
    assert _paper_plan(monkeypatch, Decimal("6")) == "risk_override_exceeds_ceiling"
    assert (
        _paper_plan(monkeypatch, Decimal("2"), profile_kind="placeholder")
        == "risk_override_exceeds_profile_max"
    )
    c_grade = _paper_plan(monkeypatch, Decimal("4"), grade=Grade.C)
    assert not isinstance(c_grade, str)
    assert _grade_cap(c_grade) == Decimal("4")


def test_demo_plan_terms_risk_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataclasses import replace as replace_dc

    from app.schemas.trade_plan import EntrySide
    from app.services import governed_blofin_demo
    from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
    from tests.support.phase5_market import EVALUATED_AT
    from tests.support.phase7_trade_plan import make_world, plan_terms
    from tests.test_demo_preflight_diagnostics import provider
    from tests.test_governed_blofin_demo import Venue

    entry = Decimal("3000000")
    stop = Decimal("2880000")
    distance = entry - stop
    world = make_world()
    drafted = plan_terms(world.candidate)
    risk = drafted.risk_and_exits.model_copy(
        update={
            "stop": drafted.risk_and_exits.stop.model_copy(update={"value": stop}),
            "targets": (
                drafted.risk_and_exits.targets[0].model_copy(
                    update={
                        "quantity_fraction": Decimal("1"),
                        "price": drafted.risk_and_exits.targets[0].price.model_copy(
                            update={"value": entry + distance}
                        ),
                    }
                ),
            ),
            "runner": drafted.risk_and_exits.runner.model_copy(
                update={
                    "enabled": False,
                    "activation_target_order": None,
                    "remaining_quantity_fraction": Decimal("0"),
                }
            ),
        }
    )
    basis = drafted.basis_policy.model_copy(
        update={
            "evidence_price": drafted.basis_policy.evidence_price.model_copy(
                update={"value": entry}
            ),
            "execution_price": drafted.basis_policy.execution_price.model_copy(
                update={"value": entry}
            ),
        }
    )
    terms = drafted.model_copy(
        update={"side": EntrySide.BUY, "risk_and_exits": risk, "basis_policy": basis}
    )
    monkeypatch.setattr(governed_blofin_demo, "_plan_terms", lambda **_kwargs: terms)
    from app.services.risk_policy.profile import profile_with

    active = ActiveProfile(
        profile=profile_with(
            PolicyKind.DEMO,
            profile_name="override_probe_demo",
            risk_percent_by_grade={
                Grade.A_PLUS: Decimal("5"),
                Grade.A: Decimal("3"),
                Grade.B_PLUS: Decimal("2"),
                Grade.B: Decimal("1"),
                Grade.C: Decimal("0"),
            },
            max_leverage=Decimal("20"),
            default_leverage=Decimal("1"),
            daily_loss_limit_percent=Decimal("6"),
            max_aggregate_open_risk_percent=Decimal("6"),
            max_open_positions=3,
            max_notional_per_trade_percent=Decimal("100"),
            max_aggregate_notional_percent=Decimal("200"),
            paper_demo_data_collection=True,
        ),
        version_id=uuid4(),
    )
    tight = ActiveProfile(
        profile=placeholder_profile(PolicyKind.DEMO),
        version_id=uuid4(),
    )
    grade = GradeDecision(Grade.A_PLUS, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None)
    grades = {"current": grade}

    def _graded(*_args: object, **_kwargs: object) -> tuple[GradeDecision, Decimal]:
        return grades["current"], Decimal("1")

    monkeypatch.setattr("app.services.automated_paper_loop._grade_for_plan", _graded)
    venue = Venue(entry - Decimal("0.1"))
    snapshot = provider(venue.handle).snapshot(
        symbol="BTCUSDT",
        now=EVALUATED_AT,
        side=terms.side,
    )
    loop = GovernedBloFinDemoLoop(None, None, None)  # type: ignore[arg-type]
    loop._snapshot = replace_dc(snapshot, account_leverage=Decimal("1"), margin_mode="isolated")
    common = {
        "candidate": world.candidate,
        "assembled": None,
        "policy": None,
        "account_id": uuid4(),
        "equity": Decimal("10000"),
        "now": EVALUATED_AT,
        "eligibility_valid_until": terms.valid_until,
        "eligibility_id": uuid4(),
        "active_profile": active,
        "open_exposure": Decimal("0"),
        "open_risk_percent": Decimal("0"),
        "open_positions": 0,
        "remaining_daily_budget_percent": Decimal("6"),
        "breaker_refusing": False,
    }
    unset = loop._plan_terms(**common)  # type: ignore[arg-type]
    three = loop._plan_terms(**common, fixed_risk_percent=Decimal("3"))  # type: ignore[arg-type]
    assert not isinstance(unset, str)
    assert not isinstance(three, str)
    assert _grade_cap(unset) == Decimal("5")
    assert _grade_cap(three) == Decimal("3")
    assert (
        loop._plan_terms(**common, fixed_risk_percent=Decimal("6"))  # type: ignore[arg-type]
        == "risk_override_exceeds_ceiling"
    )
    assert (
        loop._plan_terms(  # type: ignore[arg-type]
            **{**common, "active_profile": tight},
            fixed_risk_percent=Decimal("2"),
        )
        == "risk_override_exceeds_profile_max"
    )
    grades["current"] = GradeDecision(
        Grade.C, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None
    )
    c_trade = loop._plan_terms(**common, fixed_risk_percent=Decimal("4"))  # type: ignore[arg-type]
    assert not isinstance(c_trade, str)
    assert _grade_cap(c_trade) == Decimal("4")


def test_api_rejects_non_numeric_and_explicit_null(client: TestClient) -> None:
    headers = _token(client, "owner-284@test.example")
    for payload in (
        {"daily_loss_limit_percent": "nope"},
        {"risk_per_trade_percent": "NaN"},
        {"risk_per_trade_percent": "Infinity"},
        {"risk_per_trade_percent": "-1"},
        {"max_trades_per_day": 1001},
        {"max_trades_per_day": "many"},
    ):
        denied = client.patch("/autonomous/settings", headers=headers, json=payload)
        assert denied.status_code == 422, payload

    with pytest.raises(AutonomousSettingsValidationError) as exc:
        validate_risk_per_trade_percent(Decimal("NaN"))
    assert exc.value.code == "risk_per_trade_invalid"
    with pytest.raises(AutonomousSettingsValidationError) as exc:
        validate_max_trades_per_day(1001)
    assert exc.value.code == "max_trades_per_day_too_large"

    saved = client.patch(
        "/autonomous/settings",
        headers=headers,
        json={"risk_per_trade_percent": "2", "strategy_id": str(STRATEGY), "symbol": "BTCUSDT"},
    )
    assert saved.status_code == 200
    cleared = client.patch(
        "/autonomous/settings",
        headers=headers,
        json={"risk_per_trade_percent": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["risk_per_trade_percent"] is None


def test_same_org_non_owner_cannot_read_or_write_another_user(
    client: TestClient, settings_db
) -> None:
    owner_headers = _token(client, "owner-284@test.example")
    saved = client.patch(
        "/autonomous/settings",
        headers=owner_headers,
        json={"max_trades_per_day": 2, "daily_loss_limit_percent": "2"},
    )
    assert saved.status_code == 200
    trader_headers = _token(client, "trader-284@test.example")
    denied = client.patch(
        "/autonomous/settings",
        headers=trader_headers,
        json={"max_trades_per_day": 9},
    )
    assert denied.status_code == 403
    seen = client.get("/autonomous/settings", headers=trader_headers)
    assert seen.status_code == 200
    body = seen.json()
    assert body["user_id"] == str(TRADER)
    assert body["using_defaults"] is True
    assert body["max_trades_per_day"] == 5
    factory, _settings = settings_db
    with factory() as session:
        owner = AutonomousTradingSettingsService(session, AuditService(session)).resolve(
            organization_id=ORG, user_id=OWNER
        )
        trader = AutonomousTradingSettingsService(session, AuditService(session)).resolve(
            organization_id=ORG, user_id=TRADER
        )
    assert owner.using_defaults is False
    assert owner.max_trades_per_day == 2
    assert trader.using_defaults is True


def test_strategy_must_belong_to_the_caller(client: TestClient, settings_db) -> None:
    factory, _settings = settings_db
    other = uuid4()
    with factory() as session:
        session.add(
            UserStrategy(
                id=other,
                organization_id=ORG,
                user_id=TRADER,
                name="Trader strategy",
                setup_type=StrategyId.HTF_TREND_PULLBACK,
                enabled=True,
            )
        )
        session.commit()
    headers = _token(client, "owner-284@test.example")
    denied = client.patch(
        "/autonomous/settings",
        headers=headers,
        json={"strategy_id": str(other)},
    )
    assert denied.status_code == 422
    assert denied.json()["detail"] == "strategy_not_available"


def test_audit_request_id_is_unique_per_change(client: TestClient, settings_db) -> None:
    headers = _token(client, "owner-284@test.example")
    first = client.patch("/autonomous/settings", headers=headers, json={"max_trades_per_day": 3})
    second = client.patch("/autonomous/settings", headers=headers, json={"max_trades_per_day": 4})
    assert first.status_code == second.status_code == 200
    factory, _settings = settings_db
    with factory() as session:
        rows = session.scalars(
            select(AuditLog).where(
                AuditLog.action == AuditEventType.AUTONOMOUS_TRADING_SETTINGS_UPDATED
            )
        ).all()
    ids = [row.request_id for row in rows]
    assert len(ids) >= 2
    assert len(set(ids)) == len(ids)
    assert all(item is not None and item.startswith("settings-") for item in ids)


def test_repeated_save_keeps_one_row(settings_db) -> None:
    """Upsert path: a second save updates the row instead of inserting another."""

    factory, _settings = settings_db
    from app.schemas.autonomous_trading_settings import AutonomousTradingSettingsUpdate

    for trades in (3, 4):
        with factory() as session:
            AutonomousTradingSettingsService(session, AuditService(session)).update(
                AutonomousTradingSettingsUpdate(max_trades_per_day=trades),
                organization_id=ORG,
                user_id=OWNER,
            )
            session.commit()
    with factory() as session:
        rows = session.scalars(select(AutonomousTradingSettingsRow)).all()
        resolved = AutonomousTradingSettingsService(session, AuditService(session)).resolve(
            organization_id=ORG, user_id=OWNER
        )
    assert len(rows) == 1
    assert resolved.using_defaults is False
    assert resolved.max_trades_per_day == 4


class _ProbeLoop(AutomatedPaperLoop):
    def __init__(self, settings: object) -> None:
        super().__init__(
            SimpleNamespace(bind_session=lambda _session: nullcontext()),  # type: ignore[arg-type]
            settings,  # type: ignore[arg-type]
            FrozenClock(datetime(2026, 10, 20, tzinfo=UTC)),
        )
        self.bound_calls = 0

    def _preface(self, **_kwargs: object) -> None:
        return None

    def _account(self, _session: object, _target: object) -> SimpleNamespace:
        return SimpleNamespace(id=uuid4())

    def _bound(self, *_args: object, **_kwargs: object) -> AutomatedPaperLoopProof:
        self.bound_calls += 1
        return AutomatedPaperLoopProof(stage="opened", reason_code="probe_reached_bound")


def _run_probe(
    factory: object,
    settings: object,
    snapshot: AuthoritativeDailySnapshot,
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: PaperScanTarget | None = None,
) -> AutomatedPaperLoopProof:
    class _Accounting:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def sync_from_portfolio(self, **_kwargs: object) -> AuthoritativeDailySnapshot:
            return snapshot

    monkeypatch.setattr(
        "app.services.risk.daily_risk_accounting.DailyRiskAccounting",
        _Accounting,
    )
    loop = _ProbeLoop(settings)
    with factory() as session:  # type: ignore[operator]
        proof = loop.continue_confirmed_setup(
            session,
            target=_target() if target is None else target,
            candidate=SimpleNamespace(candidate_id=uuid4()),  # type: ignore[arg-type]
            assessment=None,  # type: ignore[arg-type]
            window=None,  # type: ignore[arg-type]
            assembled=None,  # type: ignore[arg-type]
            policy=None,  # type: ignore[arg-type]
            kill_switch_active=False,
        )
    assert loop.bound_calls == (1 if proof.reason_code == "probe_reached_bound" else 0)
    return proof


def test_saved_stops_block_the_loop_and_defaults_do_not(
    settings_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, settings = settings_db
    harsh = _snapshot(realized_pnl=Decimal("-400"), trade_count=6)
    unsaved = _run_probe(factory, settings, harsh, monkeypatch)
    assert unsaved.reason_code == "probe_reached_bound"

    with factory() as session:
        from app.schemas.autonomous_trading_settings import AutonomousTradingSettingsUpdate

        AutonomousTradingSettingsService(session, AuditService(session)).update(
            AutonomousTradingSettingsUpdate(
                daily_loss_limit_percent=Decimal("2"),
                max_trades_per_day=2,
                strategy_id=STRATEGY,
                symbol="BTCUSDT",
            ),
            organization_id=ORG,
            user_id=OWNER,
        )
        session.commit()

    loss = _run_probe(
        factory,
        settings,
        _snapshot(realized_pnl=Decimal("-250"), trade_count=0),
        monkeypatch,
    )
    assert loss.reason_code == "autonomous_daily_loss_limit"
    trades = _run_probe(factory, settings, _snapshot(trade_count=2), monkeypatch)
    assert trades.reason_code == "autonomous_max_trades_per_day"
    allowed = _run_probe(factory, settings, _snapshot(), monkeypatch)
    assert allowed.reason_code == "probe_reached_bound"
    mismatch = _run_probe(
        factory,
        settings,
        _snapshot(),
        monkeypatch,
        target=_target(symbol="ETHUSDT"),
    )
    assert mismatch.reason_code == "autonomous_symbol_mismatch"


def test_kill_switch_still_refuses_when_settings_allow(settings_db) -> None:
    factory, settings = settings_db
    with factory() as session:
        from app.schemas.autonomous_trading_settings import AutonomousTradingSettingsUpdate

        AutonomousTradingSettingsService(session, AuditService(session)).update(
            AutonomousTradingSettingsUpdate(
                daily_loss_limit_percent=Decimal("4"),
                max_trades_per_day=5,
                strategy_id=STRATEGY,
                symbol="BTCUSDT",
            ),
            organization_id=ORG,
            user_id=OWNER,
        )
        session.commit()
    loop = AutomatedPaperLoop(
        SimpleNamespace(),  # type: ignore[arg-type]
        settings,
        FrozenClock(datetime(2026, 10, 20, tzinfo=UTC)),
    )
    proof = loop.continue_confirmed_setup(
        SimpleNamespace(),  # type: ignore[arg-type]
        target=_target(),
        candidate=SimpleNamespace(candidate_id=uuid4()),  # type: ignore[arg-type]
        assessment=None,  # type: ignore[arg-type]
        window=None,  # type: ignore[arg-type]
        assembled=None,  # type: ignore[arg-type]
        policy=None,  # type: ignore[arg-type]
        kill_switch_active=True,
    )
    assert proof.reason_code == "kill_switch_active"
