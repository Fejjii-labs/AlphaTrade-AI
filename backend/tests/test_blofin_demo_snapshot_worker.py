"""Snapshot cycle gates, fresh skip, and error backoff. No venue calls."""

import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import httpx
import pytest
import structlog
from pydantic import ValidationError

from app.controlled_activation.profile import controlled_telegram_projection
from app.core.blofin_identity import connection_binding
from app.core.blofin_readonly_access import (
    BloFinReadOnlyClient,
    get_readonly_account_provider,
    get_readonly_client,
)
from app.core.config import ExchangeMode, Settings
from app.core.deployment_safety import validate_deployment_settings
from app.core.disarmed_worker_boot import settings_are_disarmed_paper_worker
from app.core.errors import ExchangeDemoInactiveError, NotFoundError
from app.core.exchange_demo_access import get_demo_execution_provider
from app.core.execution_credentials import (
    CredentialAccessDeniedError,
    blofin_execution_authorized,
    load_blofin_execution_credentials,
)
from app.db.blofin_activity import BloFinActivityAccount
from app.market_activation.profile import live_market_activation_violations
from app.providers.exchange.base import AccountPermissions
from app.providers.exchange.blofin_account import BloFinAccountProvider
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.factory import resolve_exchange_provider
from app.schemas.common import ActorType, BloFinSyncHealthStatus
from app.services import blofin_sync_service
from app.services.blofin_activity_config import BloFinActivitySettings
from app.services.blofin_demo_manual_mirror import BloFinDemoManualMirror, MirrorReadError
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
    organization_id = uuid4()
    _pin_activity(monkeypatch, organization_id, "demo-read-fallback")
    with structlog.testing.capture_logs() as logs:
        result = service.sync(
            organization_id=organization_id, user_id=uuid4(), sync_origin="worker"
        )
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


def _pin_activity(
    monkeypatch: pytest.MonkeyPatch, organization_id: UUID, expected_uid: str
) -> None:
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_activity_settings",
        lambda: BloFinActivitySettings(
            _env_file=None,
            organization_id=organization_id,
            expected_uid=expected_uid,
        ),
    )


def _fallback_sync_service(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    *,
    uid: str,
) -> BloFinSyncService:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        data: dict[str, object] | list[object]
        if request.url.path == "/api/v1/user/query-apikey":
            data = {"readOnly": 0, "uid": uid}
        elif request.url.path == "/api/v1/account/balance":
            data = {
                "totalEquity": "10",
                "details": [{"currency": "USDT", "balance": "10", "available": "10"}],
            }
        else:
            data = []
        return httpx.Response(200, json={"code": "0", "data": data})

    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_readonly_account_provider",
        lambda cfg: get_readonly_account_provider(cfg, transport=httpx.MockTransport(handle)),
    )
    service = BloFinSyncService(MagicMock(), settings)

    def keep(row: object) -> None:
        if getattr(row, "id", None) is None:
            row.id = uuid4()  # type: ignore[attr-defined]

    monkeypatch.setattr(service._snapshots, "add", keep)
    monkeypatch.setattr(service._audit, "record", lambda *_args, **_kwargs: None)
    return service


