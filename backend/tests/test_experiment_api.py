"""HTTP contract, authorization and scoped OpenAPI. No application runtime startup."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.experiments import get_experiment_service, router
from app.core.auth import get_current_tenant
from app.core.errors import AuthError, register_exception_handlers
from app.db.session import get_session
from app.schemas.common import MembershipRole
from app.schemas.experiments import ExperimentCreate
from tests.support.experiment_fixtures import (
    experiment_engine as _experiment_engine,  # noqa: F401
)
from tests.support.experiment_fixtures import (
    experiment_world as _experiment_world,  # noqa: F401
)


def contract_app():
    app = FastAPI(title="AlphaTrade Experiment API", version="1")
    app.include_router(router)
    register_exception_handlers(app)
    return app


def test_scoped_openapi_matches_published_interface():
    expected = Path(__file__).parents[2] / "docs/contracts/experiments_v1.openapi.json"
    schema = contract_app().openapi()
    assert json.loads(expected.read_text()) == schema
    assert set(schema["paths"]) == {
        "/experiments",
        "/experiments/{experiment_id}",
        "/experiments/{experiment_id}/versions",
        "/experiments/{experiment_id}/versions/{version_id}/outcomes/{source_record_id}",
        "/experiments/{experiment_id}/versions/{version_id}/performance",
        *{
            f"/experiments/{{experiment_id}}/versions/{{version_id}}/{action}"
            for action in ("transition", "approve", "promote", "samples")
        },
    }
    request = schema["components"]["schemas"]["ExperimentSampleCreate"]
    assert set(request["properties"]) == {"variant_key", "source_record_id"}
    assert request["additionalProperties"] is False


def test_api_requires_auth_roles_exact_revision_and_tenant_scope(experiment_world):
    w = experiment_world
    app = contract_app()
    actor = [w.tenant]

    def tenant():
        if actor[0] is None:
            raise AuthError("Missing bearer token.")
        return actor[0]

    app.dependency_overrides[get_current_tenant] = tenant
    app.dependency_overrides[get_session] = lambda: w.session
    app.dependency_overrides[get_experiment_service] = lambda: w.service
    body = ExperimentCreate(name="HTTP", idempotency_key="http", configuration=w.configuration())
    with TestClient(app) as client:
        actor[0] = None
        assert client.get("/experiments").status_code == 401
        actor[0] = replace(w.tenant, membership_role=MembershipRole.VIEWER)
        assert client.post("/experiments", json=body.model_dump(mode="json")).status_code == 403
        actor[0] = w.tenant
        created = client.post("/experiments", json=body.model_dump(mode="json"))
        assert created.status_code == 201
        data = created.json()
        assert data["runtime_activated"] is False and data["performance"] is None
        assert "credential_binding" not in created.text
        prefix = f"/experiments/{data['experiment_id']}/versions/{data['id']}"
        submitted = client.post(
            prefix + "/transition", json={"action": "submit", "expected_revision": 0}
        )
        assert submitted.status_code == 200 and submitted.json()["state"] == "pending_approval"
        assert (
            client.post(
                prefix + "/transition", json={"action": "submit", "expected_revision": 0}
            ).status_code
            == 409
        )
        actor[0] = replace(w.tenant, membership_role=MembershipRole.TRADER)
        approval = {
            "expected_revision": 1,
            "configuration_hash": data["configuration_hash"],
            "authorized_until": "2030-01-01T00:00:00Z",
            "confirm": "APPROVE_BOUNDED_EXPERIMENT",
        }
        assert client.post(prefix + "/approve", json=approval).status_code == 403
        actor[0] = replace(w.tenant, membership_role=MembershipRole.VIEWER)
        page = client.get("/experiments?limit=1&offset=0")
        assert page.status_code == 200 and page.json()["total"] == 1
        assert client.get("/experiments?limit=101").status_code == 422
        assert (
            client.post(
                prefix + "/transition", json={"action": "start", "expected_revision": 1}
            ).status_code
            == 403
        )
        actor[0] = replace(w.tenant, organization_id=uuid4())
        assert client.get(f"/experiments/{data['experiment_id']}").status_code == 404
        assert client.get("/experiments").json()["items"] == []
        actor[0] = w.tenant
        w.service.source_resolver = None
        sample = {"variant_key": "baseline", "source_record_id": "one"}
        assert client.post(prefix + "/samples", json={**sample, "pnl": "10"}).status_code == 422
        unavailable = client.post(prefix + "/samples", json=sample)
        assert unavailable.status_code == 503
        assert unavailable.json()["error"]["code"] == "experiment_source_adapter_unavailable"
