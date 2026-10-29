"""API reads the worker-persisted monitor snapshot. Zero upstream I/O per request."""

from __future__ import annotations

import socket
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.market_monitor_snapshots import MarketMonitorSnapshotRow
from app.db.models import Membership, Organization, User
from app.db.session import get_session
from app.main import create_app
from app.market_monitor.backoff import BackoffPolicy
from app.market_monitor.monitor import PerpetualMarketMonitor
from app.market_monitor.persisted import (
    MarketMonitorSnapshotPublisher,
    PersistedMarketMonitorReader,
    api_monitor_runs_in_process,
    save_monitor_snapshot,
)
from app.market_monitor.types import (
    MarketAvailability,
    MonitorReason,
    SymbolMonitorSnapshot,
)
from app.schemas.common import MembershipRole
from app.security.passwords import hash_password
from app.security.rate_limit import reset_rate_limiter
from app.workers.watcher_market import SymbolMarketFactory
from tests.support.live_market_monitor import ScriptedPerpetualSource
from tests.support.phase5_market import consecutive_bars, trade

T0 = datetime(2026, 9, 21, 14, 0, 0, tzinfo=UTC)
ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000007101")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000007102")
EMAIL = "persisted-monitor@test.example"
PASSWORD = "TestPassword123!"

_LIVE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "persisted-monitor-secret-minimum-32b",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "market_data_cache_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
    "perpetual_evidence_source": "binance_usdm",
    "worker_enabled": False,
    "market_watcher_enabled": False,
    "watcher_orchestration_enabled": False,
    "alert_delivery_enabled": False,
    "telegram_alerts_enabled": False,
    "telegram_interaction_enabled": False,
    "automatic_telegram_delivery_enabled": False,
}


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


@pytest.fixture
def db() -> Iterator[sessionmaker[Session]]:
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
    with factory() as session:
        session.add(Organization(id=ORG_ID, name="Persisted Monitor Org"))
        session.add(
            User(
                id=USER_ID,
                email=EMAIL,
                hashed_password=hash_password(PASSWORD, Settings(**_LIVE)),
                email_verified=True,
            )
        )
        session.flush()
        session.add(Membership(organization_id=ORG_ID, user_id=USER_ID, role=MembershipRole.OWNER))
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _fresh_snapshot(now: datetime = T0) -> SymbolMonitorSnapshot:
    bars = consecutive_bars(2, last_open=now - timedelta(minutes=15))
    source = ScriptedPerpetualSource(bars_15m=bars)
    source.enqueue(
        [
            trade(
                sequence=10,
                price="101234.7",
                quantity="0.01",
                buyer_is_maker=False,
                event_time=now - timedelta(seconds=1),
                receive_at=now - timedelta(seconds=1),
            )
        ]
    )
    snapshot = _monitor(source).tick("BTCUSDT", now=now)
    assert snapshot.availability is MarketAvailability.FRESH
    return snapshot


def _monitor(source: ScriptedPerpetualSource) -> PerpetualMarketMonitor:
    return PerpetualMarketMonitor(
        source,
        replay=False,
        backoff=BackoffPolicy(initial_seconds=0.25, max_seconds=8.0, multiplier=2.0),
        poll_seconds=0.01,
    )


def _persist(factory: sessionmaker[Session], snapshot: SymbolMonitorSnapshot, at: datetime) -> None:
    with factory() as session, session.begin():
        save_monitor_snapshot(session, snapshot, worker_id="watcher-paper-test", written_at=at)


def _reader(
    factory: sessionmaker[Session], session: Session, now: datetime
) -> PersistedMarketMonitorReader:
    del factory
    return PersistedMarketMonitorReader(session, settings=Settings(**_LIVE), clock=lambda: now)


# --- reader semantics -------------------------------------------------------


def test_missing_row_is_unavailable_monitor_state_missing(db: sessionmaker[Session]) -> None:
    with db() as session:
        snap = _reader(db, session, T0).snapshot("BTCUSDT")
    assert snap.availability is MarketAvailability.UNAVAILABLE
    assert snap.reason is MonitorReason.MONITOR_STATE_MISSING
    assert snap.current_price is None
    assert snap.is_mock is False


