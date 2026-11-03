"""GET /autonomous/strategy-stats: journal trades and stats per strategy (read-only)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
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
    SetupDetectionRecord,
    User,
    UserStrategy,
)
from app.main import create_app
from app.schemas.common import (
    JournalTradeSource,
    JournalTradeStatus,
    MembershipRole,
    StrategyId,
    TradeDirection,
    TradeResult,
)
from app.security.passwords import hash_password
from app.services.autonomous_strategy_stats import (
    COUNTED_SOURCES,
    EXCLUDED_SOURCES,
    AutonomousStrategyStatsService,
)
from tests.support.postgres_persistence import (
    phase7_plan_session_factory,
    postgres_available,
)

ORG = uuid.UUID("00000000-0000-0000-0000-00000000c5a0")
OTHER_ORG = uuid.UUID("00000000-0000-0000-0000-00000000c5a9")
OWNER = uuid.UUID("00000000-0000-0000-0000-00000000c5a1")
TRADER = uuid.UUID("00000000-0000-0000-0000-00000000c5a2")
OUTSIDER = uuid.UUID("00000000-0000-0000-0000-00000000c5a8")
NESTED = uuid.UUID("00000000-0000-0000-0000-00000000c5b1")
SFP = uuid.UUID("00000000-0000-0000-0000-00000000c5b2")
PAUSED = uuid.UUID("00000000-0000-0000-0000-00000000c5b3")
T0 = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
OWNER_EMAIL = "owner-c5a@test.example"
TRADER_EMAIL = "trader-c5a@test.example"


def _sqlite_factory() -> sessionmaker[Session]:
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
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(
                not postgres_available(), reason="PostgreSQL fixture database not reachable"
            ),
        ),
    ]
)
def db(request: pytest.FixtureRequest) -> Iterator[tuple[sessionmaker[Session], Settings]]:
    factory = _sqlite_factory() if request.param == "sqlite" else phase7_plan_session_factory()
    settings = Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="autonomous-strategy-stats-test-secret-32",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
    )
    with factory() as session:
        session.add(Organization(id=ORG, name="Stats Org"))
        session.add(Organization(id=OTHER_ORG, name="Other Org"))
        for user_id, email in (
            (OWNER, OWNER_EMAIL),
            (TRADER, TRADER_EMAIL),
            (OUTSIDER, "outsider-c5a@test.example"),
        ):
            session.add(
                User(
                    id=user_id,
                    email=email,
                    hashed_password=hash_password("TestPassword123!", settings),
                    timezone="UTC",
                )
            )
        session.flush()
        session.add(Membership(user_id=OWNER, organization_id=ORG, role=MembershipRole.OWNER))
        session.add(Membership(user_id=TRADER, organization_id=ORG, role=MembershipRole.TRADER))
        session.add(
            Membership(user_id=OUTSIDER, organization_id=OTHER_ORG, role=MembershipRole.OWNER)
        )
        session.commit()
    yield factory, settings
    factory.kw["bind"].dispose()


@pytest.fixture
def client(db: tuple[sessionmaker[Session], Settings]) -> TestClient:
    factory, settings = db
    app = create_app(settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    return TestClient(app)


def _token(client: TestClient, email: str) -> dict[str, str]:
    login = client.post("/auth/login", json={"email": email, "password": "TestPassword123!"})
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}


def _strategy(
    strategy_id: uuid.UUID, name: str, setup: StrategyId, *, enabled: bool = True
) -> UserStrategy:
    return UserStrategy(
        id=strategy_id,
        organization_id=ORG,
        user_id=OWNER,
        name=name,
        setup_type=setup,
        enabled=enabled,
    )


def _trade(
    *,
    strategy_id: uuid.UUID | None,
    status: JournalTradeStatus,
    result: TradeResult = TradeResult.OPEN,
    net_pnl: str | None = None,
    at: datetime = T0,
    source: JournalTradeSource = JournalTradeSource.PAPER_EXECUTION,
    user_id: uuid.UUID = OWNER,
    organization_id: uuid.UUID = ORG,
) -> JournalTrade:
    closed = status is JournalTradeStatus.CLOSED
    return JournalTrade(
        organization_id=organization_id,
        user_id=user_id,
        source=source,
        status=status,
        symbol="BTCUSDT",
        timeframe="15m",
        direction=TradeDirection.LONG,
        user_strategy_id=strategy_id,
        entry_time=at,
        exit_time=at + timedelta(hours=1) if closed else None,
        result=result,
        net_pnl=Decimal(net_pnl) if net_pnl is not None else None,
    )


def _at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    # SQLite returns naive timestamps; they were written as UTC.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def test_empty_view_lists_no_strategies_and_zero_totals(client: TestClient) -> None:
    resp = client.get("/autonomous/strategy-stats", headers=_token(client, OWNER_EMAIL))
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategies"] == []
    assert body["unattributed"]["closed_trades"] == 0
    assert body["unattributed"]["win_rate"] is None
    assert body["excluded_sources"] == ["backtest"]
    assert "backtest" not in body["counted_sources"]
    assert "paper_execution" in body["counted_sources"]


def test_groups_trades_by_strategy_with_win_rate_pnl_and_open_positions(
    client: TestClient, db: tuple[sessionmaker[Session], Settings]
) -> None:
    factory, _ = db
    with factory() as session:
        session.add_all(
            [
                _strategy(NESTED, "Nested BTC", StrategyId.NESTED_CONTINUATION),
                _strategy(SFP, "SFP ETH", StrategyId.SFP),
                _strategy(PAUSED, "Paused pullback", StrategyId.HTF_TREND_PULLBACK, enabled=False),
            ]
        )
        session.flush()
        session.add(
            AutonomousTradingSettingsRow(
                organization_id=ORG,
                user_id=OWNER,
                daily_loss_limit_percent=Decimal("3"),
                max_trades_per_day=5,
                strategy_id=SFP,
            )
        )
        session.add_all(
            [
                _trade(
                    strategy_id=NESTED,
                    status=JournalTradeStatus.CLOSED,
                    result=TradeResult.WIN,
                    net_pnl="120.50",
                ),
                _trade(
                    strategy_id=NESTED,
                    status=JournalTradeStatus.CLOSED,
                    result=TradeResult.LOSS,
                    net_pnl="-40.25",
                    at=T0 + timedelta(hours=2),
                ),
                _trade(
                    strategy_id=NESTED,
                    status=JournalTradeStatus.CLOSED,
                    result=TradeResult.WIN,
                    net_pnl="10",
                    at=T0 + timedelta(hours=4),
                ),
                _trade(
                    strategy_id=NESTED,
                    status=JournalTradeStatus.OPEN,
                    at=T0 + timedelta(hours=6),
                ),
                _trade(strategy_id=SFP, status=JournalTradeStatus.OPEN),
                _trade(
                    strategy_id=None,
                    status=JournalTradeStatus.CLOSED,
                    result=TradeResult.LOSS,
                    net_pnl="-5",
                ),
                # Excluded: backtest source, another user, planned/cancelled rows.
                _trade(
                    strategy_id=NESTED,
                    status=JournalTradeStatus.CLOSED,
                    result=TradeResult.WIN,
                    net_pnl="9999",
                    source=JournalTradeSource.BACKTEST,
                ),
                _trade(
                    strategy_id=NESTED,
                    status=JournalTradeStatus.CLOSED,
                    result=TradeResult.WIN,
                    net_pnl="7777",
                    user_id=TRADER,
                ),
                _trade(strategy_id=NESTED, status=JournalTradeStatus.CANCELLED),
            ]
        )
        session.add(
            SetupDetectionRecord(
                organization_id=ORG,
                symbol="BTCUSDT",
                timeframe="15m",
                setup_name=StrategyId.NESTED_CONTINUATION.value,
                detected_at=T0 + timedelta(minutes=30),
            )
        )
        session.add(
            SetupDetectionRecord(
                organization_id=OTHER_ORG,
                symbol="BTCUSDT",
                timeframe="15m",
                setup_name=StrategyId.SFP.value,
                detected_at=T0,
            )
        )
        session.commit()

    resp = client.get("/autonomous/strategy-stats", headers=_token(client, OWNER_EMAIL))
    assert resp.status_code == 200
    body = resp.json()
    by_name = {row["name"]: row for row in body["strategies"]}
    # Enabled strategies first, then by name.
    assert [row["name"] for row in body["strategies"]] == [
        "Nested BTC",
        "SFP ETH",
        "Paused pullback",
    ]

    nested = by_name["Nested BTC"]
    assert nested["enabled"] is True
    assert nested["bot_selected"] is False
    assert nested["setup_type"] == "nested_continuation"
    assert _at(nested["last_setup_at"]) == T0 + timedelta(minutes=30)
    assert nested["stats"]["closed_trades"] == 3
    assert nested["stats"]["wins"] == 2
    assert nested["stats"]["losses"] == 1
    assert nested["stats"]["win_rate"] == pytest.approx(0.6667)
    assert Decimal(nested["stats"]["net_pnl"]) == Decimal("90.25")
    assert nested["stats"]["open_positions"] == 1
    assert _at(nested["stats"]["last_trade_at"]) == T0 + timedelta(hours=6)

    sfp = by_name["SFP ETH"]
    assert sfp["bot_selected"] is True
    assert sfp["last_setup_at"] is None  # the other org's detection is not visible
    assert sfp["stats"]["closed_trades"] == 0
    assert sfp["stats"]["win_rate"] is None
    assert sfp["stats"]["open_positions"] == 1

    paused = by_name["Paused pullback"]
    assert paused["enabled"] is False
    assert paused["stats"]["closed_trades"] == 0

    assert body["unattributed"]["closed_trades"] == 1
    assert Decimal(body["unattributed"]["net_pnl"]) == Decimal("-5")


def test_other_users_see_only_their_own_strategies(
    client: TestClient, db: tuple[sessionmaker[Session], Settings]
) -> None:
    factory, _ = db
    with factory() as session:
        session.add(_strategy(NESTED, "Nested BTC", StrategyId.NESTED_CONTINUATION))
        session.flush()
        session.add(
            _trade(
                strategy_id=NESTED,
                status=JournalTradeStatus.CLOSED,
                result=TradeResult.WIN,
                net_pnl="1",
            )
        )
        session.commit()
    for email in (TRADER_EMAIL, "outsider-c5a@test.example"):
        resp = client.get("/autonomous/strategy-stats", headers=_token(client, email))
        assert resp.status_code == 200
        assert resp.json()["strategies"] == []
        assert resp.json()["unattributed"]["closed_trades"] == 0


def test_requires_authentication(client: TestClient) -> None:
    assert client.get("/autonomous/strategy-stats").status_code == 401


def test_strategy_stats_is_read_only(client: TestClient) -> None:
    headers = _token(client, OWNER_EMAIL)
    for method in ("post", "patch", "put", "delete"):
        resp = client.request(method, "/autonomous/strategy-stats", headers=headers)
        assert resp.status_code == 405


FOREIGN = uuid.UUID("00000000-0000-0000-0000-00000000c5b4")
OWNER_OTHER_ORG_STRATEGY = uuid.UUID("00000000-0000-0000-0000-00000000c5b5")


def test_trade_sources_are_an_explicit_allowlist() -> None:
    # A new JournalTradeSource must be classified on purpose; it is not counted by default.
    assert set(COUNTED_SOURCES) | set(EXCLUDED_SOURCES) == set(JournalTradeSource)
    assert not set(COUNTED_SOURCES) & set(EXCLUDED_SOURCES)
    assert JournalTradeSource.BACKTEST in EXCLUDED_SOURCES


def test_same_user_trades_tagged_with_another_org_are_not_counted(
    client: TestClient, db: tuple[sessionmaker[Session], Settings]
) -> None:
    """B1: guards the JournalTrade.organization_id filter."""
    factory, _ = db
    with factory() as session:
        session.add(_strategy(NESTED, "Nested BTC", StrategyId.NESTED_CONTINUATION))
        session.flush()
        session.add_all(
            [
                _trade(
                    strategy_id=NESTED,
                    status=JournalTradeStatus.CLOSED,
                    result=TradeResult.WIN,
                    net_pnl="10",
                ),
                # Same user and strategy link, but the other org: must not leak in.
                _trade(
                    strategy_id=NESTED,
                    status=JournalTradeStatus.CLOSED,
                    result=TradeResult.LOSS,
                    net_pnl="-777",
                    organization_id=OTHER_ORG,
                ),
                _trade(
                    strategy_id=None,
                    status=JournalTradeStatus.CLOSED,
                    result=TradeResult.LOSS,
                    net_pnl="-555",
                    organization_id=OTHER_ORG,
                ),
            ]
        )
        session.commit()
    body = client.get("/autonomous/strategy-stats", headers=_token(client, OWNER_EMAIL)).json()
    (row,) = body["strategies"]
    assert row["stats"]["closed_trades"] == 1
    assert Decimal(row["stats"]["net_pnl"]) == Decimal("10")
    assert body["unattributed"]["closed_trades"] == 0


def test_rows_linked_to_unowned_strategy_show_as_unattributed(
    client: TestClient, db: tuple[sessionmaker[Session], Settings]
) -> None:
    factory, _ = db
    with factory() as session:
        session.add(
            UserStrategy(
                id=FOREIGN,
                organization_id=ORG,
                user_id=TRADER,
                name="Trader SFP",
                setup_type=StrategyId.SFP,
                enabled=True,
            )
        )
        session.flush()
        session.add(
            _trade(
                strategy_id=FOREIGN,
                status=JournalTradeStatus.CLOSED,
                result=TradeResult.LOSS,
                net_pnl="-12.5",
            )
        )
        session.commit()
    body = client.get("/autonomous/strategy-stats", headers=_token(client, OWNER_EMAIL)).json()
    assert body["strategies"] == []
    assert body["unattributed"]["closed_trades"] == 1
    assert Decimal(body["unattributed"]["net_pnl"]) == Decimal("-12.5")


def test_user_in_two_orgs_sees_only_this_orgs_strategies_and_selection(
    db: tuple[sessionmaker[Session], Settings],
) -> None:
    """B2: guards the UserStrategy and AutonomousTradingSettingsRow org filters."""
    factory, _ = db
    with factory() as session:
        session.add(Membership(user_id=OWNER, organization_id=OTHER_ORG, role=MembershipRole.OWNER))
        session.add(_strategy(NESTED, "Nested BTC", StrategyId.NESTED_CONTINUATION))
        session.add(
            UserStrategy(
                id=OWNER_OTHER_ORG_STRATEGY,
                organization_id=OTHER_ORG,
                user_id=OWNER,
                name="Other-org SFP",
                setup_type=StrategyId.SFP,
                enabled=True,
            )
        )
        session.flush()
        # Only the other org has a settings row, and it points at NESTED.
        session.add(
            AutonomousTradingSettingsRow(
                organization_id=OTHER_ORG,
                user_id=OWNER,
                daily_loss_limit_percent=Decimal("3"),
                max_trades_per_day=5,
                strategy_id=NESTED,
            )
        )
        session.commit()
    with factory() as session:
        view = AutonomousStrategyStatsService(session).view(organization_id=ORG, user_id=OWNER)
        assert [row.name for row in view.strategies] == ["Nested BTC"]
        assert view.strategies[0].bot_selected is False
        other = AutonomousStrategyStatsService(session).view(
            organization_id=OTHER_ORG, user_id=OWNER
        )
        assert [row.name for row in other.strategies] == ["Other-org SFP"]
        assert other.strategies[0].bot_selected is False
