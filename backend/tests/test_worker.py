"""Tests for the background worker: locking, heartbeats, dead-letters, control."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import AuditLog, Membership
from app.repositories.audit import AuditRepository
from app.schemas.common import ActorType, AuditEventType, MembershipRole
from app.services.audit_service import AuditPersistenceError
from app.workers.lock import InMemoryWorkerLock
from app.workers.repository import MarketScanRunRepository, WorkerHeartbeatRepository
from app.workers.runner import WorkerLoopDriver
from app.workers.service import Scanner, WorkerService
from tests.test_workflows import ORG_ID, USER_ID

WORKER = "test-worker"


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _service(factory: sessionmaker[Session], scanner: Scanner | None = None) -> WorkerService:
    return WorkerService(
        factory,
        InMemoryWorkerLock("k", ttl_seconds=60),
        worker_name=WORKER,
        scanner=scanner,
    )


# --- lock ------------------------------------------------------------------


def test_in_memory_lock_is_single_runner() -> None:
    lock = InMemoryWorkerLock("k", ttl_seconds=60)
    token = lock.try_acquire()
    assert token is not None
    assert lock.try_acquire() is None  # already held
    lock.release(token)
    assert lock.try_acquire() is not None


def test_lock_release_requires_matching_token() -> None:
    lock = InMemoryWorkerLock("k", ttl_seconds=60)
    token = lock.try_acquire()
    assert token is not None
    lock.release("wrong-token")
    assert lock.try_acquire() is None  # still held by original token
    lock.release(token)
    assert lock.try_acquire() is not None


def test_lock_expires_after_ttl() -> None:
    fake = {"t": 0.0}
    lock = InMemoryWorkerLock("k", ttl_seconds=10, clock=lambda: fake["t"])
    assert lock.try_acquire() is not None
    assert lock.try_acquire() is None
    fake["t"] = 11.0  # past TTL
    assert lock.try_acquire() is not None


# --- service ---------------------------------------------------------------


def test_run_cycle_success_records_run_and_heartbeat(factory: sessionmaker[Session]) -> None:
    service = _service(factory, scanner=lambda _s: (3, 2))
    result = service.run_cycle()

    assert result.status == "success"
    assert result.symbols_scanned == 3
    assert result.setups_detected == 2

    with factory() as session:
        heartbeat = WorkerHeartbeatRepository(session).get_by_name(WORKER)
        assert heartbeat is not None
        assert heartbeat.status == "running"
        assert heartbeat.cycle_count == 1
        runs = MarketScanRunRepository(session).list_recent()
        assert len(runs) == 1
        assert runs[0].status == "success"


def test_failed_cycle_is_dead_lettered(factory: sessionmaker[Session]) -> None:
    def boom(_s: Session) -> tuple[int, int]:
        raise RuntimeError("scanner exploded")

    service = _service(factory, scanner=boom)
    result = service.run_cycle()

    assert result.status == "failed"
    with factory() as session:
        dead = MarketScanRunRepository(session).list_dead_letters()
        assert len(dead) == 1
        assert dead[0].status == "failed"
        assert "scanner exploded" in (dead[0].error or "")
        heartbeat = WorkerHeartbeatRepository(session).get_by_name(WORKER)
        assert heartbeat is not None
        assert heartbeat.status == "error"


def test_error_messages_are_redacted(factory: sessionmaker[Session]) -> None:
    def boom(_s: Session) -> tuple[int, int]:
        raise RuntimeError("api_key=supersecret failed")

    _service(factory, scanner=boom).run_cycle()
    with factory() as session:
        dead = MarketScanRunRepository(session).list_dead_letters()
        assert "supersecret" not in (dead[0].error or "")


def test_paused_worker_skips_cycle(factory: sessionmaker[Session]) -> None:
    service = _service(factory, scanner=lambda _s: (1, 1))
    service.set_paused(True)
    result = service.run_cycle()

    assert result.status == "skipped"
    assert result.reason == "paused"
    with factory() as session:
        assert MarketScanRunRepository(session).list_recent() == []


def test_resume_clears_pause(factory: sessionmaker[Session]) -> None:
    service = _service(factory, scanner=lambda _s: (1, 1))
    service.set_paused(True)
    assert service.is_paused() is True
    service.set_paused(False)
    assert service.is_paused() is False
    with factory() as session:
        row = WorkerHeartbeatRepository(session).get_by_name(WORKER)
        assert row is not None
        assert row.last_beat_at.replace(tzinfo=UTC) == datetime(1970, 1, 1, tzinfo=UTC)
    assert service.run_cycle().status == "success"
    with factory() as session:
        row = WorkerHeartbeatRepository(session).get_by_name(WORKER)
        assert row is not None and row.last_beat_at.year > 1970


def test_lock_held_elsewhere_skips_cycle(factory: sessionmaker[Session]) -> None:
    lock = InMemoryWorkerLock("k", ttl_seconds=60)
    service = WorkerService(factory, lock, worker_name=WORKER, scanner=lambda _s: (1, 1))
    held = lock.try_acquire()  # simulate another runner holding the lock
    assert held is not None

    result = service.run_cycle()
    assert result.status == "skipped"
    assert result.reason == "lock_not_acquired"


# --- loop driver -----------------------------------------------------------


def test_loop_driver_runs_max_cycles(factory: sessionmaker[Session]) -> None:
    service = _service(factory, scanner=lambda _s: (1, 0))
    driver = WorkerLoopDriver(service, interval_seconds=0, sleeper=lambda _x: None)
    cycles = driver.run(max_cycles=3)

    assert cycles == 3
    with factory() as session:
        heartbeat = WorkerHeartbeatRepository(session).get_by_name(WORKER)
        assert heartbeat is not None
        assert heartbeat.cycle_count == 3


def test_loop_driver_stops_on_should_continue(factory: sessionmaker[Session]) -> None:
    service = _service(factory, scanner=lambda _s: (1, 0))
    driver = WorkerLoopDriver(service, interval_seconds=0, sleeper=lambda _x: None)
    cycles = driver.run(max_cycles=10, should_continue=lambda: False)
    assert cycles == 0


# --- HTTP endpoints --------------------------------------------------------


def test_worker_health_and_control_endpoints(
    client_with_workflow_db: TestClient,
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = workflow_db
    settings.platform_operator_user_ids = [USER_ID]
    # No heartbeat yet -> unknown but 200.
    initial = client_with_workflow_db.get("/worker/health")
    assert initial.status_code == 200
    assert initial.json()["status"] == "unknown"
    assert initial.json()["running"] is False

    paused = client_with_workflow_db.post("/worker/pause")
    assert paused.status_code == 200
    assert paused.json()["paused"] is True

    after = client_with_workflow_db.get("/worker/health")
    assert after.json()["paused"] is True
    assert after.json()["running"] is False
    assert after.json()["last_beat_at"].startswith("1970-01-01")

    resumed = client_with_workflow_db.post("/worker/resume")
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False

    health = client_with_workflow_db.get("/worker/health").json()
    assert health["running"] is False
    assert health["status"] == "unknown"
    assert health["last_beat_at"] == after.json()["last_beat_at"]
    with factory() as session:
        row = WorkerHeartbeatRepository(session).get_by_name(settings.worker_name)
        assert row is not None and row.paused is False
        events = session.scalars(
            select(AuditLog).where(AuditLog.resource_type == "worker").order_by(AuditLog.event_at)
        ).all()
        assert [event.action for event in events] == [
            AuditEventType.WORKER_PAUSED,
            AuditEventType.WORKER_RESUMED,
        ]
        for event, expected_paused in zip(events, (True, False), strict=True):
            assert event.user_id == USER_ID
            assert event.organization_id == ORG_ID
            assert event.actor_type == ActorType.USER
            assert event.actor == str(USER_ID)
            assert event.resource_id == settings.worker_name
            assert event.redacted_metadata == {
                "worker_name": settings.worker_name,
                "paused": expected_paused,
            }


def test_resume_before_first_worker_beat_stays_unhealthy(
    client_with_workflow_db: TestClient,
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    _, settings = workflow_db
    settings.platform_operator_user_ids = [USER_ID]
    assert client_with_workflow_db.post("/worker/resume").status_code == 200
    health = client_with_workflow_db.get("/worker/health").json()
    assert health["running"] is False
    assert health["paused"] is False
    assert health["status"] == "unknown"
    assert health["last_beat_at"].startswith("1970-01-01")


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_worker_control_requires_authentication(
    client_with_workflow_db: TestClient,
    workflow_db: tuple[sessionmaker[Session], Settings],
    action: str,
) -> None:
    _, settings = workflow_db
    settings.platform_operator_user_ids = [USER_ID]
    client_with_workflow_db.headers.pop("Authorization")
    assert client_with_workflow_db.post(f"/worker/{action}").status_code == 401


@pytest.mark.parametrize("allowlist", [[], [UUID(int=999)]], ids=["empty", "other-user"])
@pytest.mark.parametrize("action,initial_paused", [("pause", False), ("resume", True)])
@pytest.mark.parametrize("seed_heartbeat", [False, True])
def test_owner_without_operator_authorization_cannot_control_worker(
    client_with_workflow_db: TestClient,
    workflow_db: tuple[sessionmaker[Session], Settings],
    allowlist: list[UUID],
    action: str,
    initial_paused: bool,
    seed_heartbeat: bool,
) -> None:
    factory, settings = workflow_db
    settings.platform_operator_user_ids = allowlist
    beat = datetime(2020, 1, 1, tzinfo=UTC)
    if seed_heartbeat:
        with factory() as session:
            WorkerHeartbeatRepository(session).upsert(
                worker_name=settings.worker_name,
                status="error",
                last_beat_at=beat,
                paused=initial_paused,
            )
            session.commit()
    response = client_with_workflow_db.post(f"/worker/{action}")
    assert response.status_code == 403
    assert "Platform operator" in response.json()["error"]["message"]
    assert client_with_workflow_db.get("/worker/health").status_code == 200
    with factory() as session:
        row = WorkerHeartbeatRepository(session).get_by_name(settings.worker_name)
        if seed_heartbeat:
            assert row is not None
            assert row.paused is initial_paused
            assert row.status == "error"
            assert row.last_beat_at.replace(tzinfo=UTC) == beat
        else:
            assert row is None
        assert (
            session.scalars(select(AuditLog).where(AuditLog.resource_type == "worker")).all() == []
        )


@pytest.mark.parametrize("status", ["running", "error"])
@pytest.mark.parametrize("stale", [False, True])
def test_operator_controls_preserve_heartbeat_and_cycle_status(
    client_with_workflow_db: TestClient,
    workflow_db: tuple[sessionmaker[Session], Settings],
    status: str,
    stale: bool,
) -> None:
    factory, settings = workflow_db
    settings.platform_operator_user_ids = [USER_ID]
    beat = datetime.now(UTC) - timedelta(days=1 if stale else 0)
    with factory() as session:
        WorkerHeartbeatRepository(session).upsert(
            worker_name=settings.worker_name, status=status, last_beat_at=beat, cycle_count=7
        )
        session.commit()
    for action, paused in (("pause", True), ("resume", False)):
        response = client_with_workflow_db.post(f"/worker/{action}")
        assert response.status_code == 200
        assert response.json()["paused"] is paused
        with factory() as session:
            row = WorkerHeartbeatRepository(session).get_by_name(settings.worker_name)
            assert row is not None
            assert row.last_beat_at.replace(tzinfo=UTC) == beat
            assert row.status == status
            assert row.cycle_count == 7
        health = client_with_workflow_db.get("/worker/health").json()
        assert health["running"] is (not paused and not stale and status != "error")


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_allowlisted_operator_still_requires_owner_role(
    client_with_workflow_db: TestClient,
    workflow_db: tuple[sessionmaker[Session], Settings],
    action: str,
) -> None:
    factory, settings = workflow_db
    settings.platform_operator_user_ids = [USER_ID]
    with factory() as session:
        membership = session.scalars(select(Membership).where(Membership.user_id == USER_ID)).one()
        membership.role = MembershipRole.TRADER
        session.commit()
    assert client_with_workflow_db.post(f"/worker/{action}").status_code == 403


@pytest.mark.parametrize("action,initial_paused", [("pause", False), ("resume", True)])
def test_failed_audit_rolls_back_worker_control(
    client_with_workflow_db: TestClient,
    workflow_db: tuple[sessionmaker[Session], Settings],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    initial_paused: bool,
) -> None:
    factory, settings = workflow_db
    settings.platform_operator_user_ids = [USER_ID]
    with factory() as session:
        WorkerHeartbeatRepository(session).set_paused(
            worker_name=settings.worker_name, paused=initial_paused
        )
        session.commit()

    def fail_audit(_self: AuditRepository, _entity: AuditLog) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AuditRepository, "add", fail_audit)
    with pytest.raises(AuditPersistenceError):
        client_with_workflow_db.post(f"/worker/{action}")
    with factory() as session:
        row = WorkerHeartbeatRepository(session).get_by_name(settings.worker_name)
        assert row is not None and row.paused is initial_paused


def test_platform_operator_ids_parse_csv_and_default_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PLATFORM_OPERATOR_USER_IDS", raising=False)
    assert Settings(_env_file=None).platform_operator_user_ids == []
    monkeypatch.setenv("PLATFORM_OPERATOR_USER_IDS", f" {USER_ID}, , {UUID(int=999)} ")
    assert Settings(_env_file=None).platform_operator_user_ids == [USER_ID, UUID(int=999)]
    monkeypatch.setenv("PLATFORM_OPERATOR_USER_IDS", " , ")
    assert Settings(_env_file=None).platform_operator_user_ids == []
    monkeypatch.setenv("PLATFORM_OPERATOR_USER_IDS", "invalid-user-id")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