def test_fallback_key_refuses_a_different_demo_account(monkeypatch: pytest.MonkeyPatch) -> None:
    organization_id = uuid4()
    settings = _trading_key_settings()
    _pin_activity(monkeypatch, organization_id, "pinned-demo-account")
    service = _fallback_sync_service(monkeypatch, settings, uid="other-demo-account")
    result = service.sync(organization_id=organization_id, user_id=uuid4(), sync_origin="worker")
    assert result.snapshot.health_status is BloFinSyncHealthStatus.UNAVAILABLE
    assert result.snapshot.provenance.get("error_type") == "ValueError"
    assert result.snapshot.provenance.get("native_account_uid") is None
    assert result.snapshot.balance_count == 0


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("unset_uid", "activity_expected_uid_unset"),
        ("unset_org", "activity_organization_unset"),
        ("mismatched_org", "activity_organization_mismatch"),
    ],
)
def test_fallback_refuses_without_a_matching_account_pin(
    monkeypatch: pytest.MonkeyPatch, case: str, reason: str
) -> None:
    organization_id = uuid4()
    if case == "unset_uid":
        config = BloFinActivitySettings(
            _env_file=None, organization_id=organization_id, expected_uid=""
        )
    elif case == "unset_org":
        config = BloFinActivitySettings(_env_file=None, expected_uid="pinned-demo-account")
    else:
        config = BloFinActivitySettings(
            _env_file=None, organization_id=uuid4(), expected_uid="pinned-demo-account"
        )
    monkeypatch.setattr("app.services.blofin_sync_service.get_activity_settings", lambda: config)
    calls: list[str] = []

    def provider(_cfg: object) -> None:
        calls.append("provider")
        raise AssertionError("unpinned fallback opened a client")

    monkeypatch.setattr("app.services.blofin_sync_service.get_readonly_account_provider", provider)
    service = BloFinSyncService(MagicMock(), _trading_key_settings())

    def keep(row: object) -> None:
        if getattr(row, "id", None) is None:
            row.id = uuid4()  # type: ignore[attr-defined]

    monkeypatch.setattr(service._snapshots, "add", keep)
    monkeypatch.setattr(service._audit, "record", lambda *_args, **_kwargs: None)
    refused: list[tuple[str, str]] = []
    real_warning = blofin_sync_service.logger.warning

    def record_refusal(event: str, **kwargs: object) -> None:
        if event == "blofin_fallback_sync_refused" and isinstance(kwargs.get("reason"), str):
            refused.append((event, str(kwargs["reason"])))
        real_warning(event, **kwargs)

    monkeypatch.setattr(
        "app.services.blofin_sync_service.logger.warning",
        record_refusal,
    )
    result = service.sync(organization_id=organization_id, user_id=uuid4(), sync_origin="api")
    assert calls == []
    assert result.snapshot.health_status is BloFinSyncHealthStatus.UNAVAILABLE
    assert result.snapshot.provenance.get("error_type") == "ValueError"
    assert result.snapshot.provenance.get("native_account_uid") is None
    assert result.snapshot.balance_count == 0
    assert refused == [("blofin_fallback_sync_refused", reason)]
    rendered = repr(refused)
    assert "fixture-trading-key" not in rendered
    assert "fixture-trading-secret" not in rendered
    assert "fixture-trading-pass" not in rendered
    assert "pinned-demo-account" not in rendered


def test_fallback_key_accepts_the_pinned_demo_account(monkeypatch: pytest.MonkeyPatch) -> None:
    organization_id = uuid4()
    settings = _trading_key_settings()
    _pin_activity(monkeypatch, organization_id, "pinned-demo-account")
    service = _fallback_sync_service(monkeypatch, settings, uid="pinned-demo-account")
    result = service.sync(organization_id=organization_id, user_id=uuid4(), sync_origin="worker")
    assert result.snapshot.health_status is BloFinSyncHealthStatus.OK
    assert result.snapshot.provenance["native_account_uid"] == "pinned-demo-account"
    assert result.snapshot.provenance["identity_status"] == "verified"


def _snapshot_row(organization_id: UUID, user_id: UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        organization_id=organization_id,
        user_id=user_id,
        synced_at=datetime.now(UTC),
        health_status="ok",
        is_stale=False,
        stale_reason=None,
        provider="blofin_demo",
        exchange_mode="paper_exchange_demo",
        account_snapshot={},
        positions_snapshot={"items": []},
        market_context={"symbols": []},
        provenance={"native_account_uid": "pinned-demo-account", "identity_status": "verified"},
        error_summary=None,
        position_count=0,
        balance_count=0,
    )


