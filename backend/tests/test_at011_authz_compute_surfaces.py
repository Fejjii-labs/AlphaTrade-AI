"""AT-011: authz for compute surfaces and OpenAPI docs gating."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Environment, Settings, get_settings
from app.db.base import Base
from app.db.models import Membership
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import MembershipRole
from app.security.rate_limit import get_rate_limiter, reset_rate_limiter

_STAGING_BASE = {
    "environment": "staging",
    "jwt_secret": "x" * 32,
    "database_url": "postgresql+psycopg://user:pass@db.example.com:5432/alphatrade",
    "redis_url": "redis://redis.example.com:6379/0",
    "qdrant_url": "https://qdrant.example.com",
    "openai_api_key": "sk-test-not-a-real-key",
    "cors_origins": "https://app.example.com",
    "auth_refresh_cookie_enabled": True,
    "auth_cookie_secure": True,
    "auth_cookie_samesite": "none",
    "enable_real_trading": False,
    "execution_mode": "paper",
    "provider_mode": "fallback",
    "rate_limit_use_redis": True,
    "rate_limit_allow_in_memory_fallback": False,
    "trusted_proxy_hops": 1,
    "debug": False,
}

_RISK_CHECK_PAYLOAD = {
    "symbol": "BTCUSDT",
    "direction": "long",
    "entry_price": "60000",
    "position_size": "0.01",
    "leverage": "3",
    "account_equity": "10000",
    "stop_loss": "58000",
}

_RISK_SIZE_PAYLOAD = {
    "entry_price": "60000",
    "invalidation_level": "58500",
    "account_balance": "10000",
    "max_risk_percent": "1",
    "confidence_score": 70,
}

_LOSS_ACCEPTANCE_PAYLOAD = {
    "planned_loss_amount": "100",
    "accepted": True,
}

_STRATEGY_EVALUATE_PAYLOAD = {
    "strategy_id": "liquidity_sweep_reversal",
    "symbol": "BTCUSDT",
    "timeframe": "4h",
    "close": "60000",
    "volume": "1000000",
    "liquidity_sweep_detected": True,
}

_TOOL_EXECUTE_PAYLOAD = {
    "tool_name": "risk_checker",
    "arguments": {"request": _RISK_CHECK_PAYLOAD},
}

_UNAUTH_ENDPOINTS: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/tools", None),
    ("POST", "/tools/execute", _TOOL_EXECUTE_PAYLOAD),
    ("POST", "/risk/check", _RISK_CHECK_PAYLOAD),
    ("POST", "/risk/size", _RISK_SIZE_PAYLOAD),
    ("POST", "/risk/loss-acceptance", _LOSS_ACCEPTANCE_PAYLOAD),
    ("GET", "/strategies/modules", None),
    ("POST", "/strategies/evaluate", _STRATEGY_EVALUATE_PAYLOAD),
]

_VIEWER_FORBIDDEN_ENDPOINTS: list[tuple[str, str, dict[str, object]]] = [
    ("POST", "/tools/execute", _TOOL_EXECUTE_PAYLOAD),
    ("POST", "/risk/check", _RISK_CHECK_PAYLOAD),
    ("POST", "/risk/size", _RISK_SIZE_PAYLOAD),
    ("POST", "/risk/loss-acceptance", _LOSS_ACCEPTANCE_PAYLOAD),
    ("POST", "/strategies/evaluate", _STRATEGY_EVALUATE_PAYLOAD),
]

_VIEWER_ALLOWED_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/tools"),
    ("GET", "/strategies/modules"),
]

_TRADER_SUCCESS_ENDPOINTS: list[tuple[str, str, dict[str, object] | None]] = [
    ("GET", "/tools", None),
    ("POST", "/tools/execute", _TOOL_EXECUTE_PAYLOAD),
    ("POST", "/risk/check", _RISK_CHECK_PAYLOAD),
    ("POST", "/risk/size", _RISK_SIZE_PAYLOAD),
    ("POST", "/risk/loss-acceptance", _LOSS_ACCEPTANCE_PAYLOAD),
    ("GET", "/strategies/modules", None),
    ("POST", "/strategies/evaluate", _STRATEGY_EVALUATE_PAYLOAD),
]


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


@pytest.fixture
def at011_settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="at011-authz-test-secret-32-bytes-min",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        execution_mode="paper",
        enable_real_trading=False,
    )


@pytest.fixture
def at011_client(at011_settings: Settings) -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
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
    app = create_app(settings=at011_settings)
    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as client:
        yield client, factory

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _register(client: TestClient, *, email: str) -> dict[str, object]:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "secure-password-1",
            "organization_name": "AT011 Org",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_membership_role(
    factory: sessionmaker[Session],
    *,
    user_id: uuid.UUID,
    role: MembershipRole,
) -> None:
    with factory() as session:
        membership = session.query(Membership).filter(Membership.user_id == user_id).one()
        membership.role = role
        session.commit()


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _request(
    client: TestClient,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, object] | None = None,
) -> object:
    if method == "GET":
        return client.get(path, headers=headers)
    if method == "POST":
        return client.post(path, headers=headers, json=json)
    raise AssertionError(f"Unsupported method: {method}")


@pytest.mark.parametrize(("method", "path", "payload"), _UNAUTH_ENDPOINTS)
def test_unauthenticated_compute_endpoints_return_401(
    at011_client: tuple[TestClient, sessionmaker[Session]],
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    client, _factory = at011_client
    response = _request(client, method, path, json=payload)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(("method", "path", "payload"), _VIEWER_FORBIDDEN_ENDPOINTS)
def test_viewer_cannot_use_trader_compute_endpoints(
    at011_client: tuple[TestClient, sessionmaker[Session]],
    method: str,
    path: str,
    payload: dict[str, object],
) -> None:
    client, factory = at011_client
    registered = _register(client, email="viewer-at011@example.com")
    _set_membership_role(
        factory,
        user_id=uuid.UUID(str(registered["user"]["id"])),
        role=MembershipRole.VIEWER,
    )
    token = str(registered["tokens"]["access_token"])

    response = _request(client, method, path, headers=_auth_headers(token), json=payload)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize(("method", "path"), _VIEWER_ALLOWED_ENDPOINTS)
def test_viewer_can_use_reader_compute_endpoints(
    at011_client: tuple[TestClient, sessionmaker[Session]],
    method: str,
    path: str,
) -> None:
    client, factory = at011_client
    registered = _register(client, email="viewer-read-at011@example.com")
    _set_membership_role(
        factory,
        user_id=uuid.UUID(str(registered["user"]["id"])),
        role=MembershipRole.VIEWER,
    )
    token = str(registered["tokens"]["access_token"])

    response = _request(client, method, path, headers=_auth_headers(token))
    assert response.status_code == 200


@pytest.mark.parametrize(("method", "path", "payload"), _TRADER_SUCCESS_ENDPOINTS)
def test_trader_can_use_compute_endpoints(
    at011_client: tuple[TestClient, sessionmaker[Session]],
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    client, factory = at011_client
    registered = _register(client, email="trader-at011@example.com")
    _set_membership_role(
        factory,
        user_id=uuid.UUID(str(registered["user"]["id"])),
        role=MembershipRole.TRADER,
    )
    token = str(registered["tokens"]["access_token"])

    response = _request(client, method, path, headers=_auth_headers(token), json=payload)
    assert response.status_code == 200


@pytest.mark.parametrize(("method", "path", "payload"), _TRADER_SUCCESS_ENDPOINTS)
def test_owner_can_use_compute_endpoints(
    at011_client: tuple[TestClient, sessionmaker[Session]],
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    client, _factory = at011_client
    registered = _register(client, email="owner-at011@example.com")
    token = str(registered["tokens"]["access_token"])

    response = _request(client, method, path, headers=_auth_headers(token), json=payload)
    assert response.status_code == 200


def test_openapi_docs_available_in_local_environment() -> None:
    settings = Settings(
        environment=Environment.LOCAL,
        log_json=False,
        jwt_secret="at011-local-docs-secret-32-bytes-min",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
    )
    app = create_app(settings=settings)
    with TestClient(app) as client:
        docs = client.get("/docs")
        assert docs.status_code == 200
        redoc = client.get("/redoc")
        assert redoc.status_code == 200
        openapi = client.get("/openapi.json")
        assert openapi.status_code == 200


def test_openapi_docs_disabled_outside_local_environment() -> None:
    settings = Settings(**_STAGING_BASE)
    # AT-018: staging startup hard-requires reachable Redis for rate limiting.
    # Pre-seed the process-wide limiter with a non-Redis instance so lifespan
    # warmup does not attempt a real connection in this offline test.
    get_rate_limiter(settings.model_copy(update={"rate_limit_use_redis": False}))
    app = create_app(settings=settings)
    with TestClient(app) as client:
        docs = client.get("/docs")
        assert docs.status_code == 404
        redoc = client.get("/redoc")
        assert redoc.status_code == 404
        openapi = client.get("/openapi.json")
        assert openapi.status_code == 404


def test_tools_execute_binds_tenant_and_ignores_client_org_ids(
    at011_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """JWT tenant must overwrite any client-supplied organization_id/user_id."""
    from app.core.dependencies import get_tool_registry
    from app.schemas.tools import ToolOutput, ToolSpec

    client, _factory = at011_client
    registered = _register(client, email="tenant-bind-at011@example.com")
    token = str(registered["tokens"]["access_token"])
    org_id = str(registered["organization"]["id"])
    user_id = str(registered["user"]["id"])
    foreign_org = str(uuid.uuid4())
    foreign_user = str(uuid.uuid4())
    captured: dict[str, object] = {}

    class _CaptureRegistry:
        def list_specs(self) -> list[ToolSpec]:
            return []

        def execute(self, tool_name: str, arguments: dict[str, object]) -> ToolOutput:
            captured.clear()
            captured.update(arguments)
            return ToolOutput(tool_name=tool_name, success=True, result={"ok": True})

    app = client.app
    app.dependency_overrides[get_tool_registry] = lambda: _CaptureRegistry()
    try:
        response = client.post(
            "/tools/execute",
            headers=_auth_headers(token),
            json={
                "tool_name": "risk_checker",
                "arguments": {
                    "organization_id": foreign_org,
                    "user_id": foreign_user,
                    "request": _RISK_CHECK_PAYLOAD,
                },
            },
        )
    finally:
        app.dependency_overrides.pop(get_tool_registry, None)

    assert response.status_code == 200
    assert captured.get("organization_id") == org_id
    assert captured.get("user_id") == user_id
    assert captured.get("organization_id") != foreign_org
    assert captured.get("user_id") != foreign_user


def test_tools_execute_refuses_mutating_tools_and_strips_consent(
    at011_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    """M17: only read-only tools run; ``confirm``/``user_message`` never reach a tool."""
    from app.core.dependencies import get_tool_registry
    from app.schemas.tools import ToolOutput, ToolSpec

    client, _factory = at011_client
    registered = _register(client, email="tools-readonly-at011@example.com")
    token = str(registered["tokens"]["access_token"])
    calls: list[tuple[str, dict[str, object]]] = []

    class _CaptureRegistry:
        def list_specs(self) -> list[ToolSpec]:
            return []

        def execute(self, tool_name: str, arguments: dict[str, object]) -> ToolOutput:
            calls.append((tool_name, dict(arguments)))
            return ToolOutput(tool_name=tool_name, success=True, result={"ok": True})

    app = client.app
    app.dependency_overrides[get_tool_registry] = lambda: _CaptureRegistry()
    try:
        for tool_name, arguments in (
            ("strategy_library_tool", {"action": "create", "confirm": True}),
            ("backtest_tool", {"action": "run", "confirm": True}),
            ("risk_settings_tool", {"action": "update", "confirm": True}),
            ("paper_validation_tool", {"action": "start", "user_message": "I confirm"}),
            ("strategy_proposal_tool", {"action": "confirm"}),
            ("paper_execution", {}),
        ):
            refused = client.post(
                "/tools/execute",
                headers=_auth_headers(token),
                json={"tool_name": tool_name, "arguments": arguments},
            )
            assert refused.status_code == 200, refused.text
            assert refused.json()["success"] is False, tool_name
        assert calls == []

        allowed = client.post(
            "/tools/execute",
            headers=_auth_headers(token),
            json={
                "tool_name": "risk_checker",
                "arguments": {
                    "request": _RISK_CHECK_PAYLOAD,
                    "confirm": True,
                    "confirmed": True,
                    "user_message": "I confirm",
                },
            },
        )
    finally:
        app.dependency_overrides.pop(get_tool_registry, None)

    assert allowed.status_code == 200, allowed.text
    assert len(calls) == 1
    _name, forwarded = calls[0]
    assert "confirm" not in forwarded
    assert "confirmed" not in forwarded
    assert "user_message" not in forwarded
    assert forwarded["request"] == _RISK_CHECK_PAYLOAD
