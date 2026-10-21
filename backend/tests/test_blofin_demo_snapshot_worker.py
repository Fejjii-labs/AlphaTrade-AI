"""Snapshot cycle gates, fresh skip, and error backoff. No venue calls."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
import structlog

from app.core.blofin_readonly_access import (
    BloFinReadOnlyClient,
    get_readonly_account_provider,
    get_readonly_client,
)
from app.core.config import Settings
from app.core.errors import ExchangeDemoInactiveError, NotFoundError
from app.providers.exchange.base import AccountPermissions
from app.providers.exchange.blofin_account import BloFinAccountProvider
from app.schemas.common import ActorType, BloFinSyncHealthStatus
from app.services.blofin_sync_service import BloFinSyncService
from app.services.dashboard.demo_account import demo_account_active
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


def test_partial_readonly_set_skips_without_a_database_or_a_secret_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden() -> None:
        raise AssertionError("partial read-only set opened a database")

    monkeypatch.setattr("app.db.session.get_session_factory", forbidden)
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_demo_account_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("trading provider")),
    )
    monkeypatch.setattr(
        "app.services.blofin_sync_service.ensure_demo_exchange_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("trading access")),
    )

    def forbidden_mirror(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("mirror ran on a partial read-only set")

    monkeypatch.setattr(
        "app.services.blofin_demo_manual_mirror.BloFinDemoManualMirror",
        forbidden_mirror,
    )
    with structlog.testing.capture_logs() as logs:
        outcome = BloFinDemoSnapshotCycle(
            _trading_key_settings(
                blofin_demo_manual_mirror_enabled=True,
                blofin_readonly_api_key="partial-only",
            )
        )()
    assert outcome.status == "skipped"
    assert outcome.error == "readonly_credentials_required"
    rendered = repr(logs)
    assert "fixture-trading-key" not in rendered
    assert "fixture-trading-secret" not in rendered
    assert "fixture-trading-pass" not in rendered
    assert "partial-only" not in rendered
    assert any(item.get("event") == "blofin_demo_snapshot_readonly_required" for item in logs)


def test_demo_key_snapshot_uses_the_get_only_client(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _trading_key_settings(blofin_demo_manual_mirror_enabled=True)
    now = datetime(2026, 10, 21, tzinfo=UTC)
    captured: dict[str, object] = {}
    session = SimpleNamespace(committed=False, closed=False, rollback=lambda: None)
    session.commit = lambda: setattr(session, "committed", True)
    session.close = lambda: setattr(session, "closed", True)

    class Service:
        def __init__(self, _session: object, _settings: object) -> None:
            pass

        def latest(self, *, organization_id: object, user_id: object = None) -> None:
            assert organization_id and user_id
            raise NotFoundError("none")

        def sync(self, **_kwargs: object) -> SimpleNamespace:
            health = BloFinSyncHealthStatus.OK
            return SimpleNamespace(snapshot=SimpleNamespace(health_status=health))

    class Mirror:
        def __init__(self, client: object, now: object = None) -> None:
            captured["client"] = client

        def apply(self, *_args: object, **_kwargs: object) -> None:
            captured["applied"] = True

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr("app.services.blofin_sync_service.BloFinSyncService", Service)
    monkeypatch.setattr("app.services.blofin_demo_manual_mirror.BloFinDemoManualMirror", Mirror)
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_demo_account_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("trading provider")),
    )
    monkeypatch.setattr(
        "app.services.blofin_sync_service.ensure_demo_exchange_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("trading access")),
    )
    with structlog.testing.capture_logs() as logs:
        outcome = BloFinDemoSnapshotCycle(settings, clock=lambda: now)()
    assert outcome.status == "ok"
    assert captured["applied"] is True
    client = captured["client"]
    assert isinstance(client, BloFinReadOnlyClient)
    with pytest.raises(ExchangeDemoInactiveError):
        client.request("POST", "/api/v1/trade/order", body={"instId": "BTC-USDT"})
    rendered = repr(logs)
    assert "fixture-trading-key" not in rendered
    assert "fixture-trading-secret" not in rendered
    assert "fixture-trading-pass" not in rendered


def test_worker_sync_uses_get_only_demo_key_and_refuses_trade_permission_on_dedicated_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _trading_key_settings()
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.headers["ACCESS-KEY"] == "fixture-trading-key"
        data = {
            "/api/v1/user/query-apikey": {"readOnly": 0, "uid": "demo-read-fallback"},
            "/api/v1/account/balance": {
                "totalEquity": "10",
                "details": [{"currency": "USDT", "balance": "10", "available": "10"}],
            },
            "/api/v1/account/positions": [],
        }[request.url.path]
        return httpx.Response(200, json={"code": "0", "data": data})

    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_readonly_account_provider",
        lambda cfg: get_readonly_account_provider(cfg, transport=httpx.MockTransport(handle)),
    )
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_demo_account_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("trading provider")),
    )
    monkeypatch.setattr(
        "app.services.blofin_sync_service.ensure_demo_exchange_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("trading access")),
    )
    service = BloFinSyncService(MagicMock(), settings)

    def keep(row: object) -> None:
        if getattr(row, "id", None) is None:
            row.id = uuid4()  # type: ignore[attr-defined]

    monkeypatch.setattr(service._snapshots, "add", keep)
    monkeypatch.setattr(service._audit, "record", lambda *_args, **_kwargs: None)
    with structlog.testing.capture_logs() as logs:
        result = service.sync(organization_id=uuid4(), user_id=uuid4(), sync_origin="worker")
    assert result.snapshot.health_status is BloFinSyncHealthStatus.OK
    assert [request.method for request in seen] == ["GET", "GET", "GET"]
    rendered = repr(logs)
    assert "fixture-trading-key" not in rendered
    assert "fixture-trading-secret" not in rendered

    dedicated = _trading_key_settings(
        blofin_readonly_sync_enabled=True,
        blofin_readonly_api_key="fixture-reader",
        blofin_readonly_api_secret="fixture-reader-secret",
        blofin_readonly_api_passphrase="fixture-reader-pass",
    )
    trade_client = BloFinReadOnlyClient(
        base_url="https://demo-trading-openapi.blofin.com",
        api_key="fixture-reader",
        api_secret="fixture-reader-secret",
        api_passphrase="fixture-reader-pass",
        transport=httpx.MockTransport(handle),
    )

    class TradingKeyProvider(BloFinAccountProvider):
        def get_account_permissions(self) -> AccountPermissions:
            self.native_account_uid = "demo-read-fallback"
            return AccountPermissions(
                can_read=True,
                can_trade=True,
                can_withdraw=False,
                can_transfer=False,
                raw_scopes=("read", "trade"),
                response_keys=("readOnly",),
            )

    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_readonly_account_provider",
        lambda _cfg: TradingKeyProvider(trade_client),
    )
    dedicated_service = BloFinSyncService(MagicMock(), dedicated)
    monkeypatch.setattr(dedicated_service._snapshots, "add", keep)
    monkeypatch.setattr(dedicated_service._audit, "record", lambda *_args, **_kwargs: None)
    refused = dedicated_service.sync(organization_id=uuid4(), user_id=uuid4(), sync_origin="worker")
    assert refused.snapshot.health_status is BloFinSyncHealthStatus.UNAVAILABLE
    assert refused.snapshot.provenance.get("error_type") == "ValueError"


def test_demo_account_active_accepts_paper_exchange_demo_for_sync_and_orders() -> None:
    assert demo_account_active(_trading_key_settings()) is True
    assert demo_account_active(_trading_key_settings(blofin_readonly_sync_enabled=True)) is True
    dedicated = _trading_key_settings(
        blofin_readonly_sync_enabled=True,
        blofin_readonly_api_key="fixture-reader",
        blofin_readonly_api_secret="fixture-reader-secret",
        blofin_readonly_api_passphrase="fixture-reader-pass",
    )
    assert demo_account_active(dedicated) is True
    assert demo_account_active(readonly_settings()) is True
    closed_host = readonly_settings().model_copy(
        update={"blofin_demo_rest_base_url": "https://example.com"}
    )
    assert demo_account_active(closed_host) is False
    real = _trading_key_settings().model_copy(update={"enable_real_trading": True})
    assert demo_account_active(real) is False
    assert real.real_trading_enabled is False


def test_demo_key_fallback_prefers_a_complete_readonly_set_and_keeps_the_host_guard() -> None:
    fallback = get_readonly_client(_trading_key_settings())
    assert isinstance(fallback, BloFinReadOnlyClient)
    assert fallback._api_key == "fixture-trading-key"
    preferred = get_readonly_client(
        _trading_key_settings(
            blofin_readonly_sync_enabled=True,
            blofin_readonly_api_key="fixture-reader",
            blofin_readonly_api_secret="fixture-reader-secret",
            blofin_readonly_api_passphrase="fixture-reader-pass",
        )
    )
    assert preferred._api_key == "fixture-reader"
    with pytest.raises(ExchangeDemoInactiveError):
        get_readonly_client(_trading_key_settings(blofin_readonly_api_secret="partial-only"))
    production = _trading_key_settings().model_copy(
        update={"blofin_demo_rest_base_url": "https://openapi.blofin.com"}
    )
    with pytest.raises(ValueError, match="production"):
        get_readonly_client(production)


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

        def latest(self, *, organization_id: object, user_id: object = None) -> SimpleNamespace:
            assert organization_id == UUID(settings.governed_blofin_demo_organization_id)
            assert user_id == UUID(settings.governed_blofin_demo_user_id)
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

        def latest(self, *, organization_id: object, user_id: object = None) -> SimpleNamespace:
            assert organization_id and user_id
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

        def latest(self, *, organization_id: object, user_id: object = None) -> None:
            assert organization_id and user_id
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


def test_each_worker_snapshot_sync_uses_a_distinct_audit_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _open_settings()
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    clock = {"now": now}
    request_ids: list[str] = []
    session = SimpleNamespace(committed=False, closed=False)

    def commit() -> None:
        session.committed = True

    session.commit = commit
    session.rollback = lambda: None
    session.close = lambda: setattr(session, "closed", True)

    class Service:
        def __init__(self, _session: object, _settings: object) -> None:
            pass

        def latest(self, *, organization_id: object, user_id: object = None) -> None:
            assert organization_id and user_id
            raise NotFoundError("none")

        def sync(self, **kwargs: object) -> SimpleNamespace:
            request_id = kwargs.get("request_id")
            assert isinstance(request_id, str)
            request_ids.append(request_id)
            health = BloFinSyncHealthStatus.OK
            return SimpleNamespace(snapshot=SimpleNamespace(health_status=health))

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr("app.services.blofin_sync_service.BloFinSyncService", Service)
    cycle = BloFinDemoSnapshotCycle(settings, clock=lambda: clock["now"])
    assert cycle().status == "ok"
    clock["now"] = now + timedelta(seconds=settings.blofin_demo_snapshot_sync_seconds * 2)
    assert cycle().status == "ok"
    assert len(request_ids) == 2
    assert request_ids[0] != request_ids[1]
    assert all(rid.startswith("blofin-snapshot-") for rid in request_ids)
    assert all(len(rid) <= 64 for rid in request_ids)


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

        def latest(self, *, organization_id: object, user_id: object = None) -> None:
            assert organization_id and user_id
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

        def latest(self, *, organization_id: object, user_id: object = None) -> None:
            assert organization_id and user_id
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

        def latest(self, *, organization_id: object, user_id: object = None) -> None:
            assert organization_id and user_id
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

        def latest(self, *, organization_id: object, user_id: object = None) -> SimpleNamespace:
            assert organization_id and user_id
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


def test_unhealthy_snapshot_still_runs_the_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _open_settings().model_copy(update={"blofin_demo_manual_mirror_enabled": True})
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    applied: list[str] = []
    commits: list[str] = []
    session = SimpleNamespace(closed=False, rollback=lambda: None)
    session.commit = lambda: commits.append("commit")
    session.close = lambda: setattr(session, "closed", True)

    class Service:
        def __init__(self, _session: object, _settings: object) -> None:
            pass

        def latest(self, *, organization_id: object, user_id: object = None) -> None:
            assert organization_id and user_id
            raise NotFoundError("none")

        def sync(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                snapshot=SimpleNamespace(health_status=BloFinSyncHealthStatus.UNAVAILABLE)
            )

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
    outcome = cycle()
    assert outcome.status == "degraded"
    assert outcome.error == "snapshot_unavailable"
    assert applied == ["apply"]
    assert commits == ["commit", "commit"]
    assert cycle._next_attempt is not None


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

        def latest(self, *, organization_id: object, user_id: object = None) -> SimpleNamespace:
            assert organization_id and user_id
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
