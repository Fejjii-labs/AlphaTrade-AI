"""Tests for GET /exchange/diagnostics/summary (owner-scoped, read-only)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import suppress
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import ExchangeMode, ExecutionMode, Settings, get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.providers.base import ProviderHealth
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType
from app.security.rate_limit import reset_rate_limiter
from app.services.audit_service import AuditService
from app.services.exchange_diagnostics_service import (
    InstrumentProbe,
    LeverageProbe,
    compute_readiness,
)
from tests.support.network_guard import block_outbound_network

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "exchange-diagnostics-test-secret-min-32-bytes",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
}

_DEMO_SETTINGS = {
    **_BASE,
    "exchange_mode": "paper_exchange_demo",
    "blofin_demo_enabled": True,
    "blofin_api_key": "demo-key",
    "blofin_api_secret": "demo-secret",
    "blofin_api_passphrase": "demo-pass",
    "blofin_demo_rest_base_url": "https://demo-trading-openapi.blofin.com",
    "blofin_max_retries": 1,
}

_INSTRUMENTS = [
    {
        "instId": "BTC-USDT",
        "baseCurrency": "BTC",
        "quoteCurrency": "USDT",
        "instType": "SWAP",
        "tickSize": "0.1",
        "lotSize": "1",
        "minSize": "1",
        "contractValue": "0.001",
        "state": "live",
    }
]

_POSITIONS_EMPTY: list[dict[str, str]] = []

_POSITIONS_OPEN = [
    {
        "instId": "BTC-USDT",
        "pos": "1",
        "positionSide": "long",
        "avgPx": "65000",
        "markPx": "65100",
        "upl": "0.1",
        "leverage": "3",
    }
]

_ORDER = {
    "orderId": "demo-order-1",
    "clientOrderId": "idem-123",
    "state": "filled",
    "filledSize": "1",
    "averagePrice": "65000",
}


def _demo_handler(
    request: httpx.Request,
    *,
    positions: list[dict[str, str]] | None = None,
) -> httpx.Response:
    path = request.url.path
    if "query-apikey" in path:
        return httpx.Response(200, json={"code": "0", "data": [{"readOnly": 0}]})
    if "/market/instruments" in path:
        return httpx.Response(200, json={"code": "0", "data": _INSTRUMENTS})
    if "/account/balance" in path:
        return httpx.Response(200, json={"code": "0", "data": [{"details": []}]})
    if "/account/positions" in path:
        payload = positions if positions is not None else _POSITIONS_EMPTY
        return httpx.Response(200, json={"code": "0", "data": payload})
    if "/account/position-mode" in path:
        return httpx.Response(
            200,
            json={"code": "0", "data": {"positionMode": "long_short_mode"}},
        )
    if "/account/leverage-info" in path:
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": {
                    "instId": "BTC-USDT",
                    "marginMode": "cross",
                    "leverage": "3",
                    "positionSide": "net",
                },
            },
        )
    if "/trade/order" in path and request.method == "GET":
        return httpx.Response(200, json={"code": "0", "data": [_ORDER]})
    return httpx.Response(200, json={"code": "0", "data": []})


@pytest.fixture(autouse=True)
def _no_real_network() -> Iterator[None]:
    with block_outbound_network() as attempts:
        yield
    assert not attempts, f"test reached a real network host: {attempts}"


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


def _build_client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from app.providers.exchange import factory as exchange_factory

    original_build = exchange_factory.build_blofin_client

    def _build(settings_obj, *, transport=None, **kwargs):
        return original_build(
            settings_obj,
            transport=transport or httpx.MockTransport(lambda req: _demo_handler(req)),
            **kwargs,
        )

    monkeypatch.setattr(exchange_factory, "build_blofin_client", _build)

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

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    get_settings.cache_clear()
    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    engine.dispose()


@pytest.fixture
def demo_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _build_client(Settings(**_DEMO_SETTINGS), monkeypatch)


@pytest.fixture
def internal_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _build_client(Settings(**_BASE), monkeypatch)


def _register_owner(
    client: TestClient,
    email: str = "owner@example.com",
) -> tuple[dict[str, str], uuid.UUID, uuid.UUID]:
    reg = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "SecurePass123!",
            "organization_name": "Exchange Org",
        },
    )
    assert reg.status_code == 201, reg.text
    login = client.post(
        "/auth/login",
        json={"email": email, "password": "SecurePass123!"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["tokens"]["access_token"]
    org_id = uuid.UUID(reg.json()["organization"]["id"])
    user_id = uuid.UUID(reg.json()["user"]["id"])
    return {"Authorization": f"Bearer {token}"}, org_id, user_id


def _record_mirror_failure(
    client: TestClient,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    override = client.app.dependency_overrides[get_session]
    session_gen = override()
    session = next(session_gen)
    try:
        audit = AuditService(session)
        audit.record(
            AuditRecordCreate(
                request_id="mirror-fail-key",
                trace_id="mirror-fail-key",
                event_type=AuditEventType.EXCHANGE_DEMO_ORDER_FAILED,
                resource_type="exchange_order",
                resource_id=str(uuid.uuid4()),
                organization_id=org_id,
                user_id=user_id,
                actor_type=ActorType.SYSTEM,
                metadata={
                    "venue_error_code": "51008",
                    "venue_error_message": "Order price is out of range with demo-secret",
                    "inst_id": "BTC-USDT",
                },
            )
        )
        session.commit()
    finally:
        with suppress(StopIteration):
            next(session_gen)


def test_diagnostics_requires_owner(demo_client: TestClient) -> None:
    response = demo_client.get("/exchange/diagnostics/summary")
    assert response.status_code == 401


def test_diagnostics_blocked_real_trading_response(
    demo_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = demo_client.app.state.settings
    monkeypatch.setattr(type(settings), "real_trading_enabled", property(lambda _self: True))
    headers, _, _ = _register_owner(demo_client, email="unsafe@example.com")
    response = demo_client.get("/exchange/diagnostics/summary", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["readiness"] == "blocked"
    assert body["real_trading_enabled"] is True


def test_diagnostics_internal_mode_blocked(internal_client: TestClient) -> None:
    headers, _, _ = _register_owner(internal_client)
    response = internal_client.get("/exchange/diagnostics/summary", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["readiness"] == "blocked"
    assert body["exchange_mode"] == "paper_internal"
    assert body["demo_active"] is False


def test_diagnostics_ready_on_healthy_demo(demo_client: TestClient) -> None:
    headers, _, _ = _register_owner(demo_client)
    response = demo_client.get("/exchange/diagnostics/summary", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["readiness"] == "ready"
    assert body["exchange_mode"] == "paper_exchange_demo"
    assert body["execution_mode"] == "paper"
    assert body["real_trading_enabled"] is False
    assert body["demo_active"] is True
    assert body["provider_health"] == "healthy"
    assert body["venue_positions_count"] == 0
    assert body["position_mode"] == "long_short_mode"
    assert body["instrument"]["active"] is True
    assert body["leverage"]["leverage"] == "3"
    assert body["worker_enabled"] is False
    assert body["telegram_enabled"] is False


def test_diagnostics_redacts_errors(demo_client: TestClient) -> None:
    headers, org_id, user_id = _register_owner(demo_client, email="mirror-fail@example.com")
    _record_mirror_failure(demo_client, org_id=org_id, user_id=user_id)

    response = demo_client.get("/exchange/diagnostics/summary", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    raw = json.dumps(body).lower()
    assert body["last_demo_mirror_result"] == "failed"
    assert body["last_demo_mirror_error_code"] == "51008"
    assert "demo-secret" not in raw
    assert body["readiness"] == "degraded"


def test_diagnostics_blocked_when_venue_positions_open(
    demo_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.exchange import factory as exchange_factory

    original_build = exchange_factory.build_blofin_client

    def _build(settings_obj, *, transport=None, **kwargs):
        return original_build(
            settings_obj,
            transport=httpx.MockTransport(
                lambda req: _demo_handler(req, positions=_POSITIONS_OPEN),
            ),
            **kwargs,
        )

    monkeypatch.setattr(exchange_factory, "build_blofin_client", _build)

    headers, _, _ = _register_owner(demo_client, email="positions@example.com")
    response = demo_client.get("/exchange/diagnostics/summary", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["readiness"] == "blocked"
    assert body["venue_positions_count"] == 1
    assert any("venue positions" in warning.lower() for warning in body["warnings"])


def test_compute_readiness_rules() -> None:
    settings = Settings(**_DEMO_SETTINGS)
    ready = compute_readiness(
        settings=settings,
        provider_health=ProviderHealth.HEALTHY.value,
        position_mode="long_short_mode",
        instrument=InstrumentProbe(active=True, probe_ok=True),
        leverage=LeverageProbe(leverage=Decimal("3"), probe_ok=True),
        venue_positions_count=0,
        order_status_probe_ok=True,
        last_demo_mirror_result="created",
        warnings=[],
    )
    assert ready == "ready"

    blocked_positions = compute_readiness(
        settings=settings,
        provider_health=ProviderHealth.HEALTHY.value,
        position_mode="long_short_mode",
        instrument=InstrumentProbe(active=True, probe_ok=True),
        leverage=LeverageProbe(leverage=Decimal("3"), probe_ok=True),
        venue_positions_count=2,
        order_status_probe_ok=True,
        last_demo_mirror_result=None,
        warnings=[],
    )
    assert blocked_positions == "blocked"

    class _UnsafeSettings:
        real_trading_enabled = True
        exchange_mode = ExchangeMode.PAPER_EXCHANGE_DEMO
        execution_mode = ExecutionMode.TRADE
        exchange_demo_active = True

    blocked_real = compute_readiness(
        settings=_UnsafeSettings(),  # type: ignore[arg-type]
        provider_health=ProviderHealth.HEALTHY.value,
        position_mode="long_short_mode",
        instrument=InstrumentProbe(active=True, probe_ok=True),
        leverage=LeverageProbe(leverage=Decimal("3"), probe_ok=True),
        venue_positions_count=0,
        order_status_probe_ok=True,
        last_demo_mirror_result=None,
        warnings=[],
    )
    assert blocked_real == "blocked"

    degraded = compute_readiness(
        settings=settings,
        provider_health=ProviderHealth.HEALTHY.value,
        position_mode="long_short_mode",
        instrument=InstrumentProbe(active=True, probe_ok=True),
        leverage=LeverageProbe(probe_ok=False),
        venue_positions_count=0,
        order_status_probe_ok=True,
        last_demo_mirror_result=None,
        warnings=["Leverage probe failed."],
    )
    assert degraded == "degraded"

    degraded_provider = compute_readiness(
        settings=settings,
        provider_health=ProviderHealth.DEGRADED.value,
        position_mode="long_short_mode",
        instrument=InstrumentProbe(active=True, probe_ok=True),
        leverage=LeverageProbe(leverage=Decimal("3"), probe_ok=True),
        venue_positions_count=0,
        order_status_probe_ok=True,
        last_demo_mirror_result=None,
        warnings=[],
    )
    assert degraded_provider == "degraded"

    blocked_unhealthy = compute_readiness(
        settings=settings,
        provider_health=ProviderHealth.UNAVAILABLE.value,
        position_mode="long_short_mode",
        instrument=InstrumentProbe(active=True, probe_ok=True),
        leverage=LeverageProbe(leverage=Decimal("3"), probe_ok=True),
        venue_positions_count=0,
        order_status_probe_ok=True,
        last_demo_mirror_result=None,
        warnings=[],
    )
    assert blocked_unhealthy == "blocked"
