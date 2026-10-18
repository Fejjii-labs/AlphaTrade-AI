"""Snapshot cycle gates, fresh skip, and error backoff. No venue calls."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.schemas.common import ActorType, BloFinSyncHealthStatus
from app.workers.blofin_demo_snapshot import BloFinDemoSnapshotCycle
from app.workers.paper_worker import (
    CycleOutcome,
    PaperWorkerSupervisor,
    build_paper_worker_supervisor,
)
from tests.test_blofin_activity_provider import readonly_settings


def _closed_settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="local",
        execution_mode="paper",
        enable_real_trading=False,
        exchange_mode="paper_internal",
        provider_mode="mock",
        blofin_readonly_sync_enabled=False,
    )


def _open_settings() -> Settings:
    org, user = uuid4(), uuid4()
    return readonly_settings(
        governed_blofin_demo_organization_id=str(org),
        governed_blofin_demo_user_id=str(user),
    )


def test_closed_gates_skip_without_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> None:
        raise AssertionError("closed gates opened a database")

    monkeypatch.setattr("app.db.session.get_session_factory", forbidden)
    cycle = BloFinDemoSnapshotCycle(_closed_settings())
    outcome = cycle()
    assert outcome.status == "skipped"
    assert outcome.error == "demo_gates_closed"


def test_open_gates_without_an_owner_pin_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> None:
        raise AssertionError("unpinned cycle opened a database")

    monkeypatch.setattr("app.db.session.get_session_factory", forbidden)
    cycle = BloFinDemoSnapshotCycle(readonly_settings())
    outcome = cycle()
    assert outcome.status == "skipped"
    assert outcome.error == "demo_owner_unpinned"


def _idle_supervisor() -> PaperWorkerSupervisor:
    return PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome("running"),
        telegram_cycle=lambda: CycleOutcome("disarmed"),
        poll_seconds=0.01,
    )


def test_supervisor_reports_snapshot_health_when_gates_are_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BLOFIN_ACTIVITY_ENABLED", "false")
    monkeypatch.setattr(
        "app.workers.paper_worker._build_armed_supervisor",
        lambda *_args: _idle_supervisor(),
    )

    def forbidden() -> None:
        raise AssertionError("skipped snapshot opened a database")

    monkeypatch.setattr("app.db.session.get_session_factory", forbidden)
    worker = build_paper_worker_supervisor(_closed_settings())
    health = worker.run_round()
    assert health.demo_snapshot is not None
    assert health.demo_snapshot.status == "skipped"
    assert health.demo_snapshot.last_error == "demo_gates_closed"
    assert health.watcher.status == "running"
    worker.close()


def test_open_gates_sync_once_and_back_off_after_an_error() -> None:
    settings = _open_settings()
    now = datetime(2026, 10, 18, tzinfo=UTC)
    clock = {"now": now}
    calls: list[str] = []

    cycle = BloFinDemoSnapshotCycle(settings, clock=lambda: clock["now"])

    def sync_once(_org: object, _user: object) -> str:
        calls.append("sync")
        return "unavailable"

    cycle._sync_once = sync_once  # type: ignore[method-assign]
    assert cycle().status == "degraded"
    assert cycle().status == "backing_off"
    assert calls == ["sync"]
    clock["now"] = now + timedelta(seconds=settings.blofin_demo_snapshot_sync_seconds * 2 - 1)
    assert cycle().status == "backing_off"
    clock["now"] = now + timedelta(seconds=settings.blofin_demo_snapshot_sync_seconds * 2)
    assert cycle().status == "degraded"
    assert calls == ["sync", "sync"]


def test_fresh_snapshot_does_not_call_blofin(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _open_settings()
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    synced: list[dict[str, object]] = []
    session = SimpleNamespace(committed=False, closed=False, rollback=lambda: None)

    def commit() -> None:
        session.committed = True

    def close() -> None:
        session.closed = True

    session.commit = commit
    session.close = close

    class Service:
        def __init__(self, _session: object, _settings: object) -> None:
            pass

        def latest(self, *, organization_id: object) -> SimpleNamespace:
            assert organization_id
            return SimpleNamespace(
                health_status=BloFinSyncHealthStatus.OK,
                synced_at=now - timedelta(seconds=10),
            )

        def sync(self, **kwargs: object) -> SimpleNamespace:
            synced.append(kwargs)
            health = BloFinSyncHealthStatus.OK
            return SimpleNamespace(snapshot=SimpleNamespace(health_status=health))

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr("app.services.blofin_sync_service.BloFinSyncService", Service)
    cycle = BloFinDemoSnapshotCycle(settings, clock=lambda: now)
    assert cycle().status == "ok"
    assert synced == []
    assert session.closed is True
    assert session.committed is False


def test_stale_connection_syncs_and_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _open_settings()
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    synced: list[dict[str, object]] = []
    session = SimpleNamespace(committed=False, closed=False)

    def commit() -> None:
        session.committed = True

    def close() -> None:
        session.closed = True

    session.commit = commit
    session.rollback = lambda: None
    session.close = close

    class Service:
        def __init__(self, _session: object, _settings: object) -> None:
            pass

        def latest(self, *, organization_id: object) -> None:
            raise NotFoundError("none")

        def sync(self, **kwargs: object) -> SimpleNamespace:
            synced.append(kwargs)
            health = BloFinSyncHealthStatus.OK
            return SimpleNamespace(snapshot=SimpleNamespace(health_status=health))

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr("app.services.blofin_sync_service.BloFinSyncService", Service)
    cycle = BloFinDemoSnapshotCycle(settings, clock=lambda: now)
    assert cycle().status == "ok"
    assert session.committed is True
    assert synced[0]["actor_type"] is ActorType.SYSTEM
    assert synced[0]["sync_origin"] == "worker"
    assert synced[0]["include_instrument_metadata"] is True


def test_flag_off_does_not_construct_the_readonly_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("flag off opened the read-only client")

    monkeypatch.setattr("app.core.blofin_readonly_access.get_readonly_client", forbidden)
    settings = _open_settings()
    assert settings.blofin_demo_manual_mirror_enabled is False
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    session = SimpleNamespace(committed=False, closed=False, rollback=lambda: None)
    session.commit = lambda: setattr(session, "committed", True)
    session.close = lambda: setattr(session, "closed", True)

    class Service:
        def __init__(self, _session: object, _settings: object) -> None:
            pass

        def latest(self, *, organization_id: object) -> None:
            raise NotFoundError("none")

        def sync(self, **_kwargs: object) -> SimpleNamespace:
            health = BloFinSyncHealthStatus.OK
            return SimpleNamespace(snapshot=SimpleNamespace(health_status=health))

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr("app.services.blofin_sync_service.BloFinSyncService", Service)
    cycle = BloFinDemoSnapshotCycle(settings, clock=lambda: now)
    assert cycle().status == "ok"
    assert session.committed is True


def test_flag_on_runs_the_mirror_after_the_snapshot_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _open_settings().model_copy(update={"blofin_demo_manual_mirror_enabled": True})
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    commits: list[str] = []
    applied: list[str] = []
    session = SimpleNamespace(closed=False, rollback=lambda: None)

    def commit() -> None:
        commits.append("commit")

    session.commit = commit
    session.close = lambda: setattr(session, "closed", True)

    class Service:
        def __init__(self, _session: object, _settings: object) -> None:
            pass

        def latest(self, *, organization_id: object) -> None:
            raise NotFoundError("none")

        def sync(self, **_kwargs: object) -> SimpleNamespace:
            health = BloFinSyncHealthStatus.OK
            return SimpleNamespace(snapshot=SimpleNamespace(health_status=health))

    class Mirror:
        def __init__(self, client: object, *, now: datetime) -> None:
            assert client == "readonly-client"
            assert now.tzinfo is UTC

        def apply(self, _session: object, **kwargs: object) -> None:
            bound = kwargs["settings"]
            assert getattr(bound, "blofin_demo_manual_mirror_enabled", None) is True
            applied.append("apply")

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr("app.services.blofin_sync_service.BloFinSyncService", Service)
    monkeypatch.setattr(
        "app.core.blofin_readonly_access.get_readonly_client",
        lambda *_args, **_kwargs: "readonly-client",
    )
    monkeypatch.setattr(
        "app.services.blofin_demo_manual_mirror.BloFinDemoManualMirror",
        Mirror,
    )
    cycle = BloFinDemoSnapshotCycle(settings, clock=lambda: now)
    assert cycle().status == "ok"
    assert commits == ["commit", "commit"]
    assert applied == ["apply"]


def test_fresh_snapshot_still_runs_the_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _open_settings().model_copy(update={"blofin_demo_manual_mirror_enabled": True})
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    synced: list[str] = []
    applied: list[str] = []
    commits: list[str] = []
    session = SimpleNamespace(closed=False, rollback=lambda: None)
    session.commit = lambda: commits.append("commit")
    session.close = lambda: setattr(session, "closed", True)

    class Service:
        def __init__(self, _session: object, _settings: object) -> None:
            pass

        def latest(self, *, organization_id: object) -> SimpleNamespace:
            assert organization_id
            return SimpleNamespace(
                health_status=BloFinSyncHealthStatus.OK,
                synced_at=now - timedelta(seconds=10),
            )

        def sync(self, **_kwargs: object) -> SimpleNamespace:
            synced.append("sync")
            health = BloFinSyncHealthStatus.OK
            return SimpleNamespace(snapshot=SimpleNamespace(health_status=health))

    class Mirror:
        def __init__(self, client: object, *, now: datetime) -> None:
            assert client == "readonly-client"
            assert now.tzinfo is UTC

        def apply(self, _session: object, **_kwargs: object) -> None:
            applied.append("apply")

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr("app.services.blofin_sync_service.BloFinSyncService", Service)
    monkeypatch.setattr(
        "app.core.blofin_readonly_access.get_readonly_client",
        lambda *_args, **_kwargs: "readonly-client",
    )
    monkeypatch.setattr(
        "app.services.blofin_demo_manual_mirror.BloFinDemoManualMirror",
        Mirror,
    )
    cycle = BloFinDemoSnapshotCycle(settings, clock=lambda: now)
    assert cycle().status == "ok"
    assert synced == []
    assert applied == ["apply"]
    assert commits == ["commit"]
    assert cycle._next_attempt is None


def test_mirror_read_failure_does_not_back_off_the_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _open_settings().model_copy(update={"blofin_demo_manual_mirror_enabled": True})
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    commits: list[str] = []
    rollbacks: list[str] = []
    session = SimpleNamespace(closed=False)
    session.commit = lambda: commits.append("commit")
    session.rollback = lambda: rollbacks.append("rollback")
    session.close = lambda: setattr(session, "closed", True)

    class Service:
        def __init__(self, _session: object, _settings: object) -> None:
            pass

        def latest(self, *, organization_id: object) -> SimpleNamespace:
            return SimpleNamespace(
                health_status=BloFinSyncHealthStatus.OK,
                synced_at=now - timedelta(seconds=10),
            )

        def sync(self, **_kwargs: object) -> None:
            raise AssertionError("a fresh snapshot must not be refetched")

    class Mirror:
        def __init__(self, _client: object, *, now: datetime) -> None:
            assert now.tzinfo is UTC

        def apply(self, _session: object, **_kwargs: object) -> None:
            raise RuntimeError("mirror read")

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr("app.services.blofin_sync_service.BloFinSyncService", Service)
    monkeypatch.setattr(
        "app.core.blofin_readonly_access.get_readonly_client",
        lambda *_args, **_kwargs: "readonly-client",
    )
    recorded: list[str] = []

    def remember(*_args: object, **kwargs: object) -> None:
        recorded.append(str(kwargs.get("outcome")))

    monkeypatch.setattr(
        "app.services.blofin_demo_manual_mirror.BloFinDemoManualMirror",
        Mirror,
    )
    monkeypatch.setattr(
        "app.services.blofin_demo_manual_mirror.record_mirror_cycle",
        remember,
    )
    cycle = BloFinDemoSnapshotCycle(settings, clock=lambda: now)
    outcome = cycle()
    assert outcome.status == "ok"
    assert outcome.error == ""
    assert commits == ["commit"]
    assert rollbacks == ["rollback"]
    assert recorded == ["failed"]
    assert cycle._failures == 0
    assert cycle._next_attempt is None
    assert cycle().status == "ok"