def test_fallback_latest_applies_the_snapshot_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    user_id = uuid4()
    settings = _trading_key_settings()
    _pin_activity(monkeypatch, organization_id, "pinned-demo-account")
    binding = connection_binding(settings, readonly=False)
    session = MagicMock()
    proof = SimpleNamespace(
        credential_binding=binding,
        identity_verified_at=datetime(2026, 10, 21, tzinfo=UTC),
        identity_error=None,
    )
    session.get.return_value = proof
    service = BloFinSyncService(session, settings)
    monkeypatch.setattr(service._snapshots, "verified_uid_for_binding", lambda *_a, **_k: None)
    row = _snapshot_row(organization_id, user_id)
    seen: dict[str, str] = {}

    def latest_for_connection(
        *_args: object, native_uid: str, **_kwargs: object
    ) -> SimpleNamespace:
        seen["uid"] = native_uid
        return row

    monkeypatch.setattr(service._snapshots, "latest_for_connection", latest_for_connection)
    item = service.latest(organization_id=organization_id, user_id=user_id)
    assert session.get.call_args.args[0] is BloFinActivityAccount
    assert session.get.call_args.args[1] == (organization_id, "demo", "pinned-demo-account")
    assert seen["uid"] == "pinned-demo-account"
    assert item.provenance["native_account_uid"] == "pinned-demo-account"

    session.get.return_value = SimpleNamespace(
        credential_binding=binding,
        identity_verified_at=datetime(2026, 10, 21, tzinfo=UTC),
        identity_error="identity_unverified",
    )
    monkeypatch.setattr(
        service._snapshots, "verified_uid_for_binding", lambda *_a, **_k: "pinned-demo-account"
    )
    with pytest.raises(NotFoundError, match="unverified"):
        service.latest(organization_id=organization_id, user_id=user_id)

    session.get.return_value = None
    monkeypatch.setattr(
        service._snapshots, "verified_uid_for_binding", lambda *_a, **_k: "other-demo-account"
    )
    with pytest.raises(NotFoundError, match="unverified"):
        service.latest(organization_id=organization_id, user_id=user_id)


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


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/v1/account/balance", None),
        ("POST", "/api/v1/trade/order", {"instId": "BTC-USDT"}),
        ("GET", "/api/v1/account/balance", {}),
        ("GET", "/api/v1/user/query-apikey", {"note": "body"}),
        ("GET", "/api/v1/trade/order", None),
        ("GET", "/api/v1/asset/withdrawal", None),
        ("GET", "/api/v1/account/balance?limit=1", None),
        ("GET", "/api/v1/account/../trade/order", None),
        ("GET", "/api/v1/account/balance/..", None),
    ],
)
def test_fallback_client_refuses_writes_bodies_and_unlisted_paths(
    method: str, path: str, body: dict[str, str] | None
) -> None:
    seen: list[httpx.Request] = []
    client = get_readonly_client(
        _trading_key_settings(),
        transport=httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(500)),
    )
    assert isinstance(client, BloFinReadOnlyClient)
    assert client._api_key == "fixture-trading-key"
    with pytest.raises(ExchangeDemoInactiveError):
        client.request(method, path, body=body, signed=True)
    assert seen == []


