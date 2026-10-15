"""Application routes consume actual durable screening records under RBAC."""

from dataclasses import replace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.trendpulse_screening import get_screening_service, router
from app.core.auth import get_current_tenant
from app.core.errors import AuthError, register_exception_handlers
from app.db.session import get_session
from app.schemas.common import MembershipRole
from tests.support.experiment_fixtures import experiment_engine as _experiment_engine  # noqa: F401
from tests.test_trendpulse_screening import request, service
from tests.test_trendpulse_screening import screening_world as _screening_world  # noqa: F401


def test_application_screen_producer_and_paginated_read_consumer(screening_world):
    w = screening_world
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    actor = [w.tenant]

    def tenant():
        if actor[0] is None:
            raise AuthError("Missing bearer token.")
        return actor[0]

    app.dependency_overrides[get_current_tenant] = tenant
    app.dependency_overrides[get_session] = lambda: w.session
    app.dependency_overrides[get_screening_service] = lambda: service(w)
    prefix = f"/experiments/{w.version.experiment_id}/versions/{w.version.id}/trendpulse-screenings"
    with TestClient(app) as client:
        actor[0] = None
        assert client.get(prefix).status_code == 401
        actor[0] = replace(w.tenant, membership_role=MembershipRole.VIEWER)
        assert client.post(prefix, json=request().model_dump(mode="json")).status_code == 403
        actor[0] = w.tenant
        body = request().model_dump(mode="json")
        first = client.post(prefix, json=body)
        assert first.status_code == 200
        detail = first.json()
        assert detail["status"] == "qualified_research_signal"
        assert detail["performance"] is None and not detail["execution_authorized"]
        assert client.post(prefix, json=body).json() == detail
        second = client.post(prefix, json=request().model_dump(mode="json")).json()
        assert second["status"] == "duplicate" and second["duplicate_of"] == detail["id"]
        actor[0] = replace(w.tenant, membership_role=MembershipRole.VIEWER)
        page = client.get(prefix + "?limit=1&offset=0").json()
        assert page["total"] == 2 and len(page["items"]) == 1
        assert "evidence" not in page["items"][0] and "signal" not in page["items"][0]
        filtered = client.get(prefix + "?status=qualified_research_signal").json()
        assert filtered["total"] == 1 and filtered["items"][0]["signal_id"] == detail["signal_id"]
        assert client.get(f"/trendpulse-screenings/{detail['id']}").json() == detail
        assert client.get(prefix + "?limit=51").status_code == 422
        assert client.get(prefix + "?offset=10001").status_code == 422
        actor[0] = replace(w.tenant, user_id=uuid4())
        assert client.get(prefix).status_code == 404
        assert client.get(f"/trendpulse-screenings/{detail['id']}").status_code == 404


def test_api_refuses_caller_supplied_evidence_clock_or_rules_and_default_is_disabled(
    screening_world,
):
    from app.core.config import Settings

    w = screening_world
    assert Settings.model_fields["trendpulse_screening_enabled"].default is False
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)
    app.dependency_overrides[get_current_tenant] = lambda: w.tenant
    app.dependency_overrides[get_session] = lambda: w.session
    app.dependency_overrides[get_screening_service] = lambda: service(w)
    prefix = f"/experiments/{w.version.experiment_id}/versions/{w.version.id}/trendpulse-screenings"
    with TestClient(app) as client:
        for field in ("evaluated_at", "spec", "evidence", "instrument_rules", "signal"):
            body = {**request().model_dump(mode="json"), field: "untrusted"}
            assert client.post(prefix, json=body).status_code == 422
        w.settings = w.settings.model_copy(update={"trendpulse_screening_enabled": False})
        assert client.post(prefix, json=request().model_dump(mode="json")).status_code == 503
        assert client.get(prefix).json()["total"] == 0
