"""Real row-lock coverage for concurrent V3 confirmation replay."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    ConversationMessage,
    Document,
    Membership,
    Organization,
    User,
)
from app.interactive_agent.service import InteractiveAgentService
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.common import MembershipRole
from tests.test_agent_action_orchestration import confirm, turn
from tests.test_interactive_agent_foundation import ORG_A, USER_A, _count, _settings


@pytest.fixture
def application_postgres():
    url = os.environ.get(
        "AT028_POSTGRES_URL",
        "postgresql+psycopg://alphatrade:alphatrade@localhost:5432/alphatrade_test",
    )
    from tests.support.postgres_template import clone_database, drop_clone

    try:
        engine = clone_database(url)
    except OperationalError:
        pytest.skip("PostgreSQL is unavailable for concurrent application tests.")
    try:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            session.add(Organization(id=ORG_A, name="Agent application concurrency"))
            session.add(User(id=USER_A, email="application@test.example", hashed_password="x"))
            session.flush()
            session.add(
                Membership(user_id=USER_A, organization_id=ORG_A, role=MembershipRole.OWNER)
            )
            session.commit()
        yield factory, _settings()
    finally:
        drop_clone(engine)


@pytest.mark.parametrize("action", ["knowledge", "watcher", "pretrade"])
def test_concurrent_confirmations_share_one_canonical_result(application_postgres, action):
    factory, settings = application_postgres
    with factory() as session:
        if action == "knowledge":
            _, result = turn(
                session, settings, name="knowledge.propose", arguments={"text": "Wait"}
            )
        elif action == "watcher":
            _, result = turn(
                session,
                settings,
                name="watcher.change",
                arguments={
                    "operation": "disable",
                    "position": 2,
                },
            )
        else:
            _, result = turn(
                session,
                settings,
                message=(
                    "Enter a paper trade BTCUSDT long 1h entry 100 stop 95 targets 110. "
                    "Account size 10000 max risk 1%"
                ),
            )
    barrier = threading.Barrier(2)

    def apply_once():
        with factory() as session:
            service = InteractiveAgentService(session, settings=settings)
            barrier.wait(timeout=10)
            applied = confirm(service, result)
            session.commit()
            return applied

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(apply_once) for _ in range(2)]
        first, second = [future.result(timeout=30) for future in futures]
    assert first == second and first.applied and first.resulting_record_id
    with factory() as session:
        if action == "knowledge":
            assert _count(session, Document) == 1
        elif action == "watcher":
            assert WatcherWatchlistRepository(session).load(ORG_A).revision == 1
        else:
            assert (
                len(
                    session.scalars(
                        select(ConversationMessage).where(
                            ConversationMessage.intent == "paper_pretrade_analysis"
                        )
                    ).all()
                )
                == 1
            )