def test_recent_row_is_served_fresh(db: sessionmaker[Session]) -> None:
    snapshot = _fresh_snapshot()
    _persist(db, snapshot, T0)
    with db() as session:
        served = _reader(db, session, T0 + timedelta(seconds=2)).snapshot("BTCUSDT")
    assert served.availability is MarketAvailability.FRESH
    assert served.current_price is not None
    assert served.current_price.usable_as_current_market_price is True
    assert served == snapshot


def test_recent_row_with_aged_quote_is_not_a_current_mark(db: sessionmaker[Session]) -> None:
    _persist(db, _fresh_snapshot(), T0)
    with db() as session:
        served = _reader(db, session, T0 + timedelta(seconds=30)).snapshot("BTCUSDT")
    assert served.current_price is not None
    assert served.current_price.usable_as_current_market_price is False
    assert served.current_price.presentation.value == "stale"


def test_old_row_is_stale_and_price_unusable(db: sessionmaker[Session]) -> None:
    _persist(db, _fresh_snapshot(), T0)
    with db() as session:
        served = _reader(db, session, T0 + timedelta(seconds=61)).snapshot("BTCUSDT")
    assert served.availability is MarketAvailability.STALE
    assert served.reason is MonitorReason.MONITOR_STATE_STALE
    assert served.current_price is None


def test_in_process_monitor_only_for_replay_or_local_opt_in() -> None:
    assert api_monitor_runs_in_process(Settings(**{**_LIVE, "perpetual_evidence_source": "replay"}))
    assert not api_monitor_runs_in_process(Settings(**_LIVE))
    assert api_monitor_runs_in_process(
        Settings(**{**_LIVE, "market_monitor_api_in_process_live": True})
    )


# --- worker side ------------------------------------------------------------