def test_every_public_method_of_the_readonly_client_routes_through_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = get_readonly_client(
        _trading_key_settings(),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"code": "0", "data": {"readOnly": 0, "uid": "pinned-demo-account"}}
            )
        ),
    )
    parent: list[tuple[str, str]] = []
    real = BloFinClient.request

    def wrapped(
        self: BloFinClient,
        method: str,
        path: str,
        **kwargs: object,
    ) -> object:
        parent.append((method, path))
        return real(self, method, path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(BloFinClient, "request", wrapped)
    public = sorted(
        name
        for name, member in inspect.getmembers(BloFinReadOnlyClient, inspect.isfunction)
        if not name.startswith("_")
    )
    assert public == ["request"]
    client.request("GET", "/api/v1/user/query-apikey", signed=True)
    assert parent == [("GET", "/api/v1/user/query-apikey")]
    parent.clear()
    assert client.last_success_at is not None
    assert client.last_error is None
    assert parent == []
    with pytest.raises(ExchangeDemoInactiveError):
        client.request("POST", "/api/v1/trade/order", body={"instId": "BTC-USDT"})
    assert parent == []


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
    """Closed demo gates skip the snapshot without a snapshot session.

    A non-disarmed worker still publishes boot arming posture, and that
    writer opens its own session. ``_closed_settings`` is ``environment=local``,
    so ``settings_are_disarmed_paper_worker`` is false and the posture write
    runs. ``publish_boot_arming_posture`` re-raises ``AssertionError``, which
    is why a blanket ``get_session_factory`` ban fails here. Main's focused
    selector does not collect this file unless a ``blofin_demo_snapshot`` path
    changes, so the green main job never re-ran it after that posture write
    landed. The same assertion failed the PR 304 backend job once the file
    was selected. Arming persistence stays covered by ``test_arming_visibility``.
    """

    monkeypatch.setenv("BLOFIN_ACTIVITY_ENABLED", "false")
    monkeypatch.setattr(
        "app.workers.paper_worker._build_armed_supervisor",
        lambda *_args: _idle_supervisor(),
    )
    published: list[str] = []

    def posture(*_args: object, **_kwargs: object) -> None:
        published.append("posture")
        return None

    monkeypatch.setattr("app.services.arming_visibility.publish_boot_arming_posture", posture)

    def forbidden() -> None:
        raise AssertionError("skipped snapshot opened a database")

    monkeypatch.setattr("app.db.session.get_session_factory", forbidden)
    worker = build_paper_worker_supervisor(
        _closed_settings().model_copy(update={"blofin_demo_snapshot_sync_enabled": True})
    )
    assert published == ["posture"]
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


def _internal_key_settings(**updates: object) -> Settings:
    org, user = uuid4(), uuid4()
    values: dict[str, object] = {
        "environment": "local",
        "execution_mode": "paper",
        "enable_real_trading": False,
        "exchange_mode": "paper_internal",
        "blofin_demo_enabled": False,
        "provider_mode": "mock",
        "blofin_demo_snapshot_sync_enabled": True,
        "blofin_demo_snapshot_internal_read_enabled": True,
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


def _forbid_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> None:
        raise AssertionError("skipped snapshot opened a database")

    monkeypatch.setattr("app.db.session.get_session_factory", forbidden)


def test_paper_exchange_demo_fallback_does_not_require_the_internal_flag() -> None:
    settings = _trading_key_settings()
    assert settings.blofin_demo_snapshot_internal_read_enabled is False
    assert settings.blofin_demo_read_fallback_ready is True
    assert settings.blofin_paper_internal_snapshot_read_ready is False
    client = get_readonly_client(settings)
    assert isinstance(client, BloFinReadOnlyClient)
    assert client._api_key == "fixture-trading-key"


def test_paper_internal_prefers_a_complete_readonly_set() -> None:
    settings = _internal_key_settings(
        blofin_readonly_sync_enabled=True,
        blofin_readonly_api_key="fixture-reader",
        blofin_readonly_api_secret="fixture-reader-secret",
        blofin_readonly_api_passphrase="fixture-reader-pass",
    )
    assert settings.blofin_paper_internal_snapshot_read_ready is False
    client = get_readonly_client(settings)
    assert client._api_key == "fixture-reader"


def test_paper_internal_cycle_syncs_when_every_gate_is_open() -> None:
    settings = _internal_key_settings()
    seen: list[tuple[UUID, UUID]] = []
    cycle = BloFinDemoSnapshotCycle(settings)

    def sync_once(organization_id: UUID, user_id: UUID) -> str:
        seen.append((organization_id, user_id))
        return "ok"

    cycle._sync_once = sync_once  # type: ignore[method-assign]
    outcome = cycle()
    assert outcome.status == "ok"
    assert outcome.error == ""
    assert seen == [
        (
            UUID(settings.governed_blofin_demo_organization_id),
            UUID(settings.governed_blofin_demo_user_id),
        )
    ]


def test_paper_internal_snapshot_runs_when_every_gate_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _internal_key_settings()
    organization_id = UUID(settings.governed_blofin_demo_organization_id)
    _pin_activity(monkeypatch, organization_id, "pinned-demo-account")
    service = _fallback_sync_service(monkeypatch, settings, uid="pinned-demo-account")
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_demo_account_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("trading provider")),
    )
    monkeypatch.setattr(
        "app.services.blofin_sync_service.ensure_demo_exchange_access",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("trading access")),
    )
    with structlog.testing.capture_logs() as logs:
        result = service.sync(
            organization_id=organization_id,
            user_id=UUID(settings.governed_blofin_demo_user_id),
            sync_origin="worker",
        )
    assert result.snapshot.health_status is BloFinSyncHealthStatus.OK
    assert result.snapshot.exchange_mode == "paper_internal"
    assert result.snapshot.provenance["identity_status"] == "verified"
    assert result.snapshot.provenance["order_mutations"] is False
    assert any(
        item.get("event") == "blofin_snapshot_identity" and item.get("result") == "verified"
        for item in logs
    )
    rendered = repr(logs)
    assert "pinned-demo-account" not in rendered
    assert "fixture-trading-key" not in rendered
    assert "fixture-trading-secret" not in rendered
    assert "fixture-trading-pass" not in rendered
    assert demo_account_active(settings) is True
    assert settings.exchange_demo_active is False


def test_paper_internal_identity_mismatch_is_logged_without_the_uid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    settings = _internal_key_settings(
        governed_blofin_demo_organization_id=str(organization_id),
    )
    _pin_activity(monkeypatch, organization_id, "pinned-demo-account")
    service = _fallback_sync_service(monkeypatch, settings, uid="other-demo-account")
    with structlog.testing.capture_logs() as logs:
        result = service.sync(
            organization_id=organization_id, user_id=uuid4(), sync_origin="worker"
        )
    assert result.snapshot.health_status is BloFinSyncHealthStatus.UNAVAILABLE
    assert result.snapshot.provenance.get("native_account_uid") is None
    assert any(
        item.get("event") == "blofin_snapshot_identity" and item.get("result") == "mismatch"
        for item in logs
    )
    assert any(
        item.get("event") == "blofin_fallback_sync_refused"
        and item.get("reason") == "native_account_mismatch"
        for item in logs
    )
    rendered = repr(logs)
    assert "pinned-demo-account" not in rendered
    assert "other-demo-account" not in rendered
    assert "fixture-trading-key" not in rendered
    assert "fixture-trading-secret" not in rendered
    assert "fixture-trading-pass" not in rendered


