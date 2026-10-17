"""Cross-worker reservation, retry, rollback and stalled-provider evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.errors import ConflictError
from app.db.base import Base
from app.db.models import (
    Conversation,
    ConversationMessage,
    Membership,
    ModelCallAttempt,
    Organization,
    UsageEvent,
    User,
)
from app.schemas.common import MembershipRole
from app.services.conversation_service import ConversationService
from app.services.turn_coordinator import TurnCoordinator, transcript_revision
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres
from tests.test_interactive_agent_foundation import ORG_A, USER_A

pytestmark = requires_postgres


@pytest.fixture
def turns_db():
    admin = create_engine(POSTGRES_URL, poolclass=NullPool)
    schema = "agent_turn_" + uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(
        admin.url.update_query_dict({"options": "-csearch_path=" + schema}), poolclass=NullPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(Organization(id=ORG_A, name="Turn fixtures"))
        session.add(User(id=USER_A, email="turn@example.com", hashed_password="fixture"))
        session.flush()
        session.add(Membership(organization_id=ORG_A, user_id=USER_A, role=MembershipRole.OWNER))
        session.commit()
    try:
        yield factory
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def reserve(coordinator, **updates):
    args = {
        "channel": "interactive",
        "key": str(uuid4()),
        "body": {"message": "hello"},
        "organization_id": ORG_A,
        "user_id": USER_A,
        "conversation_id": None,
    }
    args.update(updates)
    return coordinator.reserve(**args)


def test_independent_workers_replay_one_durable_response(turns_db):
    key = str(uuid4())
    first = TurnCoordinator(turns_db)
    reservation, _ = reserve(first, key=key)
    first.finish(reservation, {"reply": "Durably saved"})
    restarted = TurnCoordinator(turns_db)
    second, response = reserve(restarted, key=key)
    assert second == reservation and response == {"reply": "Durably saved"}
    with turns_db() as session:
        assert session.scalar(select(func.count()).select_from(UsageEvent)) == 1
    with pytest.raises(ConflictError) as err:
        reserve(restarted, key=key, body={"message": "changed"})
    assert err.value.details["reason"] == "turn_key_conflict"


def test_stalled_provider_releases_conversation_and_other_conversations(turns_db):
    entered, release = Event(), Event()
    coordinator = TurnCoordinator(turns_db)
    reservation, _ = reserve(coordinator)

    def work():
        entered.set()
        assert release.wait(5)
        coordinator.finish(reservation, {"reply": "completed"})

    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(work)
        assert entered.wait(2)
        try:
            with turns_db() as session:
                session.execute(text("SET LOCAL lock_timeout = '500ms'"))
                assert session.scalar(
                    select(Conversation.id)
                    .where(Conversation.id == reservation.conversation_id)
                    .with_for_update(nowait=True)
                )
                session.rollback()
            with pytest.raises(ConflictError) as err:
                reserve(TurnCoordinator(turns_db), conversation_id=reservation.conversation_id)
            assert err.value.details["reason"] == "conversation_turn_in_progress"
            other, _ = reserve(TurnCoordinator(turns_db))
            assert other.conversation_id != reservation.conversation_id
        finally:
            release.set()
        job.result(3)


def test_expired_turn_cannot_finalize_after_new_worker_reserves(turns_db):
    coordinator = TurnCoordinator(turns_db)
    first, _ = reserve(coordinator)
    with turns_db() as session:
        session.get(ConversationMessage, first.turn_id).created_at = datetime.now(UTC) - timedelta(
            minutes=10
        )
        session.commit()
    new, _ = reserve(TurnCoordinator(turns_db), conversation_id=first.conversation_id)
    with pytest.raises(ConflictError) as err:
        coordinator.finish(first, {"reply": "stale"})
    assert err.value.details["reason"] == "turn_stale"
    TurnCoordinator(turns_db).finish(new, {"reply": "current"})


def test_expired_same_key_reports_interruption_without_reexecuting(turns_db):
    key = str(uuid4())
    reservation, _ = reserve(TurnCoordinator(turns_db), key=key)
    with turns_db() as session:
        session.get(ConversationMessage, reservation.turn_id).created_at = datetime.now(
            UTC
        ) - timedelta(minutes=10)
        session.commit()
    with pytest.raises(ConflictError) as err:
        reserve(TurnCoordinator(turns_db), key=key)
    assert err.value.details["reason"] == "turn_interrupted"
    with turns_db() as session:
        assert (
            session.get(ConversationMessage, reservation.turn_id).payload[
                "agent_turn_reservation_v1"
            ]["state"]
            == "interrupted"
        )


def test_transcript_snapshot_change_refuses_finalization(turns_db):
    coordinator = TurnCoordinator(turns_db)
    reservation, _ = reserve(coordinator)
    with turns_db() as session:
        revision = transcript_revision(session, reservation.conversation_id)
    with turns_db() as session:
        conv = session.get(Conversation, reservation.conversation_id)
        from app.schemas.common import ConversationMessageRole

        ConversationService(session).append_message(
            conversation=conv, role=ConversationMessageRole.USER, content="Independent new content"
        )
        session.commit()
    with turns_db() as session, pytest.raises(ConflictError) as err:
        coordinator.guard(session, reservation, revision=revision)
    assert err.value.details["reason"] == "turn_stale_snapshot"


def test_actual_usage_survives_domain_rollback_and_replay_does_not_call_again(turns_db):
    from app.providers.llm import LLMCompletionResult, LLMMessage
    from app.services.model_call_telemetry import ModelCallTelemetryService
    from app.services.model_router import ModelRouter
    from tests.test_phase2_model_router import _request

    calls = []

    class Provider:
        name = "fixture"

        def complete(self, request):
            calls.append(request)
            return LLMCompletionResult(
                content="reply",
                model="unknown-model",
                provider=self.name,
                input_tokens=17,
                output_tokens=11,
            )

    def work(reservation):
        router = ModelRouter(
            Provider(),
            tier_a_model="unknown-model",
            tier_b_model="unknown-model",
            telemetry=ModelCallTelemetryService(None),
        )
        from app.schemas.model_routing import ModelRoutingPurpose

        request = _request(
            ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS,
            org=ORG_A,
            caller_org=ORG_A,
            user=USER_A,
            caller_user=USER_A,
            resource_id=reservation.conversation_id,
            caller_resource_id=reservation.conversation_id,
            correlation_id=str(reservation.turn_id),
        )
        result = router.complete(request, [LLMMessage("user", "hello")])
        assert result.telemetry_persisted
        with turns_db() as session:
            row = coordinator.guard(session, reservation)
            row.content = "must rollback"
            session.flush()
            session.rollback()
        raise ConflictError("Final domain write failed")

    coordinator = TurnCoordinator(turns_db)
    key = str(uuid4())
    args = {
        "channel": "interactive",
        "key": key,
        "body": {"message": "hello"},
        "organization_id": ORG_A,
        "user_id": USER_A,
        "conversation_id": None,
        "work": work,
    }
    with pytest.raises(ConflictError):
        coordinator.run(**args)
    with pytest.raises(ConflictError):
        TurnCoordinator(turns_db).run(**args)
    assert len(calls) == 1
    with turns_db() as session:
        attempts = list(session.scalars(select(ModelCallAttempt)))
        assert len(attempts) == 1 and (attempts[0].input_tokens, attempts[0].output_tokens) == (
            17,
            11,
        )
        assert attempts[0].cost_source.value == "unavailable"
        assert session.scalar(select(func.count()).select_from(UsageEvent)) == 2
        reservation_row = session.get(ConversationMessage, attempts[0].usage_event_id)
        assert reservation_row is None


def test_interactive_reply_and_capture_provider_phases_hold_no_transaction(turns_db, monkeypatch):
    """Exercise production turn orchestration with both upstream calls stalled."""
    import json
    import time
    from types import SimpleNamespace

    from app.interactive_agent.contracts import AgentTurnRequest
    from app.interactive_agent.turn_runtime import run_interactive
    from app.providers.llm import LLMCompletionResult
    from app.services.turn_context import turn_context
    from tests.test_interactive_agent_foundation import _settings

    entered = [Event(), Event()]
    release = [Event(), Event()]
    calls = []

    class Provider:
        name = "fixture-frontier"

        def complete(self, request):
            index = len(calls)
            calls.append(request)
            entered[index].set()
            assert release[index].wait(5)
            return LLMCompletionResult(
                content=(
                    "Consider your existing rules."
                    if index == 0
                    else json.dumps({"entries": [], "clarification": None})
                ),
                model=request.model,
                provider=self.name,
                input_tokens=17,
                output_tokens=11,
            )

    providers = SimpleNamespace(llm=Provider())
    monkeypatch.setattr("app.interactive_agent.conversation.resolve_providers", lambda _: providers)
    monkeypatch.setattr("app.agent_capture.model.resolve_providers", lambda _: providers)
    monkeypatch.setattr(
        "app.interactive_agent.vector_adapter.AgentVectorAdapter.search", lambda *a, **k: []
    )
    coordinator = TurnCoordinator(turns_db)
    key = str(uuid4())
    body = AgentTurnRequest(message="My rule: wait for a confirmed reclaim.")
    payload = body.model_dump(mode="json")
    reservation, _ = reserve(coordinator, key=key, body=payload)

    def work():
        with turn_context(reservation.turn_id, turns_db):
            return run_interactive(coordinator, _settings(), reservation, body)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(work)
        for index in range(2):
            assert entered[index].wait(4)
            try:
                with turns_db() as session:
                    assert session.scalar(
                        select(Conversation.id)
                        .where(Conversation.id == reservation.conversation_id)
                        .with_for_update(nowait=True)
                    )
                    # No other connection is idle in a transaction during provider I/O.
                    assert (
                        session.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity "
                                "WHERE datname=current_database() "
                                "AND pid <> pg_backend_pid() AND state='idle in transaction'"
                            )
                        )
                        == 0
                    )
                other, _ = reserve(TurnCoordinator(turns_db))
                TurnCoordinator(turns_db).finish(other, {"reply": "unrelated completed"})
                if index == 1:
                    recovered, reply = reserve(TurnCoordinator(turns_db), key=key, body=payload)
                    assert recovered == reservation
                    assert reply["reply"] and reply["capture_status"] == "unavailable"
                time.sleep(0.15)
            finally:
                release[index].set()
        result = job.result(4)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert result["capture_status"] == "not_needed" and len(calls) == 2
    assert sum(coordinator.transaction_ms) < elapsed_ms - 250
    with turns_db() as session:
        assert session.scalar(select(func.count()).select_from(ModelCallAttempt)) == 2
    print({"total_ms": round(elapsed_ms, 2), "transaction_ms": coordinator.transaction_ms})


def test_langgraph_narrative_provider_holds_no_conversation_transaction(turns_db, monkeypatch):
    from types import SimpleNamespace

    from app.providers.llm import MockLLMProvider
    from app.schemas.chat import ChatMessageRequest
    from app.services.chat_turn_runtime import run_chat
    from app.services.turn_context import turn_context
    from tests.test_interactive_agent_foundation import _settings

    entered, release = Event(), Event()

    class Provider:
        name = "fixture-frontier"

        def complete(self, request):
            entered.set()
            assert release.wait(5)
            return MockLLMProvider().complete(request)

    monkeypatch.setattr(
        "app.agents.runtime.resolve_providers", lambda _: SimpleNamespace(llm=Provider())
    )
    coordinator = TurnCoordinator(turns_db)
    body = ChatMessageRequest(message="Analyze BTCUSDT on 1h", symbol="BTCUSDT", timeframe="1h")
    reservation, _ = reserve(coordinator, channel="chat", body=body.model_dump(mode="json"))
    settings = _settings().model_copy(update={"narrative_llm_enabled": True})

    def work():
        with turn_context(reservation.turn_id, turns_db):
            return run_chat(coordinator, settings, reservation, body, None, None)

    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(work)
        assert entered.wait(4)
        try:
            with turns_db() as session:
                assert session.scalar(
                    select(Conversation.id)
                    .where(Conversation.id == reservation.conversation_id)
                    .with_for_update(nowait=True)
                )
                assert (
                    session.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE datname=current_database() "
                            "AND pid <> pg_backend_pid() AND state='idle in transaction'"
                        )
                    )
                    == 0
                )
        finally:
            release.set()
        result = job.result(5)
    assert result["reply"] and result["conversation_id"] == str(reservation.conversation_id)
    with turns_db() as session:
        assert session.scalar(select(func.count()).select_from(ModelCallAttempt)) == 1
