"""Error responses must carry CORS headers, including unhandled 500s.

Starlette runs the ``Exception`` handler in ``ServerErrorMiddleware``, the
outermost layer, so a 500 built there never passes through ``CORSMiddleware``.
The browser then reports a network error ("Failed to fetch") instead of the
JSON envelope. The app must answer unhandled errors *inside* the CORS layer.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

ORIGIN = "https://preview.example.test"


def _client(*, raise_server_exceptions: bool = False) -> TestClient:
    settings = Settings(
        environment="local",
        debug=False,
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        rate_limit_use_redis=False,
        market_data_cache_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
        cors_origins=[ORIGIN],
    )
    app = create_app(settings=settings)
    router = APIRouter()

    @router.get("/__test/unhandled")
    def unhandled() -> dict[str, str]:
        raise RuntimeError("boom")

    @router.get("/__test/unhandled-async")
    async def unhandled_async() -> dict[str, str]:
        raise RuntimeError("boom")

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def test_unhandled_error_keeps_cors_headers_and_envelope() -> None:
    with _client() as client:
        response = client.get("/__test/unhandled", headers={"Origin": ORIGIN})

    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert response.headers.get("access-control-allow-credentials") == "true"
    assert response.headers.get("X-Request-ID")
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["message"] == "An unexpected error occurred."
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    assert "boom" not in response.text


def test_unhandled_error_in_async_handler_keeps_cors_headers() -> None:
    with _client() as client:
        response = client.get("/__test/unhandled-async", headers={"Origin": ORIGIN})

    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert response.json()["error"]["code"] == "internal_error"


def test_unhandled_error_without_origin_has_no_cors_header() -> None:
    with _client() as client:
        response = client.get("/__test/unhandled")

    assert response.status_code == 500
    assert "access-control-allow-origin" not in response.headers
    assert response.json()["error"]["code"] == "internal_error"


def test_handled_error_keeps_cors_headers() -> None:
    with _client() as client:
        response = client.get("/exchange/status", headers={"Origin": ORIGIN})

    assert response.status_code == 401
    assert response.headers.get("access-control-allow-origin") == ORIGIN


def test_unhandled_error_still_reaches_test_client() -> None:
    """Re-raised after the response, like Starlette's ServerErrorMiddleware."""
    with _client(raise_server_exceptions=True) as client, pytest.raises(RuntimeError):
        client.get("/__test/unhandled", headers={"Origin": ORIGIN})