@pytest.mark.parametrize(
    ("update", "reason"),
    [
        ({"blofin_demo_snapshot_internal_read_enabled": False}, "internal_snapshot_read_disabled"),
        ({"blofin_demo_snapshot_sync_enabled": False}, "snapshot_sync_disabled"),
        ({"blofin_api_key": ""}, "demo_credentials_required"),
        ({"blofin_api_secret": ""}, "demo_credentials_required"),
        ({"blofin_api_passphrase": ""}, "demo_credentials_required"),
        ({"blofin_demo_rest_base_url": ""}, "demo_rest_url_required"),
        ({"governed_blofin_demo_organization_id": ""}, "demo_owner_unpinned"),
        ({"governed_blofin_demo_user_id": "not-a-uuid"}, "demo_owner_unpinned"),
        ({"blofin_readonly_api_key": "partial-only"}, "readonly_credentials_required"),
    ],
)
def test_paper_internal_snapshot_skips_when_a_gate_is_missing(
    monkeypatch: pytest.MonkeyPatch, update: dict[str, object], reason: str
) -> None:
    _forbid_database(monkeypatch)
    outcome = BloFinDemoSnapshotCycle(_internal_key_settings(**update))()
    assert outcome.status == "skipped"
    assert outcome.error == reason


def test_paper_internal_snapshot_skips_when_execution_is_not_paper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_database(monkeypatch)
    settings = _internal_key_settings().model_copy(update={"execution_mode": "trade"})
    outcome = BloFinDemoSnapshotCycle(settings)()
    assert outcome.status == "skipped"
    assert outcome.error == "paper_execution_required"


def test_paper_internal_snapshot_skips_when_real_trading_is_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _forbid_database(monkeypatch)
    settings = _internal_key_settings().model_copy(update={"enable_real_trading": True})
    outcome = BloFinDemoSnapshotCycle(settings)()
    assert outcome.status == "skipped"
    assert outcome.error == "paper_execution_required"
    assert settings.real_trading_enabled is False


def test_non_demo_url_is_refused_for_the_internal_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="allowlisted BloFin demo host"):
        _internal_key_settings(blofin_demo_rest_base_url="https://example.com")
    with pytest.raises(ValidationError, match="allowlisted BloFin demo host"):
        _internal_key_settings(blofin_demo_rest_base_url="http://demo-trading-openapi.blofin.com")
    Settings(
        _env_file=None,
        environment="local",
        exchange_mode="paper_internal",
        blofin_demo_rest_base_url="https://example.com",
    )
    _forbid_database(monkeypatch)
    refused = _internal_key_settings().model_copy(
        update={"blofin_demo_rest_base_url": "https://example.com"}
    )
    outcome = BloFinDemoSnapshotCycle(refused)()
    assert outcome.status == "skipped"
    assert outcome.error == "demo_host_refused"
    with pytest.raises(ValueError, match="allowlisted demo host"):
        get_readonly_client(refused)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/trade/order"),
        ("POST", "/api/v1/trade/cancel-order"),
        ("DELETE", "/api/v1/trade/order"),
        ("PUT", "/api/v1/account/set-leverage"),
        ("GET", "/api/v1/trade/order"),
    ],
)
def test_paper_internal_snapshot_cannot_reach_a_write_or_order_call(method: str, path: str) -> None:
    settings = _internal_key_settings()
    assert settings.exchange_mode is ExchangeMode.PAPER_INTERNAL
    assert settings.exchange_demo_active is False
    assert blofin_execution_authorized(settings) is False
    with pytest.raises(CredentialAccessDeniedError):
        load_blofin_execution_credentials(settings)
    resolved = resolve_exchange_provider(settings)
    assert resolved.execution is None
    assert resolved.is_demo is False
    assert resolved.account is None
    with pytest.raises(ExchangeDemoInactiveError):
        get_demo_execution_provider(settings)
    seen: list[httpx.Request] = []
    client = get_readonly_client(
        settings,
        activity_history=True,
        paper_internal_snapshot=True,
        transport=httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(500)),
    )
    with pytest.raises(ExchangeDemoInactiveError):
        client.request(method, path, body={"instId": "BTC-USDT"}, signed=True)
    assert seen == []


def test_activity_still_requires_the_dedicated_readonly_key() -> None:
    from app.services.blofin_activity_service import activity_provider

    settings = _internal_key_settings()
    with pytest.raises(ExchangeDemoInactiveError, match="Dedicated BloFin read-only"):
        activity_provider(settings)
    with pytest.raises(ExchangeDemoInactiveError, match="Dedicated BloFin read-only"):
        get_readonly_client(settings, activity_history=True)


