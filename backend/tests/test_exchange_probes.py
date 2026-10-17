"""Tests for owner-scoped BloFin demo exchange probes and gated cancel."""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import AuditEventType
from app.security.rate_limit import reset_rate_limiter
from tests.support.network_guard import block_outbound_network

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "exchange-probes-test-secret-min-32-bytes",
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

_BALANCES = [{"currency": "USDT", "balance": "1000", "available": "900"}]

_POSITIONS = [
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
    "state": "live",
    "filledSize": "0",
    "averagePrice": "",
}


def _demo_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if "query-apikey" in path:
        return httpx.Response(200, json={"code": "0", "data": [{"readOnly": 0}]})
    if "/market/instruments" in path:
        return httpx.Response(200, json={"code": "0", "data": _INSTRUMENTS})
    if "/account/balance" in path:
        return httpx.Response(200, json={"code": "0", "data": [{"details": _BALANCES}]})
    if "/account/positions" in path:
        return httpx.Response(200, json={"code": "0", "data": _POSITIONS})
    if "/account/position-mode" in path:
        return httpx.Response(200, json={"code": "0", "data": {"positionMode": "net_mode"}})
    if "/account/leverage-info" in path:
        return httpx.Response(
            200,
            json={
                "code": "0",
                "data": {
                    "instId": "BTC-USDT",
                    "marginMode": "cross",
                    "leverage": "20",
                    "positionSide": "net",
                },
            },
        )
    if "/trade/order" in path and request.method == "GET":
        return httpx.Response(200, json={"code": "0", "data": [_ORDER]})
    if "/trade/cancel-order" in path:
        return httpx.Response(200, json={"code": "0", "data": [{"orderId": "demo-order-1"}]})
    return httpx.Response(200, json={"code": "0", "data": []})


def _fail_handler(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"code": "500", "msg": "server error with demo-secret"})


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


@pytest.fixture(autouse=True)
def _no_real_network() -> Iterator[None]:
    with block_outbound_network() as attempts:
        yield
    assert not attempts, f"test reached a real network host: {attempts}"


def _build_client(settings: Settings, handler=_demo_handler) -> Iterator[TestClient]:
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

    from app.providers.exchange import factory as exchange_factory

    original_build = exchange_factory.build_blofin_client

    def _build(settings_obj, *, transport=None, **kwargs):
        mock_transport = transport or httpx.MockTransport(handler)
        return original_build(settings_obj, transport=mock_transport, **kwargs)

    # Install the mock before create_app: the provider registry is built during
    # app construction and the lifespan probe must never see a real client.
    exchange_factory.build_blofin_client = _build
    get_settings.cache_clear()
    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as client:
        yield client

    exchange_factory.build_blofin_client = original_build
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    engine.dispose()


@pytest.fixture
def demo_client() -> Iterator[TestClient]:
    yield from _build_client(Settings(_env_file=None, **_DEMO_SETTINGS))


@pytest.fixture
def internal_client() -> Iterator[TestClient]:
    yield from _build_client(Settings(_env_file=None, **_BASE))


@pytest.fixture
def failing_demo_client() -> Iterator[TestClient]:
    yield from _build_client(Settings(_env_file=None, **_DEMO_SETTINGS), handler=_fail_handler)


