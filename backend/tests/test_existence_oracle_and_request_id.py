"""I10: cross-org reads look like missing ids; correlation ids are validated."""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.errors import NotFoundError, register_exception_handlers
from app.core.middleware import RequestContextMiddleware, _correlation_id
from app.schemas.common import MembershipRole
from app.security.tenant import TenantContext, ensure_same_organization

_TENANT = TenantContext(
    user_id=uuid.uuid4(),
    organization_id=uuid.uuid4(),
    email="member@example.com",
    membership_role=MembershipRole.TRADER,
)


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/missing")
    def _missing() -> None:
        raise NotFoundError("Order not found")

    @app.get("/other-org")
    def _other_org() -> None:
        ensure_same_organization(uuid.uuid4(), _TENANT, not_found_message="Order not found")

    @app.get("/echo")
    def _echo(request: Request) -> dict[str, str]:
        return {"request_id": request.state.request_id, "trace_id": request.state.trace_id}

    return app


def test_same_org_passes() -> None:
    ensure_same_organization(_TENANT.organization_id, _TENANT)


def test_other_org_raises_not_found() -> None:
    with pytest.raises(NotFoundError, match=r"Resource not found\."):
        ensure_same_organization(uuid.uuid4(), _TENANT)


def test_other_org_response_matches_missing_id() -> None:
    client = TestClient(_app())
    missing = client.get("/missing")
    other = client.get("/other-org")
    assert missing.status_code == other.status_code == 404
    strip = ("request_id", "trace_id")
    missing_error = {k: v for k, v in missing.json()["error"].items() if k not in strip}
    other_error = {k: v for k, v in other.json()["error"].items() if k not in strip}
    assert missing_error == other_error


def test_valid_correlation_ids_are_kept_and_echoed() -> None:
    client = TestClient(_app())
    response = client.get(
        "/echo", headers={"X-Request-ID": "req-123.abc:9_Z", "X-Trace-ID": "0af7651916cd43dd"}
    )
    assert response.json() == {"request_id": "req-123.abc:9_Z", "trace_id": "0af7651916cd43dd"}
    assert response.headers["X-Request-ID"] == "req-123.abc:9_Z"


@pytest.mark.parametrize(
    "value",
    ["a" * 65, "has space", "<script>x</script>", "id;drop", "line\tbreak"],
)
def test_malformed_correlation_ids_are_replaced(value: str) -> None:
    client = TestClient(_app())
    response = client.get("/echo", headers={"X-Request-ID": value, "X-Trace-ID": value})
    body = response.json()
    for key in ("request_id", "trace_id"):
        assert body[key] != value
        uuid.UUID(body[key])
    assert response.headers["X-Request-ID"] == body["request_id"]


def test_correlation_id_rules() -> None:
    assert _correlation_id("a" * 64) == "a" * 64
    assert _correlation_id(None) != _correlation_id(None)
    uuid.UUID(_correlation_id(""))
    uuid.UUID(_correlation_id("x\r\ny"))
    uuid.UUID(_correlation_id("\u00fc-unicode"))