def test_paper_internal_demo_enabled_does_not_open_demo_execution() -> None:
    settings = Settings(
        _env_file=None,
        environment="local",
        execution_mode="paper",
        exchange_mode="paper_internal",
        blofin_demo_enabled=True,
    )
    assert settings.exchange_demo_active is False
    assert blofin_execution_authorized(settings) is False
    resolved = resolve_exchange_provider(settings)
    assert resolved.execution is None
    assert resolved.is_demo is False
    with pytest.raises(CredentialAccessDeniedError):
        load_blofin_execution_credentials(settings)


def test_telegram_projection_and_internal_snapshot_pass_deployment_safety() -> None:
    org, user = str(uuid4()), str(uuid4())
    settings = Settings(
        _env_file=None,
        environment="staging",
        jwt_secret="x" * 32,
        database_url="postgresql+psycopg://user:pass@db.example.com:5432/alphatrade",
        redis_url="redis://redis.example.com:6379/0",
        qdrant_url="https://qdrant.example.com",
        openai_api_key="sk-test-not-a-real-key",
        cors_origins="https://app.example.com",
        auth_refresh_cookie_enabled=True,
        auth_cookie_secure=True,
        auth_cookie_samesite="none",
        enable_real_trading=False,
        execution_mode="paper",
        exchange_mode="paper_internal",
        provider_mode="fallback",
        rate_limit_use_redis=True,
        rate_limit_allow_in_memory_fallback=False,
        trusted_proxy_hops=1,
        debug=False,
        perpetual_evidence_source="binance_usdm",
        market_data_futures_base_url="https://fapi.binance.com",
        watcher_orchestration_enabled=True,
        watcher_paper_staging_activation=True,
        telegram_interaction_enabled=True,
        telegram_paper_activation_armed=True,
        telegram_inbound_mode="polling",
        telegram_bot_id="bot-100",
        telegram_chat_id="tg-chat-1",
        telegram_bot_token="123456789:AAHtestTokenValueForStagingPackage",
        telegram_alerts_enabled=False,
        automatic_telegram_delivery_enabled=False,
        telegram_network_permitted=True,
        blofin_demo_enabled=False,
        blofin_demo_snapshot_sync_enabled=True,
        blofin_demo_snapshot_internal_read_enabled=True,
        blofin_api_key="fixture-trading-key",
        blofin_api_secret="fixture-trading-secret",
        blofin_api_passphrase="fixture-trading-pass",
        blofin_demo_rest_base_url="https://demo-trading-openapi.blofin.com",
        governed_blofin_demo_organization_id=org,
        governed_blofin_demo_user_id=user,
    )
    assert controlled_telegram_projection(settings) is True
    assert settings.exchange_mode is ExchangeMode.PAPER_INTERNAL
    assert settings.blofin_paper_internal_snapshot_read_ready is True
    assert settings.exchange_demo_active is False
    assert blofin_execution_authorized(settings) is False
    assert settings_are_disarmed_paper_worker(settings) is False
    assert live_market_activation_violations(settings) == []
    validate_deployment_settings(settings)
    with pytest.raises(ValidationError, match="blofin_demo_enabled must be false"):
        Settings.model_validate(settings.model_dump() | {"blofin_demo_enabled": True})


_IDENTITY = "/api/v1/user/query-apikey"


class _PinClient:
    """Records mirror GETs. Account reads are refused until the test allows them."""

    def __init__(self, *, uid: str | None, account_reads: bool) -> None:
        self.paths: list[str] = []
        self._uid = uid
        self._account_reads = account_reads

    def request(
        self,
        method: str,
        path: str,
        *,
        params: object = None,
        body: object = None,
        signed: bool = False,
        before_send: object = None,
    ) -> object:
        assert method == "GET"
        self.paths.append(path)
        if path == _IDENTITY:
            assert self._uid is not None
            return {"readOnly": 0, "uid": self._uid}
        if not self._account_reads:
            raise AssertionError(path)
        if path in {"/api/v1/market/instruments", "/api/v1/account/positions"}:
            return []
        raise MirrorReadError("stopped after the pin")


def _mirror_settings(**updates: object) -> Settings:
    settings = _internal_key_settings(blofin_demo_manual_mirror_enabled=True, **updates)
    assert settings.blofin_paper_internal_main_key_selected is True
    return settings