def test_worker_poll_persists_snapshot(
    db: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    bars = consecutive_bars(2, last_open=T0 - timedelta(minutes=15))
    source = ScriptedPerpetualSource(bars_15m=bars)
    source.enqueue(
        [
            trade(
                sequence=11,
                price="101000.1",
                quantity="0.01",
                buyer_is_maker=False,
                event_time=T0 - timedelta(seconds=1),
                receive_at=T0 - timedelta(seconds=1),
            )
        ]
    )
    monitor = _monitor(source)
    factory = SymbolMarketFactory(Settings(**_LIVE))
    monkeypatch.setattr(factory, "composition", lambda _symbol: (source, None, monitor, []))
    factory.snapshot_sink = MarketMonitorSnapshotPublisher(
        db, worker_id="watcher-paper-test", clock=lambda: T0
    )
    factory.poll_monitor("BTCUSDT")
    with db() as session:
        row = session.get(MarketMonitorSnapshotRow, "BTCUSDT")
        assert row is not None
        assert row.worker_id == "watcher-paper-test"
        stored = SymbolMonitorSnapshot.model_validate(row.payload)
    assert stored.symbol == "BTCUSDT"
    assert stored.provider_name == source.name


def test_worker_persist_failure_never_crashes_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    source = ScriptedPerpetualSource()
    monitor = _monitor(source)
    factory = SymbolMarketFactory(Settings(**_LIVE))
    monkeypatch.setattr(factory, "composition", lambda _symbol: (source, None, monitor, []))

    def _broken() -> Session:
        raise RuntimeError("database down")

    factory.snapshot_sink = MarketMonitorSnapshotPublisher(
        _broken,  # type: ignore[arg-type]
        worker_id="watcher-paper-test",
    )
    factory.poll_monitor("BTCUSDT")


def test_publisher_throttles_per_symbol(db: sessionmaker[Session]) -> None:
    clock = {"now": T0}
    publisher = MarketMonitorSnapshotPublisher(
        db, worker_id="w", min_interval_seconds=5.0, clock=lambda: clock["now"]
    )
    first = _fresh_snapshot(T0)
    publisher(first)
    clock["now"] = T0 + timedelta(seconds=1)
    publisher(_fresh_snapshot(T0 + timedelta(seconds=1)))
    with db() as session:
        row = session.get(MarketMonitorSnapshotRow, "BTCUSDT")
        assert row is not None
        assert row.payload["content_hash"] == first.content_hash


def test_paper_runtime_installs_snapshot_publisher(db: sessionmaker[Session]) -> None:
    from app.workers.watcher_paper import build_watcher_paper_runtime

    runtime = build_watcher_paper_runtime(Settings(**_LIVE), db)
    factory = runtime._evidence_factory
    assert isinstance(factory, SymbolMarketFactory)
    assert isinstance(factory.snapshot_sink, MarketMonitorSnapshotPublisher)


# --- HTTP: zero upstream I/O ------------------------------------------------


@pytest.fixture
def no_upstream_io(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any outbound HTTP, socket connect, or live source construction fails the test."""

    calls: list[str] = []

    def _http(self: object, request: httpx.Request, *args: object, **kwargs: object) -> None:
        calls.append(f"http:{request.url}")
        raise AssertionError(f"upstream HTTP during API read: {request.url}")

    def _connect(self: socket.socket, address: object) -> None:
        calls.append(f"socket:{address}")
        raise AssertionError(f"socket connect during API read: {address}")

    def _source(*args: object, **kwargs: object) -> None:
        calls.append("live_source")
        raise AssertionError("API constructed a live perpetual source")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _http)
    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr("app.market_monitor.factory.resolve_perpetual_evidence_source", _source)
    return calls


def _client(db: sessionmaker[Session]) -> tuple[TestClient, dict[str, str]]:
    get_settings.cache_clear()
    app = create_app(settings=Settings(**_LIVE))

    def _override_session() -> Iterator[Session]:
        with db() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    login = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert login.status_code == 200, login.text
    token = login.json()["tokens"]["access_token"]
    assert app.state.market_monitor is None
    return client, {"Authorization": f"Bearer {token}"}


def test_market_status_missing_row_makes_zero_upstream_calls(
    db: sessionmaker[Session], no_upstream_io: list[str]
) -> None:
    client, headers = _client(db)
    response = client.get("/canonical/market-status", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["availability"] == "unavailable"
    assert body["reason"] == "monitor_state_missing"
    assert body["unavailable_reason"] == "monitor_state_missing"
    assert body["current_price"]["usable_as_current_market_price"] is False
    assert no_upstream_io == []


def test_market_status_reads_persisted_row_with_zero_upstream_calls(
    db: sessionmaker[Session], no_upstream_io: list[str]
) -> None:
    now = datetime.now(UTC)
    snapshot = _fresh_snapshot(now)
    _persist(db, snapshot, now)
    client, headers = _client(db)
    response = client.get("/canonical/market-status", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["availability"] == "fresh"
    assert body["mode"] == "live_perpetual"
    assert body["current_price"]["price"] == "101234.7"
    assert no_upstream_io == []


def test_market_status_old_row_is_stale(
    db: sessionmaker[Session], no_upstream_io: list[str]
) -> None:
    old = datetime.now(UTC) - timedelta(minutes=5)
    _persist(db, _fresh_snapshot(old), old)
    client, headers = _client(db)
    body = client.get("/canonical/market-status", headers=headers).json()
    assert body["availability"] == "stale"
    assert body["reason"] == "monitor_state_stale"
    assert body["current_price"]["usable_as_current_market_price"] is False
    assert body["current_price"]["price"] is None
    assert no_upstream_io == []


def test_market_status_unknown_symbol_still_rejected(
    db: sessionmaker[Session], no_upstream_io: list[str]
) -> None:
    client, headers = _client(db)
    response = client.get("/canonical/market-status", params={"symbol": "ETHUSDT"}, headers=headers)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_perpetual_instrument"
    assert no_upstream_io == []


def test_watcher_monitoring_uses_persisted_row_with_zero_upstream_calls(
    db: sessionmaker[Session], no_upstream_io: list[str]
) -> None:
    old = datetime.now(UTC) - timedelta(minutes=5)
    _persist(db, _fresh_snapshot(old), old)
    client, headers = _client(db)
    response = client.get("/market-watcher/monitoring", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["market_freshness"]["availability"] == "stale"
    assert body["market_freshness"]["usable_as_current_market_price"] is False
    assert no_upstream_io == []


def test_health_ready_makes_zero_upstream_calls(
    db: sessionmaker[Session], no_upstream_io: list[str]
) -> None:
    client, _ = _client(db)
    response = client.get("/health/ready")
    assert response.status_code == 200, response.text
    assert no_upstream_io == []


def test_passive_perpetual_status_reflects_worker_row(db: sessionmaker[Session]) -> None:
    from app.market_monitor.persisted import PersistedPerpetualEvidenceStatus
    from app.providers.base import ProviderHealth, ProviderKind

    clock = {"now": T0}
    status = PersistedPerpetualEvidenceStatus(
        name="binance-usdm-perpetual",
        kind=ProviderKind.MARKET_DATA,
        settings=Settings(**_LIVE),
        session_factory=db,
        clock=lambda: clock["now"],
    )
    missing = status.status()
    assert missing.health is ProviderHealth.DEGRADED
    snapshot = _fresh_snapshot()
    _persist(db, snapshot, T0)
    assert status.status().health is snapshot.provider.health
    clock["now"] = T0 + timedelta(minutes=5)
    assert status.status().health is ProviderHealth.DEGRADED


# --- freshness boundary, clock skew, write ordering (PR #388 review) --------


def test_default_stale_after_is_thirty_seconds() -> None:
    assert Settings(**_LIVE).market_monitor_snapshot_stale_after_seconds == 30.0


def test_row_exactly_at_stale_after_is_not_stale_but_just_past_is(
    db: sessionmaker[Session],
) -> None:
    _persist(db, _fresh_snapshot(), T0)
    with db() as session:
        at_bound = _reader(db, session, T0 + timedelta(seconds=30)).snapshot("BTCUSDT")
        past = _reader(db, session, T0 + timedelta(seconds=30, microseconds=1)).snapshot("BTCUSDT")
    assert at_bound.availability is MarketAvailability.FRESH
    assert at_bound.current_price is not None
    assert past.availability is MarketAvailability.STALE
    assert past.reason is MonitorReason.MONITOR_STATE_STALE
    assert past.current_price is None


def test_future_written_at_is_stale(db: sessionmaker[Session]) -> None:
    # Worker clock an hour ahead: the row must not stay "fresh" for an hour.
    _persist(db, _fresh_snapshot(), T0 + timedelta(hours=1))
    with db() as session:
        served = _reader(db, session, T0).snapshot("BTCUSDT")
    assert served.availability is MarketAvailability.STALE
    assert served.reason is MonitorReason.MONITOR_STATE_STALE
    assert served.current_price is None


def test_future_evaluated_at_is_stale_even_with_db_written_at(
    db: sessionmaker[Session],
) -> None:
    _persist(db, _fresh_snapshot(T0 + timedelta(minutes=10)), T0)
    with db() as session:
        served = _reader(db, session, T0 + timedelta(seconds=1)).snapshot("BTCUSDT")
    assert served.availability is MarketAvailability.STALE
    assert served.reason is MonitorReason.MONITOR_STATE_STALE


def test_small_forward_skew_within_tolerance_is_tolerated(db: sessionmaker[Session]) -> None:
    _persist(db, _fresh_snapshot(T0 + timedelta(seconds=4)), T0 + timedelta(seconds=4))
    with db() as session:
        served = _reader(db, session, T0).snapshot("BTCUSDT")
    assert served.availability is MarketAvailability.FRESH


def test_passive_status_degraded_for_future_row(db: sessionmaker[Session]) -> None:
    from app.market_monitor.persisted import PersistedPerpetualEvidenceStatus
    from app.providers.base import ProviderHealth, ProviderKind

    _persist(db, _fresh_snapshot(), T0 + timedelta(hours=1))
    status = PersistedPerpetualEvidenceStatus(
        name="binance-usdm-perpetual",
        kind=ProviderKind.MARKET_DATA,
        settings=Settings(**_LIVE),
        session_factory=db,
        clock=lambda: T0,
    )
    assert status.status().health is ProviderHealth.DEGRADED


def test_older_snapshot_never_overwrites_newer(db: sessionmaker[Session]) -> None:
    newer = _fresh_snapshot(T0 + timedelta(seconds=10))
    older = _fresh_snapshot(T0)
    _persist(db, newer, T0 + timedelta(seconds=10))
    _persist(db, older, T0 + timedelta(seconds=11))
    with db() as session:
        row = session.get(MarketMonitorSnapshotRow, "BTCUSDT")
        assert row is not None
        assert row.evaluated_at.replace(tzinfo=UTC) == newer.evaluated_at
        assert row.written_at.replace(tzinfo=UTC) == T0 + timedelta(seconds=10)
    later = _fresh_snapshot(T0 + timedelta(seconds=20))
    _persist(db, later, T0 + timedelta(seconds=20))
    with db() as session:
        row = session.get(MarketMonitorSnapshotRow, "BTCUSDT")
        assert row is not None
        assert row.evaluated_at.replace(tzinfo=UTC) == later.evaluated_at


def test_portable_fallback_keeps_newer_row(db: sessionmaker[Session]) -> None:
    from app.market_monitor.persisted import _save_portable

    newer = _fresh_snapshot(T0 + timedelta(seconds=10))
    older = _fresh_snapshot(T0)
    for snap in (newer, older):
        with db() as session, session.begin():
            _save_portable(
                session,
                snap,
                snap.model_dump(mode="json"),
                snap.evaluated_at,
                "w",
                snap.evaluated_at,
            )
    with db() as session:
        row = session.get(MarketMonitorSnapshotRow, "BTCUSDT")
        assert row is not None
        assert row.evaluated_at.replace(tzinfo=UTC) == newer.evaluated_at


def test_postgres_upsert_uses_db_clock_and_monotonic_guard() -> None:
    from sqlalchemy.dialects import postgresql

    executed: list[object] = []

    class _Bind:
        dialect = postgresql.dialect()

    class _Session:
        def get_bind(self) -> _Bind:
            return _Bind()

        def execute(self, stmt: object) -> None:
            executed.append(stmt)

    save_monitor_snapshot(
        _Session(),  # type: ignore[arg-type]
        _fresh_snapshot(),
        worker_id="w",
        written_at=T0 + timedelta(hours=1),
    )
    sql = " ".join(str(executed[0].compile(dialect=postgresql.dialect())).split())  # type: ignore[attr-defined]
    assert "ON CONFLICT (symbol) DO UPDATE" in sql
    assert "WHERE market_monitor_snapshots.evaluated_at <= excluded.evaluated_at" in sql
    assert "now()" in sql


def test_market_status_exposes_row_age(
    db: sessionmaker[Session], no_upstream_io: list[str]
) -> None:
    written = datetime.now(UTC) - timedelta(seconds=3)
    _persist(db, _fresh_snapshot(written), written)
    client, headers = _client(db)
    body = client.get("/canonical/market-status", headers=headers).json()
    assert datetime.fromisoformat(body["written_at"]) == written
    assert 3.0 <= body["age_seconds"] < 30.0
    assert no_upstream_io == []


def test_market_status_missing_row_has_no_age(
    db: sessionmaker[Session], no_upstream_io: list[str]
) -> None:
    client, headers = _client(db)
    body = client.get("/canonical/market-status", headers=headers).json()
    assert body["written_at"] is None
    assert body["age_seconds"] is None
