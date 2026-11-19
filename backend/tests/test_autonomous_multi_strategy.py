"""Multi-strategy autonomy: a set of strategies (or every eligible one), one position per coin.

The bot may take a setup from any listed (or, in ``all_eligible`` mode, any)
enabled, lifecycle-approved, paper-eligible strategy, long or short. Unlisted,
disabled, unapproved and non-eligible strategies are still refused, and two
strategies firing on the same coin never open two positions: the first intent
wins and the second is refused with ``autonomous_symbol_position_open``.
On the BloFin demo loop paper eligibility is not required (enabled plus
approved/active is enough); every other loop keeps that gate.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, date, datetime
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
from app.db.models import (
    JournalTrade,
    Membership,
    Organization,
    StrategyLifecycleEvent,
    User,
    UserStrategy,
    UserStrategyVersion,
)
from app.main import create_app
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.autonomous_trading_settings import AutonomousTradingSettingsUpdate
from app.schemas.common import (
    JournalTradeSource,
    JournalTradeStatus,
    MembershipRole,
    StrategyId,
    StrategyLifecycleState,
    TradeDirection,
)
from app.security.passwords import hash_password
from app.services.audit_service import AuditService
from app.services.automated_paper_loop import AutomatedPaperLoop, AutomatedPaperLoopProof
from app.services.autonomous_trading_guard import (
    AutonomousStrategyStatus,
    autonomous_precheck_refusal,
    load_autonomous_strategy_status,
    open_symbol_position_refusal,
)
from app.services.autonomous_trading_settings_service import (
    AutonomousTradingSettingsService,
    ResolvedAutonomousSettings,
)
from app.services.risk.daily_risk_accounting import AuthoritativeDailySnapshot
from app.signal_fusion.memory import FrozenClock
from app.workers.watcher_paper_targets import PaperScanTarget

ORG = uuid.UUID("00000000-0000-0000-0000-000000000466")
OWNER = uuid.UUID("00000000-0000-0000-0000-000000000467")
LONG = uuid.UUID("00000000-0000-0000-0000-000000000468")
SHORT = uuid.UUID("00000000-0000-0000-0000-000000000469")
DRAFT = uuid.UUID("00000000-0000-0000-0000-00000000046a")
NOT_ELIGIBLE = uuid.UUID("00000000-0000-0000-0000-00000000046b")
DISABLED = uuid.UUID("00000000-0000-0000-0000-00000000046c")
# Version ids follow the strategy ids so targets can point at them.
VERSION = {sid: uuid.UUID(int=sid.int + 0x1000) for sid in (LONG, SHORT, DRAFT, NOT_ELIGIBLE)}
VERSION[DISABLED] = uuid.UUID(int=DISABLED.int + 0x1000)


def _seed_strategy(
    session: Session,
    strategy_id: uuid.UUID,
    name: str,
    *,
    state: StrategyLifecycleState,
    paper_eligible: bool,
    enabled: bool = True,
) -> None:
    session.add(
        UserStrategy(
            id=strategy_id,
            organization_id=ORG,
            user_id=OWNER,
            name=name,
            setup_type=StrategyId.HTF_TREND_PULLBACK,
            enabled=enabled,
            paper_eligible=paper_eligible,
        )
    )
    session.flush()
    session.add(
        UserStrategyVersion(
            id=VERSION[strategy_id],
            strategy_id=strategy_id,
            version=1,
            card={},
            content_hash="c" * 64,
        )
    )
    session.flush()
    session.add(
        StrategyLifecycleEvent(
            organization_id=ORG,
            strategy_id=strategy_id,
            strategy_version_id=VERSION[strategy_id],
            prior_state=None,
            new_state=state,
            evidence_snapshot={},
            occurred_at=datetime(2026, 11, 1, tzinfo=UTC),
            event_hash="e" * 64,
        )
    )


@pytest.fixture
def db() -> Iterator[tuple[sessionmaker[Session], Settings]]:
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
        jwt_secret="multi-strategy-autonomy-test-secret-32",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
    )
    with factory() as session:
        session.add(Organization(id=ORG, name="Multi strategy org"))
        session.add(
            User(
                id=OWNER,
                email="owner-466@test.example",
                hashed_password=hash_password("TestPassword123!", settings),
                timezone="UTC",
            )
        )
        session.flush()
        session.add(Membership(user_id=OWNER, organization_id=ORG, role=MembershipRole.OWNER))
        _seed_strategy(
            session, LONG, "Nested long", state=StrategyLifecycleState.APPROVED, paper_eligible=True
        )
        _seed_strategy(
            session, SHORT, "Nested short", state=StrategyLifecycleState.ACTIVE, paper_eligible=True
        )
        _seed_strategy(
            session, DRAFT, "Draft idea", state=StrategyLifecycleState.DRAFT, paper_eligible=True
        )
        _seed_strategy(
            session,
            NOT_ELIGIBLE,
            "Approved, no evidence",
            state=StrategyLifecycleState.APPROVED,
            paper_eligible=False,
        )
        _seed_strategy(
            session,
            DISABLED,
            "Switched off",
            state=StrategyLifecycleState.APPROVED,
            paper_eligible=True,
            enabled=False,
        )
        WatcherWatchlistRepository(session).replace(
            ORG, [("BTCUSDT", True), ("ETHUSDT", True)], expected_revision=0
        )
        session.commit()
    yield factory, settings
    engine.dispose()


def _target(strategy_id: uuid.UUID = LONG, **updates: object) -> PaperScanTarget:
    values: dict[str, object] = {
        "organization_id": ORG,
        "user_id": OWNER,
        "strategy_id": strategy_id,
        "strategy_version_id": VERSION.get(strategy_id, uuid4()),
        "compiled_setup_definition_id": uuid4(),
        "compiled_content_hash": "a" * 64,
        "fusion_policy_version": "test",
        "symbol": "BTCUSDT",
    }
    values.update(updates)
    return PaperScanTarget(**values)  # type: ignore[arg-type]


def _snapshot(**updates: object) -> AuthoritativeDailySnapshot:
    values: dict[str, object] = {
        "day": date(2026, 11, 19),
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


def _resolved(**updates: object) -> ResolvedAutonomousSettings:
    values: dict[str, object] = {
        "daily_loss_limit_percent": Decimal("3"),
        "max_trades_per_day": 5,
        "risk_per_trade_percent": None,
        "strategy_ids": frozenset({LONG, SHORT}),
        "strategy_scope": "selected",
        "symbol": "BTCUSDT",
        "using_defaults": False,
    }
    values.update(updates)
    return ResolvedAutonomousSettings(**values)  # type: ignore[arg-type]


def _status(strategy_id: uuid.UUID = LONG, **updates: object) -> AutonomousStrategyStatus:
    values: dict[str, object] = {
        "strategy_id": strategy_id,
        "enabled": True,
        "approved": True,
        "paper_eligible": True,
    }
    values.update(updates)
    return AutonomousStrategyStatus(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Guard (pure)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("strategy_id", [LONG, SHORT])
def test_any_listed_eligible_strategy_passes(strategy_id: uuid.UUID) -> None:
    refusal = autonomous_precheck_refusal(
        _resolved(),
        _target(strategy_id),
        _snapshot(),
        strategy_status=_status(strategy_id),
    )
    assert refusal is None


def test_unlisted_unapproved_ineligible_and_disabled_are_refused() -> None:
    other = uuid4()
    cases = {
        "autonomous_strategy_mismatch": (_target(other), _status(other)),
        "autonomous_strategy_disabled": (_target(), _status(enabled=False)),
        "autonomous_strategy_not_approved": (_target(), _status(approved=False)),
        "autonomous_strategy_not_paper_eligible": (_target(), _status(paper_eligible=False)),
        # Status for a different strategy, or none at all, is not proof.
        "autonomous_strategy_status_unverified": (_target(), _status(SHORT)),
    }
    for expected, (target, status) in cases.items():
        assert (
            autonomous_precheck_refusal(_resolved(), target, _snapshot(), strategy_status=status)
            == expected
        ), expected
    assert (
        autonomous_precheck_refusal(_resolved(), _target(), _snapshot(), strategy_status=None)
        == "autonomous_strategy_status_unverified"
    )


def test_all_eligible_mode_accepts_any_eligible_strategy_only() -> None:
    every = _resolved(strategy_ids=frozenset(), strategy_scope="all_eligible")
    anyone = uuid4()
    assert (
        autonomous_precheck_refusal(
            every, _target(anyone), _snapshot(), strategy_status=_status(anyone)
        )
        is None
    )
    assert (
        autonomous_precheck_refusal(
            every, _target(anyone), _snapshot(), strategy_status=_status(anyone, approved=False)
        )
        == "autonomous_strategy_not_approved"
    )
    assert (
        autonomous_precheck_refusal(
            every,
            _target(anyone),
            _snapshot(),
            strategy_status=_status(anyone, paper_eligible=False),
        )
        == "autonomous_strategy_not_paper_eligible"
    )


@pytest.mark.parametrize("scope", ["selected", "all_eligible"])
def test_demo_does_not_require_paper_eligibility(scope: str) -> None:
    # Owner decision 2026-11-19: on BloFin demo, enabled + approved/active is enough.
    settings = _resolved(strategy_ids=frozenset({NOT_ELIGIBLE}), strategy_scope=scope)
    status = _status(NOT_ELIGIBLE, paper_eligible=False)
    assert (
        autonomous_precheck_refusal(
            settings,
            _target(NOT_ELIGIBLE),
            _snapshot(),
            strategy_status=status,
            require_paper_eligible=False,
        )
        is None
    )
    # Off demo (the default) the paper gate still applies.
    assert (
        autonomous_precheck_refusal(
            settings, _target(NOT_ELIGIBLE), _snapshot(), strategy_status=status
        )
        == "autonomous_strategy_not_paper_eligible"
    )


def test_demo_still_refuses_disabled_unapproved_and_unverified() -> None:
    every = _resolved(strategy_ids=frozenset(), strategy_scope="all_eligible")
    cases = {
        "autonomous_strategy_disabled": _status(enabled=False, paper_eligible=False),
        "autonomous_strategy_not_approved": _status(approved=False, paper_eligible=False),
        "autonomous_strategy_status_unverified": None,
    }
    for expected, status in cases.items():
        assert (
            autonomous_precheck_refusal(
                every,
                _target(),
                _snapshot(),
                strategy_status=status,
                require_paper_eligible=False,
            )
            == expected
        ), expected


def test_existing_caps_still_apply_with_many_strategies() -> None:
    empty = _resolved(strategy_ids=frozenset())
    assert (
        autonomous_precheck_refusal(empty, _target(), _snapshot(), strategy_status=_status())
        == "autonomous_settings_incomplete"
    )
    no_coin = _resolved(symbol=None)
    assert (
        autonomous_precheck_refusal(no_coin, _target(), _snapshot(), strategy_status=_status())
        == "autonomous_settings_incomplete"
    )
    assert (
        autonomous_precheck_refusal(
            _resolved(),
            _target(symbol="ETHUSDT"),
            _snapshot(),
            strategy_status=_status(),
        )
        == "autonomous_symbol_mismatch"
    )
    assert (
        autonomous_precheck_refusal(
            _resolved(max_trades_per_day=2),
            _target(SHORT),
            _snapshot(trade_count=2),
            strategy_status=_status(SHORT),
        )
        == "autonomous_max_trades_per_day"
    )
    # Unsaved settings keep the previous loop unchanged.
    assert (
        autonomous_precheck_refusal(
            _resolved(using_defaults=True),
            _target(uuid4()),
            _snapshot(trade_count=99),
            strategy_status=None,
        )
        is None
    )


def test_strategy_status_reads_lifecycle_and_eligibility(db) -> None:
    factory, _settings = db
    with factory() as session:
        long = load_autonomous_strategy_status(session, _target(LONG))
        short = load_autonomous_strategy_status(session, _target(SHORT))
        draft = load_autonomous_strategy_status(session, _target(DRAFT))
        not_eligible = load_autonomous_strategy_status(session, _target(NOT_ELIGIBLE))
        disabled = load_autonomous_strategy_status(session, _target(DISABLED))
        # A version of another strategy does not prove this strategy approved.
        borrowed = load_autonomous_strategy_status(
            session, _target(DRAFT, strategy_version_id=VERSION[LONG])
        )
        missing = load_autonomous_strategy_status(session, _target(uuid4()))
    assert long == _status(LONG)
    assert short == _status(SHORT)
    assert draft == _status(DRAFT, approved=False)
    assert not_eligible == _status(NOT_ELIGIBLE, paper_eligible=False)
    assert disabled == _status(DISABLED, enabled=False)
    assert borrowed is not None and borrowed.approved is False
    assert missing is None


# --------------------------------------------------------------------------- #
# Settings service and API
# --------------------------------------------------------------------------- #


def _service(session: Session) -> AutonomousTradingSettingsService:
    return AutonomousTradingSettingsService(session, AuditService(session))


def test_settings_hold_a_set_and_mirror_the_legacy_column(db) -> None:
    factory, _settings = db
    with factory() as session:
        view = _service(session).update(
            AutonomousTradingSettingsUpdate(
                strategy_ids=[SHORT, LONG, SHORT], symbol="BTCUSDT", max_trades_per_day=3
            ),
            organization_id=ORG,
            user_id=OWNER,
        )
        session.commit()
        assert view.strategy_ids == [SHORT, LONG]
        assert view.strategy_scope == "selected"
        # Old readers still see one strategy in the legacy column.
        assert view.strategy_id == SHORT
        row = session.scalar(select(AutonomousTradingSettingsRow))
        assert row is not None and row.strategy_id == SHORT
        resolved = _service(session).resolve(organization_id=ORG, user_id=OWNER)
        assert resolved.strategy_ids == frozenset({LONG, SHORT})
        assert resolved.strategy_scope == "selected"

        every = _service(session).update(
            AutonomousTradingSettingsUpdate(strategy_scope="all_eligible"),
            organization_id=ORG,
            user_id=OWNER,
        )
        session.commit()
        assert every.strategy_scope == "all_eligible"
        assert every.strategy_ids == [SHORT, LONG]


def test_legacy_single_strategy_payload_and_row_still_work(db) -> None:
    factory, _settings = db
    with factory() as session:
        view = _service(session).update(
            AutonomousTradingSettingsUpdate(strategy_id=LONG, symbol="BTCUSDT"),
            organization_id=ORG,
            user_id=OWNER,
        )
        session.commit()
        assert view.strategy_ids == [LONG]
        assert view.strategy_id == LONG
        # A row written before the migration (no set yet) reads as its one strategy.
        row = session.scalar(select(AutonomousTradingSettingsRow))
        assert row is not None
        row.strategy_ids = None
        session.commit()
        resolved = _service(session).resolve(organization_id=ORG, user_id=OWNER)
        assert resolved.strategy_ids == frozenset({LONG})
        cleared = _service(session).update(
            AutonomousTradingSettingsUpdate(strategy_id=None),
            organization_id=ORG,
            user_id=OWNER,
        )
        assert cleared.strategy_ids == []
        assert cleared.strategy_id is None


def test_options_report_approval_and_eligibility(db) -> None:
    factory, _settings = db
    with factory() as session:
        view = _service(session).get(organization_id=ORG, user_id=OWNER)
    options = {option.strategy_id: option for option in view.strategy_options}
    assert DISABLED not in options
    assert options[LONG].approved and options[LONG].paper_eligible
    assert options[SHORT].approved and options[SHORT].paper_eligible
    assert not options[DRAFT].approved
    assert options[NOT_ELIGIBLE].approved and not options[NOT_ELIGIBLE].paper_eligible


def _token(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/auth/login", json={"email": "owner-466@test.example", "password": "TestPassword123!"}
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}


def test_api_accepts_a_set_and_rejects_bad_selections(db) -> None:
    factory, settings = db
    app = create_app(settings)

    def _override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override
    client = TestClient(app)
    headers = _token(client)
    saved = client.patch(
        "/autonomous/settings",
        headers=headers,
        json={"strategy_ids": [str(LONG), str(SHORT)], "symbol": "BTCUSDT"},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["strategy_ids"] == [str(LONG), str(SHORT)]
    assert body["strategy_scope"] == "selected"

    unknown = client.patch(
        "/autonomous/settings", headers=headers, json={"strategy_ids": [str(uuid4())]}
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"] == "strategy_not_available"
    disabled = client.patch(
        "/autonomous/settings", headers=headers, json={"strategy_ids": [str(DISABLED)]}
    )
    assert disabled.status_code == 422
    both = client.patch(
        "/autonomous/settings",
        headers=headers,
        json={"strategy_id": str(LONG), "strategy_ids": [str(SHORT)]},
    )
    assert both.status_code == 422
    assert both.json()["detail"] == "strategy_selection_conflict"
    bad_scope = client.patch(
        "/autonomous/settings", headers=headers, json={"strategy_scope": "everything"}
    )
    assert bad_scope.status_code == 422
    after = client.get("/autonomous/settings", headers=headers).json()
    assert after["strategy_ids"] == [str(LONG), str(SHORT)]


# --------------------------------------------------------------------------- #
# Loop: guard wiring, kill switch, per-coin rule
# --------------------------------------------------------------------------- #


class _ProbeLoop(AutomatedPaperLoop):
    def __init__(self, settings: object) -> None:
        super().__init__(
            SimpleNamespace(bind_session=lambda _session: nullcontext()),  # type: ignore[arg-type]
            settings,  # type: ignore[arg-type]
            FrozenClock(datetime(2026, 11, 19, tzinfo=UTC)),
        )
        self.bound_calls = 0

    def _preface(self, **_kwargs: object) -> None:
        return None

    def _account(self, _session: object, _target: object) -> SimpleNamespace:
        return SimpleNamespace(id=uuid4())

    def _bound(self, *_args: object, **_kwargs: object) -> AutomatedPaperLoopProof:
        self.bound_calls += 1
        return AutomatedPaperLoopProof(stage="opened", reason_code="probe_reached_bound")


def _probe(
    factory: sessionmaker[Session],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    target: PaperScanTarget,
    *,
    direction: TradeDirection = TradeDirection.LONG,
    kill_switch_active: bool = False,
    loop_class: type[_ProbeLoop] = _ProbeLoop,
) -> AutomatedPaperLoopProof:
    class _Accounting:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def sync_from_portfolio(self, **_kwargs: object) -> AuthoritativeDailySnapshot:
            return _snapshot()

    monkeypatch.setattr("app.services.risk.daily_risk_accounting.DailyRiskAccounting", _Accounting)
    loop = loop_class(settings)
    with factory() as session:
        return loop.continue_confirmed_setup(
            session,
            target=target,
            candidate=SimpleNamespace(candidate_id=uuid4(), direction=direction),  # type: ignore[arg-type]
            assessment=None,  # type: ignore[arg-type]
            window=None,  # type: ignore[arg-type]
            assembled=None,  # type: ignore[arg-type]
            policy=None,  # type: ignore[arg-type]
            kill_switch_active=kill_switch_active,
        )


def test_loop_takes_long_and_short_from_listed_strategies(db, monkeypatch) -> None:
    factory, settings = db
    with factory() as session:
        _service(session).update(
            AutonomousTradingSettingsUpdate(strategy_ids=[LONG, SHORT], symbol="BTCUSDT"),
            organization_id=ORG,
            user_id=OWNER,
        )
        session.commit()
    long = _probe(factory, settings, monkeypatch, _target(LONG), direction=TradeDirection.LONG)
    short = _probe(factory, settings, monkeypatch, _target(SHORT), direction=TradeDirection.SHORT)
    assert long.reason_code == "probe_reached_bound"
    assert short.reason_code == "probe_reached_bound"
    assert (
        _probe(factory, settings, monkeypatch, _target(NOT_ELIGIBLE)).reason_code
        == "autonomous_strategy_mismatch"
    )
    killed = _probe(factory, settings, monkeypatch, _target(LONG), kill_switch_active=True)
    assert killed.reason_code == "kill_switch_active"


def test_loop_all_eligible_mode_refuses_draft_and_unproven(db, monkeypatch) -> None:
    factory, settings = db
    with factory() as session:
        _service(session).update(
            AutonomousTradingSettingsUpdate(strategy_scope="all_eligible", symbol="BTCUSDT"),
            organization_id=ORG,
            user_id=OWNER,
        )
        session.commit()
    assert _probe(factory, settings, monkeypatch, _target(SHORT)).reason_code == (
        "probe_reached_bound"
    )
    assert _probe(factory, settings, monkeypatch, _target(DRAFT)).reason_code == (
        "autonomous_strategy_not_approved"
    )
    assert _probe(factory, settings, monkeypatch, _target(NOT_ELIGIBLE)).reason_code == (
        "autonomous_strategy_not_paper_eligible"
    )
    assert _probe(factory, settings, monkeypatch, _target(DISABLED)).reason_code == (
        "autonomous_strategy_disabled"
    )


class _DemoProbeLoop(_ProbeLoop):
    def _risk_policy_kind(self) -> str:
        return "demo"


@pytest.mark.parametrize("scope", ["selected", "all_eligible"])
def test_demo_loop_takes_long_and_short_from_non_paper_eligible_strategy(
    db, monkeypatch, scope: str
) -> None:
    factory, settings = db
    with factory() as session:
        _service(session).update(
            AutonomousTradingSettingsUpdate(
                strategy_ids=[NOT_ELIGIBLE], strategy_scope=scope, symbol="BTCUSDT"
            ),
            organization_id=ORG,
            user_id=OWNER,
        )
        session.commit()
    for direction in (TradeDirection.LONG, TradeDirection.SHORT):
        demo = _probe(
            factory,
            settings,
            monkeypatch,
            _target(NOT_ELIGIBLE),
            direction=direction,
            loop_class=_DemoProbeLoop,
        )
        assert demo.reason_code == "probe_reached_bound", direction
        # The internal paper loop keeps the paper-eligibility gate.
        paper = _probe(factory, settings, monkeypatch, _target(NOT_ELIGIBLE), direction=direction)
        assert paper.reason_code == "autonomous_strategy_not_paper_eligible", direction
    # Demo still refuses an unlisted or unapproved strategy.
    draft = _probe(factory, settings, monkeypatch, _target(DRAFT), loop_class=_DemoProbeLoop)
    expected = "mismatch" if scope == "selected" else "not_approved"
    assert draft.reason_code == f"autonomous_strategy_{expected}"


def _open_trade(
    session: Session,
    *,
    strategy_id: uuid.UUID,
    symbol: str = "BTCUSDT",
    status: JournalTradeStatus = JournalTradeStatus.OPEN,
    source: JournalTradeSource = JournalTradeSource.PAPER_EXECUTION,
    candidate_id: uuid.UUID | None = None,
) -> JournalTrade:
    trade = JournalTrade(
        organization_id=ORG,
        user_id=OWNER,
        source=source,
        status=status,
        symbol=symbol,
        timeframe="15m",
        direction=TradeDirection.LONG,
        user_strategy_id=strategy_id,
        candidate_id=candidate_id or uuid4(),
    )
    session.add(trade)
    session.flush()
    return trade


def test_first_intent_on_a_coin_wins_and_the_second_is_refused(db) -> None:
    factory, _settings = db
    with factory() as session:
        assert open_symbol_position_refusal(session, _target(SHORT), candidate_id=uuid4()) is None
        first = _open_trade(session, strategy_id=LONG)
        # The second strategy on the same coin, either direction, is refused.
        assert (
            open_symbol_position_refusal(session, _target(SHORT), candidate_id=uuid4())
            == "autonomous_symbol_position_open"
        )
        # So is the same strategy firing again on a new candidate.
        assert (
            open_symbol_position_refusal(session, _target(LONG), candidate_id=uuid4())
            == "autonomous_symbol_position_open"
        )
        # The first intent replaying itself is idempotent, not refused.
        assert (
            open_symbol_position_refusal(
                session, _target(LONG), candidate_id=first.candidate_id or uuid4()
            )
            is None
        )
        # Another coin is independent.
        assert (
            open_symbol_position_refusal(
                session, _target(SHORT, symbol="ETHUSDT"), candidate_id=uuid4()
            )
            is None
        )
        first.status = JournalTradeStatus.CLOSED
        session.flush()
        assert open_symbol_position_refusal(session, _target(SHORT), candidate_id=uuid4()) is None
        # A demo position on the coin counts too; a backtest row does not.
        _open_trade(session, strategy_id=LONG, source=JournalTradeSource.BACKTEST)
        assert open_symbol_position_refusal(session, _target(SHORT), candidate_id=uuid4()) is None
        _open_trade(session, strategy_id=LONG, source=JournalTradeSource.MANUAL_DEMO_TEST)
        assert (
            open_symbol_position_refusal(session, _target(SHORT), candidate_id=uuid4())
            == "autonomous_symbol_position_open"
        )


def test_bound_refuses_a_new_intent_on_an_open_coin_before_any_plan(db) -> None:
    factory, settings = db
    plans: list[object] = []

    class _Store:
        def get_by_candidate_scope(self, **_kwargs: object) -> None:
            return None

    class _Loop(AutomatedPaperLoop):
        def _eligibility_service(self) -> object:
            plans.append("eligibility")
            raise AssertionError("a refused second intent must not reach eligibility")

    loop = _Loop(
        SimpleNamespace(plan_store=_Store()),  # type: ignore[arg-type]
        settings,
        FrozenClock(datetime(2026, 11, 19, tzinfo=UTC)),
    )
    with factory() as session:
        _open_trade(session, strategy_id=LONG)
        proof = loop._bound(
            session,
            target=_target(SHORT),
            candidate=SimpleNamespace(candidate_id=uuid4(), direction=TradeDirection.SHORT),  # type: ignore[arg-type]
            assessment=None,  # type: ignore[arg-type]
            window=None,  # type: ignore[arg-type]
            assembled=None,  # type: ignore[arg-type]
            policy=None,  # type: ignore[arg-type]
            account=SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
        )
    assert proof.stage == "blocked"
    assert proof.reason_code == "autonomous_symbol_position_open"
    assert plans == []


def test_bound_replays_an_existing_plan_even_when_the_coin_is_open(db) -> None:
    factory, settings = db
    replayed: list[object] = []
    candidate_id = uuid4()

    class _Store:
        def get_by_candidate_scope(self, **_kwargs: object) -> object:
            return SimpleNamespace(
                plan=SimpleNamespace(execution_policy_version=_policy, revision_id=uuid4())
            )

    class _Loop(AutomatedPaperLoop):
        def _execute_existing(self, *_args: object, **_kwargs: object) -> AutomatedPaperLoopProof:
            replayed.append(candidate_id)
            return AutomatedPaperLoopProof(
                stage="filled", reason_code="open_journal", replayed=True
            )

    loop = _Loop(
        SimpleNamespace(plan_store=_Store()),  # type: ignore[arg-type]
        settings,
        FrozenClock(datetime(2026, 11, 19, tzinfo=UTC)),
    )
    _policy = loop._policy_version()
    with factory() as session:
        _open_trade(session, strategy_id=LONG, candidate_id=candidate_id)
        proof = loop._bound(
            session,
            target=_target(LONG),
            candidate=SimpleNamespace(candidate_id=candidate_id, direction=TradeDirection.LONG),  # type: ignore[arg-type]
            assessment=None,  # type: ignore[arg-type]
            window=None,  # type: ignore[arg-type]
            assembled=SimpleNamespace(trigger_bar=SimpleNamespace(interval_end=None)),  # type: ignore[arg-type]
            policy=None,  # type: ignore[arg-type]
            account=SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
        )
    assert proof.replayed is True
    assert replayed == [candidate_id]


def test_resolved_settings_are_immutable_sets() -> None:
    resolved = _resolved()
    assert isinstance(resolved.strategy_ids, frozenset)
    with pytest.raises(AttributeError):
        resolved.strategy_ids = frozenset()  # type: ignore[misc]
    assert replace(resolved, strategy_scope="all_eligible").strategy_scope == "all_eligible"


def test_strategy_stats_mark_every_selected_strategy(db) -> None:
    from app.services.autonomous_strategy_stats import AutonomousStrategyStatsService

    factory, _settings = db
    with factory() as session:
        _service(session).update(
            AutonomousTradingSettingsUpdate(strategy_ids=[LONG, SHORT], symbol="BTCUSDT"),
            organization_id=ORG,
            user_id=OWNER,
        )
        session.commit()
        view = AutonomousStrategyStatsService(session).view(organization_id=ORG, user_id=OWNER)
    selected = {row.strategy_id for row in view.strategies if row.bot_selected}
    assert selected == {LONG, SHORT}