def _apply_mirror(client: _PinClient, settings: Settings, organization_id: UUID) -> None:
    BloFinDemoManualMirror(client, now=datetime(2026, 10, 21, tzinfo=UTC)).apply(
        MagicMock(),
        organization_id=organization_id,
        user_id=uuid4(),
        settings=settings,
    )


def _assert_secrets_absent(logs: list[object], *extra: str) -> None:
    rendered = repr(logs)
    for secret in (
        "fixture-trading-key",
        "fixture-trading-secret",
        "fixture-trading-pass",
        "pinned-demo-account",
        "other-demo-account",
        *extra,
    ):
        assert secret not in rendered


@pytest.mark.parametrize(
    ("case", "reason", "match"),
    [
        ("unset_org", "activity_organization_unset", "BLOFIN_ACTIVITY_ORGANIZATION_ID"),
        ("unset_uid", "activity_expected_uid_unset", "BLOFIN_ACTIVITY_EXPECTED_UID"),
        ("mismatched_org", "activity_organization_mismatch", "does not match"),
    ],
)
def test_internal_mirror_refuses_before_any_read_when_the_pin_is_missing(
    monkeypatch: pytest.MonkeyPatch, case: str, reason: str, match: str
) -> None:
    settings = _mirror_settings()
    organization_id = UUID(settings.governed_blofin_demo_organization_id)
    if case == "unset_uid":
        config = BloFinActivitySettings(
            _env_file=None, organization_id=organization_id, expected_uid=""
        )
    elif case == "unset_org":
        config = BloFinActivitySettings(_env_file=None, expected_uid="pinned-demo-account")
    else:
        config = BloFinActivitySettings(
            _env_file=None, organization_id=uuid4(), expected_uid="pinned-demo-account"
        )
    monkeypatch.setattr("app.services.blofin_sync_service.get_activity_settings", lambda: config)
    client = _PinClient(uid=None, account_reads=False)
    with structlog.testing.capture_logs() as logs, pytest.raises(ValueError, match=match):
        _apply_mirror(client, settings, organization_id)
    assert client.paths == []
    assert any(
        item.get("event") == "blofin_fallback_sync_refused" and item.get("reason") == reason
        for item in logs
    )
    _assert_secrets_absent(logs)


def test_internal_mirror_refuses_a_uid_mismatch_after_the_identity_get_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _mirror_settings()
    organization_id = UUID(settings.governed_blofin_demo_organization_id)
    _pin_activity(monkeypatch, organization_id, "pinned-demo-account")
    client = _PinClient(uid="other-demo-account", account_reads=False)
    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(ValueError, match="does not match connection pin"),
    ):
        _apply_mirror(client, settings, organization_id)
    assert client.paths == [_IDENTITY]
    assert any(
        item.get("event") == "blofin_snapshot_identity" and item.get("result") == "mismatch"
        for item in logs
    )
    assert any(
        item.get("event") == "blofin_fallback_sync_refused"
        and item.get("reason") == "native_account_mismatch"
        for item in logs
    )
    _assert_secrets_absent(logs)


def test_internal_mirror_refuses_an_unverified_uid_before_account_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _mirror_settings()
    organization_id = UUID(settings.governed_blofin_demo_organization_id)
    _pin_activity(monkeypatch, organization_id, "pinned-demo-account")
    client = _PinClient(uid="", account_reads=False)
    with pytest.raises(ValueError, match="UID is unverified"):
        _apply_mirror(client, settings, organization_id)
    assert client.paths == [_IDENTITY]


def test_internal_mirror_runs_the_pin_before_account_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _mirror_settings()
    organization_id = UUID(settings.governed_blofin_demo_organization_id)
    _pin_activity(monkeypatch, organization_id, "pinned-demo-account")
    client = _PinClient(uid="pinned-demo-account", account_reads=True)
    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(MirrorReadError, match="stopped after the pin"),
    ):
        _apply_mirror(client, settings, organization_id)
    assert client.paths == [
        _IDENTITY,
        "/api/v1/market/instruments",
        "/api/v1/account/positions",
        "/api/v1/account/positions-history",
    ]
    assert any(
        item.get("event") == "blofin_snapshot_identity" and item.get("result") == "verified"
        for item in logs
    )
    _assert_secrets_absent(logs)


def test_disabled_internal_mirror_does_not_read_or_pin() -> None:
    settings = _internal_key_settings(blofin_demo_manual_mirror_enabled=False)
    client = _PinClient(uid=None, account_reads=False)
    report = BloFinDemoManualMirror(client, now=datetime(2026, 10, 21, tzinfo=UTC)).apply(
        MagicMock(),
        organization_id=uuid4(),
        user_id=uuid4(),
        settings=settings,
    )
    assert report.created == 0
    assert client.paths == []


