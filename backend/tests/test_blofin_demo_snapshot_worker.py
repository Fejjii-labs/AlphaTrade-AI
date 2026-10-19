"""Snapshot cycle gates, fresh skip, and error backoff. No venue calls."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import structlog

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.schemas.common import ActorType, BloFinSyncHealthStatus
from app.services.blofin_sync_service import BloFinSyncService
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
        blofin_demo_snapshot_sync_enabled=True,
        governed_blofin_demo_organization_id=str(org),
        governed_blofin_demo_user_id=str(user),
    )


def _trading_key_settings(**updates: object) -> Settings:
    org, user = uuid4(), uuid4()
    values: dict[str, object] = {
        "environment": "local",
        "execution_mode": "paper",
        "enable_real_trading": False,
        "exchange_mode": "paper_exchange_demo",
        "blofin_demo_enabled": True,
        "provider_mode": "mock",
        "blofin_demo_snapshot_sync_enabled": True,
        "blofin_readonly_sync_enabled": False,
        "blofin_api_key": "fixture-trading-key",
        "blofin_api_secret": "fixture-trading-secret",
        "blofin_api_passphrase": "fixture-trading-pass",
        "blofin_demo_rest_base_url": "https://demo-trading-openapi.blofin.com",
        "governed_blofin_demo_organization_id": str(org),
        "governed_blofin_demo_user_id": str(user),
    }
    values.update(updates)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_snapshot_flag_off_does_not_construct_a_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLOFIN_ACTIVITY_ENABLED", "false")
    monkeypatch.setattr(
        "app.workers.paper_worker._build_armed_supervisor",
        lambda *_args: _idle_supervisor(),
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("disabled snapshot sync constructed a cycle")

    monkeypatch.setattr("app.workers.blofin_demo_snapshot.BloFinDemoSnapshotCycle", forbidden)
    worker = build_paper_worker_supervisor(_closed_settings())
    health = worker.run_round()
    assert health.demo_snapshot is None
    worker.close()


def test_disarmed_worker_does_not_construct_a_cycle_even_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.disarmed_worker_boot import (
        WorkerBootRole,
        bind_worker_boot_role,
        reset_disarmed_worker_boot,
        settings_are_disarmed_paper_worker,
    )

    monkeypatch.setenv("BLOFIN_ACTIVITY_ENABLED", "false")
    bind_worker_boot_role(WorkerBootRole.PAPER_WORKER)
    try:
        settings = Settings(
            _env_file=None,
            environment="staging",
            execution_mode="paper",
            enable_real_trading=False,
            exchange_mode="paper_internal",
            provider_mode="fallback",
            blofin_demo_snapshot_sync_enabled=True,
            auth_refresh_cookie_enabled=True,
            auth_cookie_secure=True,
            auth_cookie_samesite="none",
            cors_origins="https://app.example.com",
            rate_limit_use_redis=True,
            rate_limit_allow_in_memory_fallback=False,
            trusted_proxy_hops=1,
            access_token_denylist_enabled=True,
            access_token_denylist_use_redis=True,
            access_token_denylist_fail_closed=True,
            perpetual_evidence_source="binance_usdm",
            market_data_futures_base_url="https://fapi.binance.com",
            debug=False,
        )
        assert settings_are_disarmed_paper_worker(settings) is True

        def forbidden(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("disarmed worker constructed a snapshot cycle")

        monkeypatch.setattr("app.workers.blofin_demo_snapshot.BloFinDemoSnapshotCycle", forbidden)
        worker = build_paper_worker_supervisor(settings)
        assert worker.run_round().demo_snapshot is None
        worker.close()
    finally:
        reset_disarmed_worker_boot()


def test_trading_keys_skip_without_a_database_or_a_secret_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden() -> None:
        raise AssertionError("trading-key cycle opened a database")

    monkeypatch.setattr("app.db.session.get_session_factory", forbidden)
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_demo_account_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("trading provider")),
    )
    monkeypatch.setattr(
        "app.services.blofin_sync_service.ensure_demo_exchange_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("trading access")),
    )
    with structlog.testing.capture_logs() as logs:
        outcome = BloFinDemoSnapshotCycle(_trading_key_settings())()
    assert outcome.status == "skipped"
    assert outcome.error == "readonly_credentials_required"
    rendered = repr(logs)
    assert "fixture-trading-key" not in rendered
    assert "fixture-trading-secret" not in rendered
    assert "fixture-trading-pass" not in rendered
    assert any(item.get("event") == "blofin_demo_snapshot_readonly_required" for item in logs)


def test_worker_sync_never_uses_trading_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_demo_account_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("trading provider")),
    )
    monkeypatch.setattr(
        "app.services.blofin_sync_service.ensure_demo_exchange_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("trading access")),
    )
    service = BloFinSyncService(MagicMock(), _trading_key_settings())
    with structlog.testing.capture_logs() as logs, pytest.raises(ValueError, match="read-only"):
        service.sync(
            organization_id=uuid4(),
            user_id=uuid4(),
            sync_origin="worker",
        )
    rendered = repr(logs)
    assert "fixture-trading-key" not in rendered
    assert "fixture-trading-secret" not in rendered
    assert any(item.get("event") == "blofin_worker_sync_readonly_required" for item in logs)


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
    worker = build_paper_worker_supervisor(
        _closed_settings().model_copy(update={"blofin_demo_snapshot_sync_enabled": True})
    )
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


def test_fresh_degraded_snapshot_skips_blofin_and_reports_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _open_settings()
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    synced: list[dict[str, object]] = []
    session = SimpleNamespace(committed=False, closed=False, rollback=lambda: None)

    def close() -> None:
        session.closed = True

    session.commit = lambda: None
    session.close = close

    class Service:
        def __init__(self, _session: object, _settings: object) -> None:
            pass

        def latest(self, *, organization_id: object) -> SimpleNamespace:
            assert organization_id
            return SimpleNamespace(
                health_status=BloFinSyncHealthStatus.DEGRADED,
                synced_at=now - timedelta(seconds=10),
            )

        def sync(self, **kwargs: object) -> SimpleNamespace:
            synced.append(kwargs)
            raise AssertionError("fresh degraded snapshot called BloFin")

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr("app.services.blofin_sync_service.BloFinSyncService", Service)
    cycle = BloFinDemoSnapshotCycle(settings, clock=lambda: now)
    outcome = cycle()
    assert outcome.status == "degraded"
    assert outcome.error == "snapshot_degraded"
    assert synced == []
    assert session.committed is False
    assert session.closed is True
    assert cycle().status == "degraded"
    assert synced == []


def test_synced_degraded_snapshot_is_unhealthy_without_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _open_settings()
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    synced: list[dict[str, object]] = []
    session = SimpleNamespace(committed=False, closed=False)

    def commit() -> None:
        session.committed = True

    session.commit = commit
    session.rollback = lambda: None
    session.close = lambda: None

    class Service:
        def __init__(self, _session: object, _settings: object) -> None:
            pass

        def latest(self, *, organization_id: object) -> None:
            raise NotFoundError("none")

        def sync(self, **kwargs: object) -> SimpleNamespace:
            synced.append(kwargs)
            health = BloFinSyncHealthStatus.DEGRADED
            return SimpleNamespace(snapshot=SimpleNamespace(health_status=health))

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr("app.services.blofin_sync_service.BloFinSyncService", Service)
    cycle = BloFinDemoSnapshotCycle(settings, clock=lambda: now)
    outcome = cycle()
    assert outcome.status == "degraded"
    assert outcome.error == "snapshot_degraded"
    assert session.committed is True
    assert cycle().status == "degraded"
    assert len(synced) == 2


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
