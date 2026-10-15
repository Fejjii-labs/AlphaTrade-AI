"""Private capture concurrency against disposable PostgreSQL, with fixture reasoning."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.agent_capture.contracts import CapturePlan, SavedEntryUpdate
from app.agent_capture.service import CaptureService
from app.agent_capture.store import update_entry
from app.core.errors import ConflictError
from app.db.models import AgentCaptureSource, AgentSavedEntry, Organization, User
from app.services.conversation_service import ConversationService
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres
from tests.test_agent_capture import FixtureModel, capture, source, suggestion
from tests.test_interactive_agent_foundation import ORG_A, USER_A, _settings

pytestmark = requires_postgres


@pytest.fixture
def postgres_capture_db():
    from tests.support.postgres_template import clone_database, drop_clone

    engine = clone_database(POSTGRES_URL)
    try:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            session.add(Organization(id=ORG_A, name="Private capture fixture"))
            session.add(User(id=USER_A, email="capture@example.com", hashed_password="fixture"))
            session.commit()
        yield factory, _settings()
    finally:
        drop_clone(engine)


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
    assert len(model.calls) == 1
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