@pytest.mark.parametrize("mode", ["paper_exchange_demo", "dedicated_readonly"])
def test_other_mirror_paths_do_not_take_the_internal_pin(mode: str) -> None:
    if mode == "paper_exchange_demo":
        settings = _trading_key_settings().model_copy(
            update={"blofin_demo_manual_mirror_enabled": True}
        )
    else:
        settings = _open_settings().model_copy(update={"blofin_demo_manual_mirror_enabled": True})
    assert settings.blofin_paper_internal_main_key_selected is False
    client = _PinClient(uid=None, account_reads=True)
    with pytest.raises(MirrorReadError, match="stopped after the pin"):
        _apply_mirror(client, settings, uuid4())
    assert _IDENTITY not in client.paths
    assert client.paths[0] == "/api/v1/market/instruments"


def _fresh_internal_cycle(
    monkeypatch: pytest.MonkeyPatch, client: _PinClient
) -> tuple[BloFinDemoSnapshotCycle, list[str], list[str]]:
    settings = _mirror_settings()
    now = datetime(2026, 10, 21, 12, tzinfo=UTC)
    synced: list[str] = []
    recorded: list[str] = []
    session = SimpleNamespace(closed=False, rollback=lambda: None, commit=lambda: None)
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
            synced.append("sync")
            raise AssertionError("fresh snapshot must not sync")

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)
    monkeypatch.setattr("app.services.blofin_sync_service.BloFinSyncService", Service)
    monkeypatch.setattr(
        "app.core.blofin_readonly_access.get_readonly_client",
        lambda *_args, **_kwargs: client,
    )

    def remember(*_args: object, **kwargs: object) -> None:
        recorded.append(str(kwargs.get("outcome")))

    monkeypatch.setattr("app.services.blofin_demo_manual_mirror.record_mirror_cycle", remember)
    cycle = BloFinDemoSnapshotCycle(settings, clock=lambda: now)
    return cycle, synced, recorded


def test_fresh_internal_snapshot_refuses_the_mirror_before_any_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _PinClient(uid=None, account_reads=False)
    cycle, synced, recorded = _fresh_internal_cycle(monkeypatch, client)
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_activity_settings",
        lambda: BloFinActivitySettings(_env_file=None),
    )
    with structlog.testing.capture_logs() as logs:
        outcome = cycle()
    assert outcome.status == "ok"
    assert synced == []
    assert client.paths == []
    assert recorded == ["failed"]
    assert cycle._next_attempt is None
    assert any(
        item.get("event") == "blofin_fallback_sync_refused"
        and item.get("reason") == "activity_organization_unset"
        for item in logs
    )
    _assert_secrets_absent(logs)


def test_fresh_internal_snapshot_refuses_a_uid_mismatch_before_account_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _PinClient(uid="other-demo-account", account_reads=False)
    cycle, synced, recorded = _fresh_internal_cycle(monkeypatch, client)
    organization_id = UUID(cycle.settings.governed_blofin_demo_organization_id)
    _pin_activity(monkeypatch, organization_id, "pinned-demo-account")
    with structlog.testing.capture_logs() as logs:
        outcome = cycle()
    assert outcome.status == "ok"
    assert synced == []
    assert client.paths == [_IDENTITY]
    assert recorded == ["failed"]
    assert cycle._next_attempt is None
    assert any(
        item.get("event") == "blofin_snapshot_identity" and item.get("result") == "mismatch"
        for item in logs
    )
    _assert_secrets_absent(logs)


def test_fresh_internal_snapshot_checks_the_uid_before_the_mirror_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _PinClient(uid="pinned-demo-account", account_reads=True)
    cycle, synced, recorded = _fresh_internal_cycle(monkeypatch, client)
    organization_id = UUID(cycle.settings.governed_blofin_demo_organization_id)
    _pin_activity(monkeypatch, organization_id, "pinned-demo-account")
    with structlog.testing.capture_logs() as logs:
        outcome = cycle()
    assert outcome.status == "ok"
    assert synced == []
    assert client.paths[0] == _IDENTITY
    assert client.paths == [
        _IDENTITY,
        "/api/v1/market/instruments",
        "/api/v1/account/positions",
        "/api/v1/account/positions-history",
    ]
    assert recorded == ["failed"]
    assert cycle._next_attempt is None
    assert any(
        item.get("event") == "blofin_snapshot_identity" and item.get("result") == "verified"
        for item in logs
    )
    _assert_secrets_absent(logs)
