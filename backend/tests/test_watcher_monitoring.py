"""Watcher PAPER MONITORING API — read-only observability, Watcher stays off."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models import (
    KillSwitchState,
    MarketWatcherObservation,
    MarketWatcherScanRecord,
    Membership,
    Organization,
    User,
    WorkerHeartbeat,
)
from app.db.runtime_status import ControlledRuntimeStatusRow
from app.db.session import get_session
from app.db.watcher_orchestration import WatcherHeartbeatRow, WatcherWorkerLeaseRow
from app.main import create_app
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.schemas.common import MarketWatcherObservationStatus, MembershipRole
from app.security.passwords import hash_password
from app.security.rate_limit import reset_rate_limiter
from app.services.watcher_monitoring_service import WatcherMonitoringService

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000006901")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000006902")
OTHER_ORG = uuid.UUID("00000000-0000-0000-0000-000000006903")
NOW = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "watcher-monitoring-secret-minimum-32",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
    "alert_delivery_enabled": False,
    "telegram_alerts_enabled": False,
    "telegram_interaction_enabled": False,
    "automatic_telegram_delivery_enabled": False,
    "worker_enabled": False,
    "market_watcher_enabled": False,
    "watcher_orchestration_enabled": False,
}


class _FakeProviders:
    def __init__(self, statuses: list[ProviderStatus]) -> None:
        self._statuses = statuses

    def statuses(self) -> list[ProviderStatus]:
        return list(self._statuses)


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


@pytest.fixture
def monitoring_db() -> Iterator[sessionmaker[Session]]:
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
    settings = Settings(**_BASE)
    with factory() as session:
        session.add(Organization(id=ORG_ID, name="Monitor Org"))
        session.add(Organization(id=OTHER_ORG, name="Other Org"))
        session.add(
            User(
                id=USER_ID,
                email="owner-monitor@test.example",
                hashed_password=hash_password("TestPassword123!", settings),
                email_verified=True,
            )
        )
        session.flush()
        session.add(Membership(user_id=USER_ID, organization_id=ORG_ID, role=MembershipRole.OWNER))
        session.commit()
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _client(
    monitoring_db: sessionmaker[Session],
    *,
    settings_overrides: dict[str, object] | None = None,
) -> TestClient:
    overrides = dict(_BASE)
    if settings_overrides:
        overrides.update(settings_overrides)
    settings = Settings(**overrides)
    get_settings.cache_clear()
    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with monitoring_db() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    login = client.post(
        "/auth/login",
        json={"email": "owner-monitor@test.example", "password": "TestPassword123!"},
    )
    token = login.json()["tokens"]["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def _seed_live_lease(session: Session, *, now: datetime = NOW) -> None:
    session.add(
        WatcherWorkerLeaseRow(
            id=uuid.uuid4(),
            organization_id=ORG_ID,
            scan_scope="paper-monitor",
            owner_id="worker-1",
            lease_epoch=1,
            fencing_token=1,
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(seconds=30),
        )
    )
    session.add(
        WatcherHeartbeatRow(
            id=uuid.uuid4(),
            organization_id=ORG_ID,
            scan_scope="paper-monitor",
            owner_id="worker-1",
            lease_epoch=1,
            fencing_token=1,
            last_beat_at=now,
            detail=None,
        )
    )
    session.commit()


def _snapshot(
    factory: sessionmaker[Session],
    *,
    settings_overrides: dict[str, object] | None = None,
    providers: list[ProviderStatus] | None = None,
    now: datetime = NOW,
) -> object:
    overrides = dict(_BASE)
    if settings_overrides:
        overrides.update(settings_overrides)
    settings = Settings(**overrides)
    with factory() as session:
        return WatcherMonitoringService(
            session,
            settings,
            providers=_FakeProviders(providers or []),
            now=now,
        ).get_snapshot(organization_id=ORG_ID, user_id=USER_ID)


def test_unauthenticated_monitoring_is_401(monitoring_db: sessionmaker[Session]) -> None:
    settings = Settings(**_BASE)
    get_settings.cache_clear()
    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with monitoring_db() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    response = client.get("/market-watcher/monitoring")
    assert response.status_code == 401


def test_default_snapshot_is_stopped_paper_only_and_empty(
    monitoring_db: sessionmaker[Session],
) -> None:
    client = _client(monitoring_db)
    response = client.get("/market-watcher/monitoring")
    assert response.status_code == 200
    body = response.json()
    assert body["watcher_status"] == "STOPPED"
    assert body["paper_monitoring_status"] == "STOPPED"
    assert body["paper_only"] is True
    assert body["paper_posture"]["paper_only"] is True
    assert body["paper_posture"]["real_trading_enabled"] is False
    assert body["paper_posture"]["runtime_evidence"] is False
    assert body["config_flags"]["market_watcher_enabled"] is False
    assert body["config_flags"]["watcher_orchestration_enabled"] is False
    assert body["config_flags"]["telegram_alerts_enabled"] is False
    assert body["scanner_candidates"]["count"] == 0
    assert body["canonical_candidates"] == []
    assert body["setup_assessments"] == []
    assert body["approved_strategies"] == []
    assert body["next_scan_at"] is None
    assert "no_approved_strategies" in body["warnings"]
    assert body["market_freshness"]["usable_as_current_market_price"] is False
    assert body["market_freshness"]["status"] in {"replay", "unknown"}
    if body["market_freshness"]["status"] == "replay":
        assert any("Replay" in item or "replay" in item.lower() for item in body["limitations"])
    health = client.get("/health").json()
    assert health["market_watcher_enabled"] is False
    assert health["watcher_orchestration_enabled"] is False
    assert health["real_trading_enabled"] is False


def test_scanner_config_flag_alone_is_not_running(monitoring_db: sessionmaker[Session]) -> None:
    snapshot = _snapshot(monitoring_db, settings_overrides={"market_watcher_enabled": True})
    assert snapshot.watcher_status.value == "STOPPED"
    assert snapshot.paper_posture.runtime_evidence is False
    assert snapshot.config_flags.market_watcher_enabled is True


def test_running_requires_live_lease_and_heartbeat(monitoring_db: sessionmaker[Session]) -> None:
    with monitoring_db() as session:
        _seed_live_lease(session)
    snapshot = _snapshot(
        monitoring_db,
        settings_overrides={"watcher_orchestration_enabled": True},
        now=NOW,
    )
    assert snapshot.watcher_status.value == "RUNNING"
    assert snapshot.reason_code == "healthy"
    assert snapshot.paper_posture.runtime_evidence is True
    assert snapshot.leases[0].fenced is True
    assert snapshot.next_scan_basis == "paper_poll"
    assert snapshot.next_scan_at is not None
    assert snapshot.paper_only is True


@pytest.mark.parametrize(
    "failure",
    [None, "stale", "future", "owner", "lease", "heartbeat", "kill", "refused", "disarmed"],
)
def test_separate_paper_worker_observed_with_api_disarmed(monitoring_db, failure):
    with monitoring_db() as session:
        _seed_live_lease(session)
        session.add(
            ControlledRuntimeStatusRow(
                component="watcher",
                worker_id="other" if failure == "owner" else "worker-1",
                heartbeat_at=NOW + timedelta(seconds=10)
                if failure == "future"
                else (NOW - timedelta(days=1) if failure == "stale" else NOW),
                activation_state=failure if failure in {"refused", "disarmed"} else "running",
                last_scan_reason="migration_unhealthy" if failure == "refused" else "",
            )
        )
        if failure == "lease":
            session.scalar(select(WatcherWorkerLeaseRow)).expires_at = NOW
        if failure == "heartbeat":
            session.scalar(select(WatcherHeartbeatRow)).last_beat_at = NOW - timedelta(days=1)
        if failure == "kill":
            session.add(KillSwitchState(organization_id=ORG_ID, active=True))
        session.commit()
    snapshot = _snapshot(monitoring_db)
    assert snapshot.config_flags.watcher_orchestration_enabled is False
    if failure is None:
        assert snapshot.watcher_status.value == "RUNNING"
        assert snapshot.paper_posture.runtime_evidence is True
    else:
        assert snapshot.watcher_status.value != "RUNNING"
    if failure in {"refused", "disarmed"}:
        # A fresh idle heartbeat and even an old live lease cannot establish remote scanning.
        assert snapshot.watcher_status.value == "STOPPED"
        assert snapshot.config_flags.watcher_orchestration_enabled is False
    with monitoring_db() as session:
        other = WatcherMonitoringService(session, Settings(**_BASE), now=NOW).get_snapshot(
            organization_id=OTHER_ORG,
            user_id=USER_ID,
        )
        assert other.watcher_status.value != "RUNNING"


def test_orchestration_enabled_without_heartbeat_is_stale(
    monitoring_db: sessionmaker[Session],
) -> None:
    snapshot = _snapshot(
        monitoring_db,
        settings_overrides={"watcher_orchestration_enabled": True},
    )
    assert snapshot.watcher_status.value == "STALE"
    assert snapshot.reason_code == "heartbeat_missing"
    assert snapshot.next_scan_at is None


def test_kill_switch_blocks_monitoring(monitoring_db: sessionmaker[Session]) -> None:
    with monitoring_db() as session:
        _seed_live_lease(session)
        session.add(
            KillSwitchState(
                organization_id=ORG_ID,
                active=True,
                reason="operator halt",
                activated_at=NOW,
                version=1,
            )
        )
        session.commit()
    snapshot = _snapshot(
        monitoring_db,
        settings_overrides={"watcher_orchestration_enabled": True},
        now=NOW,
    )
    assert snapshot.watcher_status.value == "BLOCKED"
    assert snapshot.reason_code == "kill_switch_active"
    assert snapshot.next_scan_at is None


def test_provider_outage_degrades_running_watcher(monitoring_db: sessionmaker[Session]) -> None:
    with monitoring_db() as session:
        _seed_live_lease(session)
    snapshot = _snapshot(
        monitoring_db,
        settings_overrides={"watcher_orchestration_enabled": True},
        providers=[
            ProviderStatus(
                name="mock-market-data",
                kind=ProviderKind.MARKET_DATA,
                health=ProviderHealth.UNAVAILABLE,
                error_message="upstream timeout",
            )
        ],
        now=NOW,
    )
    assert snapshot.watcher_status.value == "DEGRADED"
    assert snapshot.reason_code == "provider_unavailable"
    assert snapshot.recent_errors
    assert snapshot.recent_errors[0].source.startswith("provider:")


def test_stale_observation_is_stale_when_running(monitoring_db: sessionmaker[Session]) -> None:
    with monitoring_db() as session:
        _seed_live_lease(session)
        session.add(
            MarketWatcherObservation(
                organization_id=ORG_ID,
                symbol="BTCUSDT",
                exchange="binance",
                timeframe="1h",
                observed_at=NOW - timedelta(hours=3),
                status=MarketWatcherObservationStatus.STALE,
                data_freshness="stale",
            )
        )
        session.commit()
    snapshot = _snapshot(
        monitoring_db,
        settings_overrides={"watcher_orchestration_enabled": True},
        now=NOW,
    )
    assert snapshot.watcher_status.value == "STALE"
    assert snapshot.market_freshness.status == "stale"
    assert snapshot.market_freshness.symbol == "BTCUSDT"


def test_candidate_detected_uses_persisted_scan_not_fabrication(
    monitoring_db: sessionmaker[Session],
) -> None:
    with monitoring_db() as session:
        session.add(
            MarketWatcherScanRecord(
                organization_id=ORG_ID,
                scanned_at=NOW,
                status="ok",
                candidate_count=2,
                conditions_found=["liquidity_sweep", "sfp"],
                symbols=["BTCUSDT"],
                timeframes=["15m"],
                dry_run=True,
            )
        )
        session.commit()
    snapshot = _snapshot(monitoring_db, now=NOW)
    assert snapshot.watcher_status.value == "STOPPED"
    assert snapshot.scanner_candidates.count == 2
    assert snapshot.scanner_candidates.conditions == ["liquidity_sweep", "sfp"]
    assert snapshot.canonical_candidates == []
    assert snapshot.setup_assessments == []


def test_no_approved_strategies_warning(monitoring_db: sessionmaker[Session]) -> None:
    snapshot = _snapshot(monitoring_db)
    assert snapshot.approved_strategies == []
    assert "no_approved_strategies" in snapshot.warnings


def test_worker_heartbeat_without_watcher_flags_is_not_running(
    monitoring_db: sessionmaker[Session],
) -> None:
    with monitoring_db() as session:
        session.add(
            WorkerHeartbeat(
                worker_name="alphatrade-worker",
                status="ok",
                paused=False,
                cycle_count=3,
                last_beat_at=NOW,
            )
        )
        session.commit()
    snapshot = _snapshot(monitoring_db, now=NOW)
    assert snapshot.watcher_status.value == "STOPPED"
    assert snapshot.worker.heartbeat_live is True
    assert snapshot.paper_posture.runtime_evidence is False


def test_tenant_isolation_ignores_other_org_lease(monitoring_db: sessionmaker[Session]) -> None:
    with monitoring_db() as session:
        session.add(
            WatcherWorkerLeaseRow(
                id=uuid.uuid4(),
                organization_id=OTHER_ORG,
                scan_scope="paper-monitor",
                owner_id="other-worker",
                lease_epoch=1,
                fencing_token=1,
                acquired_at=NOW,
                renewed_at=NOW,
                expires_at=NOW + timedelta(seconds=30),
            )
        )
        session.add(
            WatcherHeartbeatRow(
                id=uuid.uuid4(),
                organization_id=OTHER_ORG,
                scan_scope="paper-monitor",
                owner_id="other-worker",
                lease_epoch=1,
                fencing_token=1,
                last_beat_at=NOW,
            )
        )
        session.commit()
    snapshot = _snapshot(
        monitoring_db,
        settings_overrides={"watcher_orchestration_enabled": True},
        now=NOW,
    )
    assert snapshot.leases == []
    assert snapshot.watcher_status.value == "STALE"


def test_monitoring_payload_exposes_operator_fields(
    monitoring_db: sessionmaker[Session],
) -> None:
    client = _client(monitoring_db)
    body = client.get("/market-watcher/monitoring").json()
    for key in (
        "watcher_status",
        "paper_monitoring_status",
        "reason_code",
        "block_reasons",
        "warnings",
        "paper_posture",
        "config_flags",
        "symbols_monitored",
        "approved_strategies",
        "last_scan_at",
        "next_scan_at",
        "market_freshness",
        "provider_health",
        "setup_assessments",
        "scanner_candidates",
        "canonical_candidates",
        "leases",
        "worker",
        "recent_errors",
        "paper_only",
    ):
        assert key in body
    assert body["watcher_status"] in {"RUNNING", "STOPPED", "DEGRADED", "STALE", "BLOCKED"}
    assert "price" not in body
    assert "last_price" not in body
    assert body["paper_posture"]["runtime_evidence"] is False


def test_monitoring_does_not_enable_watcher_flags(monitoring_db: sessionmaker[Session]) -> None:
    client = _client(monitoring_db)
    first = client.get("/market-watcher/monitoring")
    assert first.status_code == 200
    health = client.get("/health").json()
    assert health["market_watcher_enabled"] is False
    assert health["watcher_orchestration_enabled"] is False
    assert health["telegram_alerts_enabled"] is False
    with monitoring_db() as session:
        assert session.scalar(select(WatcherHeartbeatRow)) is None


def _seed_expired_scope(session: Session, scope: str) -> None:
    earlier = NOW - timedelta(hours=2)
    session.add(
        WatcherWorkerLeaseRow(
            id=uuid.uuid4(),
            organization_id=ORG_ID,
            scan_scope=scope,
            owner_id="worker-1",
            lease_epoch=1,
            fencing_token=1,
            acquired_at=earlier,
            renewed_at=earlier,
            expires_at=earlier + timedelta(seconds=30),
        )
    )
    session.add(
        WatcherHeartbeatRow(
            id=uuid.uuid4(),
            organization_id=ORG_ID,
            scan_scope=scope,
            owner_id="worker-1",
            lease_epoch=1,
            fencing_token=1,
            last_beat_at=earlier,
            detail=None,
        )
    )
    session.commit()


@pytest.mark.parametrize(
    ("targets", "expected"),
    [
        # The abandoned scope left the current target set: it cannot make the card Stale.
        (frozenset({"paper-monitor"}), "RUNNING"),
        # A current target that stopped renewing is genuinely stale.
        (frozenset({"paper-monitor", "removed-strategy:15m"}), "STALE"),
        # Unknown target set: every scope still counts (fail closed).
        (None, "STALE"),
    ],
)
def test_worst_of_staleness_only_counts_current_target_scopes(
    monitoring_db: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    targets: frozenset[str] | None,
    expected: str,
) -> None:
    with monitoring_db() as session:
        _seed_live_lease(session)
        _seed_expired_scope(session, "removed-strategy:15m")
    monkeypatch.setattr(
        WatcherMonitoringService,
        "_current_target_scopes",
        lambda self, organization_id: targets,
        raising=False,
    )
    snapshot = _snapshot(
        monitoring_db,
        settings_overrides={"watcher_orchestration_enabled": True},
        now=NOW,
    )
    assert snapshot.watcher_status.value == expected
    # The abandoned scope is still listed for operators.
    assert {lease.scan_scope for lease in snapshot.leases} == {
        "paper-monitor",
        "removed-strategy:15m",
    }
