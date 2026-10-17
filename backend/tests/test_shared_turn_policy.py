"""HTTP route equivalence, quota admission and durable request-key recovery."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.agent_capture import routes as captures
from app.agent_capture.contracts import CapturePlan
from app.api.routes import chat, interactive_agent
from app.core.auth import get_current_tenant
from app.core.dependencies import (
    get_canonical_evidence_service,
    get_canonical_runtime,
    get_session,
    get_settings,
)
from app.core.errors import register_exception_handlers
from app.db.models import Membership, UsageEvent
from app.schemas.common import MembershipRole
from app.schemas.usage import OrganizationQuotaUpdate
from app.security import rate_limit
from app.security.tenant import TenantContext
from app.services.quota_service import QuotaService
from tests import test_interactive_agent_foundation as foundation
from tests.test_agent_capture import FixtureModel, source
from tests.test_interactive_agent_foundation import ORG_A, USER_A, USER_A2


@pytest.fixture
def turn_http(monkeypatch):
    with contextmanager(foundation.agent_db.__wrapped__)() as (factory, settings):
        app = FastAPI()
        app.state.settings = settings
        for router in (interactive_agent.router, chat.router, captures.router):
            app.include_router(router)
        register_exception_handlers(app)

        def session_dep():
            with factory() as session:
                yield session

        app.dependency_overrides[get_session] = session_dep
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
            organization_id=ORG_A,
            user_id=USER_A,
            email="turn-policy@example.com",
            membership_role=MembershipRole.OWNER,
        )
        app.dependency_overrides[get_canonical_runtime] = lambda: None
        app.dependency_overrides[get_canonical_evidence_service] = lambda: None
        limiter = rate_limit.InMemoryRateLimiter()
        monkeypatch.setattr(rate_limit, "_limiter", limiter)
        monkeypatch.setattr(
            "app.agent_capture.service.CaptureModel",
            lambda _: FixtureModel(CapturePlan(entries=[], clarification=None)),
        )
        with factory() as session:
            conv, message = source(session, "A contribution for capture retry.")
            retry_body = {
                "conversation_id": str(conv.id),
                "source_message_id": str(message.id),
            }
            session.commit()
        with TestClient(app) as client:
            yield client, factory, limiter, retry_body


def test_alternating_routes_and_capture_retry_share_one_rate_bucket(turn_http):
    client, _factory, limiter, retry_body = turn_http
    for _ in range(59):
        limiter.check(f"agent:turn:user:{USER_A}", limit=60, window_seconds=3600)
    first = client.post("/agent/turns", json={"message": "Hello"})
    assert first.status_code == 200, first.text
    for path, body in (
        ("/chat/message", {"message": "Hello"}),
        ("/agent/saved/retry", retry_body),
        ("/agent/turns", {"message": "Hello again"}),
    ):
        blocked = client.post(path, json=body)
        assert blocked.status_code == 429, blocked.text


@pytest.mark.parametrize("path", ["/agent/turns", "/chat/message", "/agent/saved/retry"])
def test_all_model_turn_routes_enforce_same_agent_chat_quota(turn_http, path):
    client, factory, _limiter, retry_body = turn_http
    with factory() as session:
        QuotaService(session).update_quota(ORG_A, OrganizationQuotaUpdate(limit_agent_chat=0))
        session.commit()
    body = retry_body if path.endswith("retry") else {"message": "Hello"}
    result = client.post(path, json=body)
    assert result.status_code == 429, result.text
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(UsageEvent)) == 0


def test_chat_key_replays_before_new_quota_and_does_not_duplicate_usage(turn_http):
    client, factory, _limiter, _retry_body = turn_http
    headers = {"Idempotency-Key": str(uuid4())}
    body = {"message": "Hello"}
    first = client.post("/chat/message", json=body, headers=headers)
    assert first.status_code == 200, first.text
    with factory() as session:
        before = session.scalar(select(func.count()).select_from(UsageEvent))
        QuotaService(session).update_quota(ORG_A, OrganizationQuotaUpdate(limit_agent_chat=0))
        session.commit()
    replay = client.post("/chat/message", json=body, headers=headers)
    assert replay.status_code == 200 and replay.json() == first.json()
    conflict = client.post("/agent/turns", json=body, headers=headers)
    assert conflict.status_code == 409
    from app.interactive_agent.turn_contracts import TurnConflictDetails, TurnConflictResponse

    parsed = TurnConflictResponse.model_validate(conflict.json())
    assert isinstance(parsed.error.details, TurnConflictDetails)
    assert str(parsed.error.details.conversation_id) == first.json()["conversation_id"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(UsageEvent)) == before


def test_invalid_key_and_persisted_authority_are_revalidated(turn_http):
    client, factory, _limiter, _retry_body = turn_http
    assert (
        client.post(
            "/agent/turns", json={"message": "Hello"}, headers={"Idempotency-Key": "invalid"}
        ).status_code
        == 422
    )
    # An old authenticated claim cannot override a subsequently revoked membership.
    with factory() as session:
        membership = session.scalar(
            select(Membership).where(
                Membership.organization_id == ORG_A, Membership.user_id == USER_A
            )
        )
        membership.role = MembershipRole.VIEWER
        session.commit()
    for path in ("/agent/turns", "/chat/message"):
        result = client.post(path, json={"message": "Hello"})
        assert result.status_code == 403, result.text


def test_capture_retry_cannot_read_other_users_message(turn_http):
    client, factory, _limiter, _retry_body = turn_http
    from app.schemas.common import ConversationMessageRole
    from app.services.conversation_service import ConversationService

    with factory() as session:
        conv = ConversationService(session).get_or_create(
            organization_id=ORG_A, user_id=USER_A2, conversation_id=None
        )
        row = ConversationService(session).append_message(
            conversation=conv, role=ConversationMessageRole.USER, content="Private contribution"
        )
        session.commit()
        body = {"conversation_id": str(conv.id), "source_message_id": str(row.id)}
    result = client.post("/agent/saved/retry", json=body)
    assert result.status_code == 404, result.text


def test_live_legacy_graph_cannot_run_inside_caller_transaction(turn_http, monkeypatch):
    from app.core.errors import ValidationAppError
    from app.interactive_agent.conversation import (
        MODEL_REPLY_UNAVAILABLE,
        ModelConversationalResponder,
    )
    from app.providers.llm import OpenAILLMProvider
    from app.services.agent_service import AgentInvokeContext, build_agent_service

    calls = []

    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Live model must run in a sessionless phase")

    monkeypatch.setattr(OpenAILLMProvider, "complete", forbidden)
    _client, factory, _limiter, _body = turn_http
    settings = foundation._settings().model_copy(
        update={"provider_mode": "auto", "openai_api_key": "fixture"}
    )
    with factory() as session:
        service = build_agent_service(settings=settings, session=session)
        with pytest.raises(ValidationAppError, match="durable turn runtime"):
            service.run(
                "Hello",
                AgentInvokeContext(request_id="legacy", user_id=USER_A, organization_id=ORG_A),
            )
        assert (
            ModelConversationalResponder(session, settings).compose(
                organization_id=ORG_A,
                user_id=USER_A,
                conversation_id=uuid4(),
                message="Hello",
                factual_context="No authority granted.",
            )
            == MODEL_REPLY_UNAVAILABLE
        )
        assert not calls