def _register_owner(client: TestClient, email: str = "owner@example.com") -> dict[str, str]:
    reg = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "SecurePass123!",
            "organization_name": "Exchange Probes Org",
        },
    )
    assert reg.status_code == 201, reg.text
    login = client.post(
        "/auth/login",
        json={"email": email, "password": "SecurePass123!"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _forbidden_needles() -> tuple[str, ...]:
    return (
        "demo-key",
        "demo-secret",
        "demo-pass",
        "blofin_api_key",
        "blofin_api_secret",
        "blofin_api_passphrase",
        "demo-trading-openapi",
        "access-sign",
        "access-key",
        "access-passphrase",
    )


def _assert_no_secrets(body: dict) -> None:
    raw = json.dumps(body).lower()
    for needle in _forbidden_needles():
        assert needle not in raw


def test_probes_require_owner(demo_client: TestClient) -> None:
    for path in (
        "/exchange/instruments",
        "/exchange/balances",
        "/exchange/positions",
        "/exchange/account/position-mode",
        "/exchange/account/leverage-info",
        "/exchange/orders/BTC-USDT/demo-order-1",
    ):
        response = demo_client.get(path)
        assert response.status_code == 401, path


def test_probes_return_409_in_paper_internal(internal_client: TestClient) -> None:
    headers = _register_owner(internal_client)
    for method, path in (
        ("GET", "/exchange/instruments"),
        ("GET", "/exchange/balances"),
        ("GET", "/exchange/positions"),
        ("GET", "/exchange/account/position-mode"),
        ("GET", "/exchange/account/leverage-info"),
        ("GET", "/exchange/orders/BTC-USDT/demo-order-1"),
        ("POST", "/exchange/orders/BTC-USDT/demo-order-1/cancel"),
    ):
        response = internal_client.request(method, path, headers=headers)
        assert response.status_code == 409, (method, path, response.text)
        assert response.json()["error"]["code"] == "exchange_demo_inactive"


def test_instruments_returns_sizing_fields(demo_client: TestClient) -> None:
    headers = _register_owner(demo_client)
    response = demo_client.get("/exchange/instruments?symbol=BTCUSDT", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["symbol"] == "BTCUSDT"
    assert item["inst_id"] == "BTC-USDT"
    assert item["min_size"] == "1"
    assert item["lot_size"] == "1"
    assert item["contract_size"] == "0.001"
    assert item["active"] is True
    _assert_no_secrets(body)


def test_balances_redacted_summary(demo_client: TestClient) -> None:
    headers = _register_owner(demo_client)
    response = demo_client.get("/exchange/balances", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"][0]["asset"] == "USDT"
    assert body["items"][0]["total"] == "1000"
    _assert_no_secrets(body)


def test_positions_read_only(demo_client: TestClient) -> None:
    headers = _register_owner(demo_client)
    response = demo_client.get("/exchange/positions", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"][0]["inst_id"] == "BTC-USDT"
    assert body["items"][0]["side"] == "long"
    _assert_no_secrets(body)


def test_position_mode_read_only(demo_client: TestClient) -> None:
    headers = _register_owner(demo_client)
    response = demo_client.get("/exchange/account/position-mode", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["position_mode"] == "net_mode"
    _assert_no_secrets(body)


def test_leverage_info_read_only(demo_client: TestClient) -> None:
    headers = _register_owner(demo_client)
    response = demo_client.get(
        "/exchange/account/leverage-info",
        headers=headers,
        params={"inst_id": "BTC-USDT", "margin_mode": "cross"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["inst_id"] == "BTC-USDT"
    assert body["margin_mode"] == "cross"
    assert body["leverage"] == "20"
    assert body["position_side"] == "net"
    _assert_no_secrets(body)


def test_order_status_read_only(demo_client: TestClient) -> None:
    headers = _register_owner(demo_client)
    response = demo_client.get("/exchange/orders/BTC-USDT/demo-order-1", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exchange_order_id"] == "demo-order-1"
    assert body["status"] == "live"
    _assert_no_secrets(body)


def test_cancel_is_demo_gated_and_audited(demo_client: TestClient) -> None:
    headers = _register_owner(demo_client)
    response = demo_client.post(
        "/exchange/orders/BTC-USDT/demo-order-1/cancel",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cancelled"] is True
    assert body["inst_id"] == "BTC-USDT"
    _assert_no_secrets(body)

    audit = demo_client.get(
        "/audit/events",
        headers=headers,
        params={"event_type": AuditEventType.EXCHANGE_DEMO_ORDER_CANCELLED.value},
    )
    assert audit.status_code == 200, audit.text
    events = audit.json()["items"]
    cancelled_type = AuditEventType.EXCHANGE_DEMO_ORDER_CANCELLED.value
    assert any(e["event_type"] == cancelled_type for e in events)


def test_order_status_after_cancel_falls_back_to_cancel_audit(
    demo_client: TestClient,
) -> None:
    """Transient venue GET failures after cancel return audited cancelled status."""
    cancelled = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/trade/cancel-order" in path:
            cancelled["done"] = True
            return httpx.Response(200, json={"code": "0", "data": [{"orderId": "demo-order-1"}]})
        if "/trade/order" in path and request.method == "GET" and cancelled["done"]:
            return httpx.Response(500, json={"code": "500", "msg": "demo-secret unavailable"})
        return _demo_handler(request)

    from app.core.config import Settings, get_settings
    from app.db.session import get_session
    from app.main import create_app
    from app.providers.exchange import factory as exchange_factory

    settings = Settings(_env_file=None, **_DEMO_SETTINGS)
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

    original_build = exchange_factory.build_blofin_client

    def _build(settings_obj, *, transport=None, **kwargs):
        mock_transport = transport or httpx.MockTransport(handler)
        return original_build(settings_obj, transport=mock_transport, **kwargs)

    # Install the mock before create_app: the provider registry is built during
    # app construction and the lifespan probe must never see a real client.
    exchange_factory.build_blofin_client = _build
    get_settings.cache_clear()
    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as client:
        headers = _register_owner(client)
        cancel = client.post("/exchange/orders/BTC-USDT/demo-order-1/cancel", headers=headers)
        assert cancel.status_code == 200, cancel.text

        status = client.get(
            "/exchange/orders/BTC-USDT/demo-order-1",
            headers=headers,
            params={"after_cancel": "true"},
        )
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["status"] == "cancelled"
        assert body["status_source"] == "cancel_audit_fallback"
        assert body["filled_size"] == "0"
        _assert_no_secrets(body)

        audit = client.get(
            "/audit/events",
            headers=headers,
            params={"event_type": AuditEventType.EXCHANGE_DEMO_ORDER_CANCELLED.value},
        )
        meta = audit.json()["items"][0]["redacted_metadata"]
        assert "demo-secret" not in json.dumps(meta).lower()

    exchange_factory.build_blofin_client = original_build
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    engine.dispose()


def test_order_status_without_cancel_audit_still_fails_redacted(
    failing_demo_client: TestClient,
) -> None:
    headers = _register_owner(failing_demo_client)
    response = failing_demo_client.get(
        "/exchange/orders/BTC-USDT/demo-order-1",
        headers=headers,
        params={"after_cancel": "true"},
    )
    assert response.status_code == 502, response.text
    body = response.json()
    assert body["error"]["code"] == "exchange_provider_error"
    assert "demo-secret" not in json.dumps(body).lower()


def test_provider_failure_redacted(failing_demo_client: TestClient) -> None:
    headers = _register_owner(failing_demo_client)
    response = failing_demo_client.get("/exchange/instruments", headers=headers)
    assert response.status_code == 502, response.text
    body = response.json()
    assert body["error"]["code"] == "exchange_provider_error"
    assert "demo-secret" not in json.dumps(body).lower()


def test_cancel_not_generic_live_order_route(demo_client: TestClient) -> None:
    """Cancel exists only under demo-gated /exchange/orders/.../cancel, not /execution."""
    headers = _register_owner(demo_client)
    assert demo_client.post("/execution/paper", headers=headers, json={}).status_code != 200
    assert demo_client.post("/execution/live", headers=headers).status_code in (404, 405)
