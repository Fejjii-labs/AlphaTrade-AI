"""GET /autonomous/activity: today's bot-cap count and the paper loop's last actions."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.dependencies import get_session
from app.db.base import Base
from app.db.models import DailyRiskState, JournalTrade, Membership, Organization, User
from app.main import create_app
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    ActorType,
    AuditEventType,
    AuditResult,
    JournalTradeSource,
    JournalTradeStatus,
    MembershipRole,
    TradeDirection,
)
from app.security.passwords import hash_password
from app.services.audit_service import AuditService
from app.services.autonomous_activity import PAPER_LOOP_RESOURCE, AutonomousActivityService
from app.services.risk.daily_risk_accounting import DailyRiskAccounting

ORG = uuid.UUID("00000000-0000-0000-0000-000000000b3b")
OWNER = uuid.UUID("00000000-0000-0000-0000-000000000b30")
TRADER = uuid.UUID("00000000-0000-0000-0000-000000000b31")
TRADE = uuid.UUID("00000000-0000-0000-0000-000000000b32")
T0 = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)


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
        jwt_secret="autonomous-activity-test-secret-key-32",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
    )
    with factory() as session:
        session.add(Organization(id=ORG, name="Activity Org"))
        for user_id, email in (
            (OWNER, "owner-b3b@test.example"),
            (TRADER, "trader-b3b@test.example"),
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
        session.commit()
    yield factory, settings
    engine.dispose()


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


def _loop_event(
    session: Session,
    *,
    event_type: AuditEventType,
    at: datetime,
    metadata: dict[str, object],
    user_id: uuid.UUID = OWNER,
    resource_type: str = PAPER_LOOP_RESOURCE,
) -> None:
    AuditService(session).record(
        AuditRecordCreate(
            request_id=f"paper-loop:{uuid.uuid4()}",
            trace_id="paper-loop:test",
            event_type=event_type,
            resource_type=resource_type,
            actor_type=ActorType.SYSTEM,
            action=event_type.value,
            result=AuditResult.SUCCESS
            if event_type is AuditEventType.JOURNAL_TRADE_CREATED
            else AuditResult.BLOCKED,
            user_id=user_id,
            organization_id=ORG,
            metadata=metadata,
            timestamp=at,
        )
    )


def test_empty_activity_reports_zero_trades_and_no_actions(client: TestClient) -> None:
    resp = client.get("/autonomous/activity", headers=_token(client, "owner-b3b@test.example"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["timezone"] == "UTC"
    assert body["trades_today"] == 0
    assert body["recent_actions"] == []


def test_lists_only_this_users_paper_loop_actions_newest_first(
    client: TestClient, db: tuple[sessionmaker[Session], Settings]
) -> None:
    factory, _ = db
    with factory() as session:
        session.add(
            JournalTrade(
                id=TRADE,
                organization_id=ORG,
                user_id=OWNER,
                source=JournalTradeSource.PAPER_EXECUTION,
                status=JournalTradeStatus.OPEN,
                symbol="BTCUSDT",
                timeframe="15m",
                direction=TradeDirection.LONG,
                entry_time=T0,
            )
        )
        session.flush()
        _loop_event(
            session,
            event_type=AuditEventType.RISK_BLOCK,
            at=T0,
            metadata={"candidate_id": str(uuid.uuid4()), "eligibility_state": "NOT_ELIGIBLE_RISK"},
        )
        _loop_event(
            session,
            event_type=AuditEventType.JOURNAL_TRADE_CREATED,
            at=T0 + timedelta(minutes=5),
            metadata={"journal_trade_id": str(TRADE)},
        )
        _loop_event(
            session,
            event_type=AuditEventType.PAPER_ORDER_REJECTED,
            at=T0 + timedelta(minutes=10),
            metadata={"reason_code": "stop_too_wide"},
        )
        # Not the paper loop, another user, and an unrelated event type: all excluded.
        _loop_event(
            session,
            event_type=AuditEventType.RISK_BLOCK,
            at=T0 + timedelta(minutes=11),
            metadata={},
            resource_type="manual_demo",
        )
        _loop_event(
            session,
            event_type=AuditEventType.RISK_BLOCK,
            at=T0 + timedelta(minutes=12),
            metadata={},
            user_id=TRADER,
        )
        _loop_event(
            session,
            event_type=AuditEventType.RISK_SETTINGS_UPDATED,
            at=T0 + timedelta(minutes=13),
            metadata={},
        )
        session.commit()

    body = client.get(
        "/autonomous/activity", headers=_token(client, "owner-b3b@test.example")
    ).json()
    actions = body["recent_actions"]
    assert [a["outcome"] for a in actions] == ["rejected", "entered", "blocked"]
    assert actions[0]["reason"] == "stop_too_wide"
    assert actions[1] == {
        "at": actions[1]["at"],
        "outcome": "entered",
        "symbol": "BTCUSDT",
        "direction": "long",
        "reason": None,
        "journal_trade_id": str(TRADE),
    }
    assert actions[2]["reason"] == "NOT_ELIGIBLE_RISK"
    assert actions[2]["symbol"] is None


def test_caps_the_list_at_five(
    client: TestClient, db: tuple[sessionmaker[Session], Settings]
) -> None:
    factory, _ = db
    with factory() as session:
        for minute in range(7):
            _loop_event(
                session,
                event_type=AuditEventType.RISK_BLOCK,
                at=T0 + timedelta(minutes=minute),
                metadata={"eligibility_state": f"state_{minute}"},
            )
        session.commit()
    actions = client.get(
        "/autonomous/activity", headers=_token(client, "owner-b3b@test.example")
    ).json()["recent_actions"]
    assert [a["reason"] for a in actions] == [f"state_{m}" for m in (6, 5, 4, 3, 2)]


def test_reading_never_writes_daily_risk_state(
    client: TestClient, db: tuple[sessionmaker[Session], Settings]
) -> None:
    factory, _ = db
    resp = client.get("/autonomous/activity", headers=_token(client, "owner-b3b@test.example"))
    assert resp.status_code == 200
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DailyRiskState)) == 0


def test_count_is_the_bot_cap_snapshot_and_rolled_back(
    db: tuple[sessionmaker[Session], Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, _ = db
    original = DailyRiskAccounting.sync_from_portfolio

    def counted(self: DailyRiskAccounting, **kwargs: object):  # type: ignore[no-untyped-def]
        snapshot = original(self, **kwargs)  # type: ignore[arg-type]
        return type(snapshot)(**{**snapshot.__dict__, "trade_count": 2})

    monkeypatch.setattr(DailyRiskAccounting, "sync_from_portfolio", counted)
    with factory() as session:
        view = AutonomousActivityService(session).view(organization_id=ORG, user_id=OWNER)
        assert view.trades_today == 2
        session.commit()
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DailyRiskState)) == 0


def test_unreadable_count_is_reported_as_unknown(
    db: tuple[sessionmaker[Session], Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, _ = db

    def broken(self: DailyRiskAccounting, **kwargs: object):  # type: ignore[no-untyped-def]
        raise OperationalError("select", {}, Exception("db down"))

    monkeypatch.setattr(DailyRiskAccounting, "sync_from_portfolio", broken)
    with factory() as session:
        view = AutonomousActivityService(session).view(organization_id=ORG, user_id=OWNER)
    assert view.trades_today is None
    assert view.recent_actions == []


def test_trader_can_read_and_anonymous_cannot(client: TestClient) -> None:
    assert client.get("/autonomous/activity").status_code == 401
    resp = client.get("/autonomous/activity", headers=_token(client, "trader-b3b@test.example"))
    assert resp.status_code == 200
