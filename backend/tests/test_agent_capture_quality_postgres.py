"""Disposable PostgreSQL evidence for natural capture guards and routed usage."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.agent_capture.contracts import CapturePlan
from app.agent_capture.service import CaptureService
from app.core.errors import NotFoundError, ValidationAppError
from app.db.models import AgentCaptureSource, AgentSavedEntry, ModelCallAttempt, Order, UsageEvent
from app.providers.llm import LLMCompletionResult
from tests.support.postgres_persistence import requires_postgres
from tests.test_agent_capture import FixtureModel, capture, source, suggestion
from tests.test_agent_capture_postgres import postgres_capture_db as postgres_capture_db
from tests.test_agent_capture_quality import CASES, oracle_plan
from tests.test_interactive_agent_foundation import ORG_A, ORG_B, USER_A, USER_A2

pytestmark = requires_postgres


def test_refused_double_revision_preserves_record_and_valid_mixed_retry_is_durable(
    postgres_capture_db,
):
    factory, settings = postgres_capture_db
    with factory() as session:
        conversation, initial = source(session, "No entry until the candle closes.")
        entry = capture(
            session,
            settings,
            FixtureModel(
                CapturePlan(
                    entries=[suggestion(initial.content, category="rules")], clarification=None
                )
            ),
            conversation,
            initial,
        )[0][0]
        text = "Wait for a full close. Patience matters more than prediction."
        _, next_message = source(session, text, conversation)
        session.commit()
        duplicate_plan = CapturePlan(
            entries=[
                suggestion("Wait for a full close.", category="rules", target=entry.id),
                suggestion(
                    "Patience matters more than prediction.", category="rules", target=entry.id
                ),
            ],
            clarification=None,
        )
        with pytest.raises(ValidationAppError, match="more than once"):
            capture(session, settings, FixtureModel(duplicate_plan), conversation, next_message)
    with factory() as fresh:
        original = fresh.get(AgentSavedEntry, entry.id)
        assert original.revision == 1 and original.summary == initial.content
        assert original.history == []
        assert fresh.scalar(select(func.count()).select_from(AgentCaptureSource)) == 1
        assert fresh.scalar(select(func.count()).select_from(AgentSavedEntry)) == 1
    valid_plan = CapturePlan(
        entries=[
            suggestion("Wait for a full close.", category="rules", target=entry.id),
            suggestion("Patience matters more than prediction.", category="lessons"),
        ],
        clarification=None,
    )
    with factory() as session:
        valid = capture(session, settings, FixtureModel(valid_plan), conversation, next_message)
        assert valid[1] == "saved"
        identities = [(e.id, e.revision) for e in valid[0]]
    with factory() as fresh:
        replay = CaptureService(fresh, settings, model=FixtureModel(valid_plan)).capture(
            organization_id=ORG_A,
            user_id=USER_A,
            conversation_id=conversation.id,
            message_id=next_message.id,
        )
        assert [(e.id, e.revision) for e in replay[0]] == identities
        assert fresh.scalar(select(func.count()).select_from(AgentSavedEntry)) == 2
        assert fresh.scalar(select(func.count()).select_from(AgentCaptureSource)) == 2
        updated = fresh.get(AgentSavedEntry, entry.id)
        assert updated.revision == 2 and len(updated.history) == 1
        assert updated.history[0]["summary"] == initial.content
        from app.agent_capture.store import require_entry

        for org, user in ((ORG_A, USER_A2), (ORG_B, USER_A)):
            with pytest.raises(NotFoundError):
                require_entry(fresh, entry.id, org, user)
        assert fresh.scalar(select(func.count()).select_from(Order)) == 0


def test_real_http_router_usage_and_natural_mixed_records_replay_without_another_call(
    postgres_capture_db, monkeypatch
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.agent_capture import routes as captures
    from app.api.routes import interactive_agent
    from app.core.auth import get_current_tenant
    from app.core.dependencies import get_session, get_settings
    from app.core.errors import register_exception_handlers
    from app.db.models import Membership
    from app.schemas.common import MembershipRole
    from app.security.tenant import TenantContext

    factory, settings = postgres_capture_db
    case = next(case for case in CASES if case.name == "mixed_three")
    plan = oracle_plan(case)
    requests = []

    class OracleProvider:
        name = "deterministic-quality-oracle"

        def complete(self, request):
            requests.append(request)
            return LLMCompletionResult(
                content=plan.model_dump_json(),
                model=request.model,
                provider=self.name,
                input_tokens=41,
                output_tokens=23,
                latency_ms=2,
                fallback_used=False,
            )

    monkeypatch.setattr(
        "app.agent_capture.model.resolve_providers", lambda _: SimpleNamespace(llm=OracleProvider())
    )
    with factory() as session:
        session.add(Membership(user_id=USER_A, organization_id=ORG_A, role=MembershipRole.OWNER))
        session.commit()

    def session_dep():
        with factory() as session:
            yield session

    def make_app():
        app = FastAPI()
        app.include_router(interactive_agent.router)
        app.include_router(captures.router)
        register_exception_handlers(app)
        app.dependency_overrides[get_session] = session_dep
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
            organization_id=ORG_A,
            user_id=USER_A,
            email="quality@example.com",
            membership_role=MembershipRole.OWNER,
        )
        return app

    headers = {"Idempotency-Key": str(uuid4())}
    body = {"message": case.text}
    with TestClient(make_app()) as client:
        response = client.post("/agent/turns", json=body, headers=headers)
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["capture_status"] == "saved"
        assert {e["category"] for e in receipt["saved_entries"]} == set(case.categories)
        assert not receipt["execution_attempted"] and not receipt["authority_mutated"]
    with factory() as fresh:
        attempts = list(
            fresh.scalars(
                select(ModelCallAttempt).where(
                    ModelCallAttempt.provider == "deterministic-quality-oracle"
                )
            )
        )
        assert len(attempts) == 1
        attempt = attempts[0]
        assert attempt.input_tokens == 41 and attempt.output_tokens == 23
        assert attempt.organization_id == ORG_A and attempt.user_id == USER_A
        assert attempt.success and not attempt.fallback_used
        linked = list(
            fresh.scalars(select(UsageEvent).where(UsageEvent.id == attempt.usage_event_id))
        )
        assert len(linked) == 1
        usage_ids = set(fresh.scalars(select(UsageEvent.id)))
        attempt_ids = set(fresh.scalars(select(ModelCallAttempt.id)))
    with TestClient(make_app()) as restarted:
        replay = restarted.post("/agent/turns", json=body, headers=headers)
        assert replay.status_code == 200 and replay.json() == receipt
        assert len(requests) == 1
    with factory() as fresh:
        assert set(fresh.scalars(select(UsageEvent.id))) == usage_ids
        assert set(fresh.scalars(select(ModelCallAttempt.id))) == attempt_ids
        assert fresh.scalar(select(func.count()).select_from(AgentSavedEntry)) == 3
        assert fresh.scalar(select(func.count()).select_from(AgentCaptureSource)) == 1
        assert fresh.scalar(select(func.count()).select_from(Order)) == 0
