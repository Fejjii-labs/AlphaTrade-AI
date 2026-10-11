"""Private capture concurrency against disposable PostgreSQL, with fixture reasoning."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.agent_capture.contracts import CapturePlan, SavedEntryUpdate, StrategyDraft
from app.agent_capture.service import CaptureService
from app.agent_capture.store import update_entry
from app.core.errors import ConflictError
from app.db.base import Base
from app.db.models import (
    AgentCaptureSource,
    AgentSavedEntry,
    Chunk,
    ConversationMessage,
    Document,
    Membership,
    Order,
    Organization,
    User,
)
from app.schemas.common import ConversationMessageRole, DocumentSourceType
from app.services.conversation_service import ConversationService
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres
from tests.test_agent_capture import FixtureModel, capture, source, suggestion
from tests.test_interactive_agent_foundation import ORG_A, ORG_B, USER_A, USER_A2, USER_B, _settings

pytestmark = requires_postgres


@pytest.fixture
def postgres_capture_db():
    admin = create_engine(POSTGRES_URL, poolclass=NullPool)
    schema = "agent_capture_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(
        admin.url.update_query_dict({"options": f"-csearch_path={schema}"}),
        poolclass=NullPool,
    )
    try:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            session.add(Organization(id=ORG_A, name="Private capture fixture"))
            session.add(User(id=USER_A, email="capture@example.com", hashed_password="fixture"))
            session.commit()
        yield factory, _settings()
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def test_concurrent_duplicate_capture_keeps_one_save_and_allows_unrelated_user_references(
    postgres_capture_db,
):
    factory, settings = postgres_capture_db
    contribution = "I waited for confirmation before entering."
    with factory() as session:
        first_conversation, first_message = source(session, contribution)
        second_conversation, second_message = source(session, contribution)
        session.commit()
    entered, release, second_started = Event(), Event(), Event()

    class WaitingModel(FixtureModel):
        def plan(self, **kwargs):
            entered.set()
            assert release.wait(10), "Capture fixture was not released"
            return super().plan(**kwargs)

    model = WaitingModel(CapturePlan(entries=[suggestion(contribution)], clarification=None))

    def save(conversation, message, *, notify=False):
        with factory() as session:
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            if notify:
                second_started.set()
            result = CaptureService(session, settings, model=model).capture(
                organization_id=ORG_A,
                user_id=USER_A,
                conversation_id=conversation.id,
                message_id=message.id,
            )
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(save, first_conversation, first_message)
        try:
            assert entered.wait(5), "First capture never reached reasoning"
            second = pool.submit(save, second_conversation, second_message, notify=True)
            assert second_started.wait(5), "Second capture did not start"
            # A concurrent application write referencing this user must complete
            # while reasoning holds the private capture serialization lock.
            with factory() as session:
                session.execute(text("SET LOCAL statement_timeout = '1s'"))
                unrelated = ConversationService(session).get_or_create(
                    organization_id=ORG_A, user_id=USER_A, conversation_id=None
                )
                session.commit()
                unrelated_id = unrelated.id
        finally:
            release.set()
        first_result, second_result = first.result(timeout=5), second.result(timeout=5)

    assert first_result[1] == second_result[1] == "saved"
    assert first_result[0][0].id == second_result[0][0].id
    # These are distinct accepted contributions, not a replay. Both real model
    # attempts must be accounted; only the canonical note is deduplicated.
    assert len(model.calls) == 2
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentSavedEntry)) == 1
        assert session.scalar(select(func.count()).select_from(AgentCaptureSource)) == 1
        assert ConversationService(session).require(
            unrelated_id, organization_id=ORG_A, user_id=USER_A
        )


def test_concurrent_corrections_refuse_a_stale_revision(postgres_capture_db):
    factory, settings = postgres_capture_db
    original = "I chased the breakout and need to wait."
    with factory() as session:
        conversation, message = source(session, original)
        entry = capture(
            session,
            settings,
            FixtureModel(CapturePlan(entries=[suggestion(original)], clarification=None)),
            conversation,
            message,
        )[0][0]
        session.commit()
    ready = Barrier(2)

    def correct(summary):
        with factory() as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            ready.wait(timeout=5)
            try:
                update_entry(
                    session,
                    entry.id,
                    SavedEntryUpdate(expected_revision=1, summary=summary),
                    ORG_A,
                    USER_A,
                )
                session.commit()
                return "saved"
            except ConflictError:
                session.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(correct, ["Wait for confirmation.", "Avoid chasing."]))
    assert sorted(results) == ["conflict", "saved"]
    with factory() as session:
        stored = session.get(AgentSavedEntry, entry.id)
        assert stored.revision == 2
        assert stored.summary in {"Wait for confirmation.", "Avoid chasing."}
        assert stored.original_text == original
        assert len(stored.history) == 1
        assert stored.history[0]["summary"] == original


def test_http_mixed_categories_survive_restart_replay_and_private_scope(
    postgres_capture_db, monkeypatch
):
    """Real turn/capture HTTP routes with fixture reasoning and durable PostgreSQL."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.agent_capture import routes as captures
    from app.api.routes import conversations, interactive_agent
    from app.core.auth import get_current_tenant
    from app.core.dependencies import get_session, get_settings
    from app.core.errors import register_exception_handlers
    from app.schemas.common import MembershipRole
    from app.security.tenant import TenantContext

    factory, settings = postgres_capture_db
    journal = "I rushed my paper review yesterday and felt distracted."
    news = "I think rates may weigh on BTC next week; this is my opinion."
    rule = "My rule is to wait for a closed confirmation."
    strategy = "My proposed setup is a pullback after a confirmed trend."
    lesson = "My takeaway is that patience matters more than a forecast."
    document_text = f"{journal}\n{news}\n{rule}"
    model = FixtureModel(
        CapturePlan(
            entries=[
                suggestion(journal, category="journal"),
                suggestion(news, category="news_analysis"),
                suggestion(rule, category="rules"),
            ],
            clarification=None,
        )
    )
    monkeypatch.setattr("app.agent_capture.service.CaptureModel", lambda _: model)
    with factory() as session:
        session.add(Organization(id=ORG_B, name="Other tenant"))
        session.add_all(
            [
                User(id=USER_A2, email="other-owner@example.com", hashed_password="fixture"),
                User(id=USER_B, email="other-tenant@example.com", hashed_password="fixture"),
            ]
        )
        doc = Document(
            organization_id=ORG_A,
            user_id=USER_A,
            source_type=DocumentSourceType.GENERAL_NOTE,
            title="Synthetic mixed reflection",
        )
        session.add(doc)
        session.flush()
        session.add_all(
            [
                Membership(user_id=USER_A, organization_id=ORG_A, role=MembershipRole.OWNER),
                Membership(user_id=USER_A2, organization_id=ORG_A, role=MembershipRole.OWNER),
                Membership(user_id=USER_B, organization_id=ORG_B, role=MembershipRole.OWNER),
            ]
        )
        session.add(
            Chunk(
                document_id=doc.id,
                organization_id=ORG_A,
                user_id=USER_A,
                ordinal=0,
                content=document_text,
            )
        )
        session.commit()
        document_id = doc.id

    app = FastAPI()
    for router in (interactive_agent.router, captures.router, conversations.router):
        app.include_router(router)
    register_exception_handlers(app)

    def session_dep():
        with factory() as session:
            yield session

    actor = [ORG_A, USER_A]
    app.dependency_overrides[get_session] = session_dep
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
        organization_id=actor[0],
        user_id=actor[1],
        email="capture@example.com",
        membership_role=MembershipRole.OWNER,
    )
    body = {"message": "Please organize this mixed reflection.", "source_document_id": str(doc.id)}
    headers = {"Idempotency-Key": str(uuid4())}
    with TestClient(app) as client:
        response = client.post("/agent/turns", json=body, headers=headers)
        assert response.status_code == 200, response.text
        original = response.json()
        assert original["capture_status"] == "saved"
        assert not original["execution_attempted"] and not original["authority_mutated"]
        records = original["saved_entries"]
        assert {r["category"] for r in records} == {"journal", "news_analysis", "rules"}
        assert all(r["source_document_id"] == str(document_id) for r in records)
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(AgentSavedEntry)) == 3
            assert session.scalar(select(func.count()).select_from(AgentCaptureSource)) == 1
            committed_message_ids = set(session.scalars(select(ConversationMessage.id)))
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ConversationMessage)
                    .where(
                        ConversationMessage.role.in_(
                            [ConversationMessageRole.USER, ConversationMessageRole.ASSISTANT]
                        )
                    )
                )
                == 2
            )
            assert session.scalar(select(func.count()).select_from(Order)) == 0

    # A new HTTP lifecycle resolves the original committed identity without planning again.
    model.result = CapturePlan(entries=[], clarification="This planner must not run on replay.")
    with TestClient(app) as restarted:
        replay = restarted.post("/agent/turns", json=body, headers=headers)
        assert replay.status_code == 200, replay.text
        assert replay.json() == original
        assert len(model.calls) == 1
        messages = restarted.get(f"/conversations/{original['conversation_id']}/messages").json()[
            "items"
        ]
        assert {r["id"] for r in messages} == {
            original["user_message_id"],
            original["assistant_message_id"],
        }
        with factory() as session:
            assert set(session.scalars(select(ConversationMessage.id))) == committed_message_ids
        assert messages[-1]["payload"]["interactive_agent"]["capture"]["saved_entries"] == records
        assert restarted.get("/agent/saved", params={"view": "journal"}).json()["total"] == 1
        assert restarted.get("/agent/saved", params={"view": "knowledge"}).json()["total"] == 2

        model.result = CapturePlan(
            entries=[
                suggestion(
                    strategy,
                    category="strategies",
                    draft=StrategyDraft(
                        family="trend pullback",
                        market=None,
                        direction=None,
                        timeframe=None,
                        entry_rules=[strategy],
                        exit_rules=[],
                        invalidation=[],
                        missing_fields=["market", "direction", "timeframe", "exit", "invalidation"],
                    ),
                ),
                suggestion(lesson, category="lessons"),
            ],
            clarification=None,
        )
        second = restarted.post(
            "/agent/turns",
            json={
                "message": f"{strategy}\n{lesson}",
                "conversation_id": original["conversation_id"],
            },
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert second.status_code == 200, second.text
        assert {r["category"] for r in second.json()["saved_entries"]} == {"strategies", "lessons"}
        records += second.json()["saved_entries"]
        for record in records:
            fetched = restarted.get(f"/agent/saved/{record['id']}")
            assert fetched.status_code == 200
            assert fetched.json()["summary"] == record["summary"]
            filtered = restarted.get("/agent/saved", params={"category": record["category"]})
            assert [r["id"] for r in filtered.json()["items"]] == [record["id"]]
        assert restarted.get("/agent/saved", params={"view": "knowledge"}).json()["total"] == 4

        for foreign_actor in ((ORG_A, USER_A2), (ORG_B, USER_B)):
            actor[:] = foreign_actor
            assert restarted.get("/agent/saved").json()["total"] == 0
            assert restarted.get(f"/agent/saved/{records[0]['id']}").status_code == 404
            assert (
                restarted.patch(
                    f"/agent/saved/{records[0]['id']}", json={"expected_revision": 1, "undo": True}
                ).status_code
                == 404
            )
            assert (
                restarted.get(f"/conversations/{original['conversation_id']}/messages").status_code
                == 404
            )
            foreign_upload = restarted.post(
                "/agent/turns", json=body, headers={"Idempotency-Key": str(uuid4())}
            )
            assert foreign_upload.status_code == 404
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentSavedEntry)) == 5
        assert session.scalar(select(func.count()).select_from(AgentCaptureSource)) == 2
        assert session.scalar(select(func.count()).select_from(Document)) == 1
        assert session.scalar(select(func.count()).select_from(Order)) == 0
