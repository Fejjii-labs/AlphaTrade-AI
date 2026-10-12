"""PostgreSQL confirm/reject locking and content-hash seal.

Sessions are independent connections. Interleavings wait on the row lock,
not on a timer that hopes one statement finishes first.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.base import Base
from app.db.models import Membership, Organization, TradeJournal, User
from app.interactive_agent import proposals as proposal_mod
from app.interactive_agent.contracts import (
    PAYLOAD_KEY,
    AgentTurnRequest,
    ProposalDecisionRequest,
    ProposalLifecycle,
)
from app.interactive_agent.proposals import find_proposal
from app.interactive_agent.service import InteractiveAgentService
from app.schemas.common import MembershipRole
from tests.support.postgres_persistence import reset_public_schema

POSTGRES_URL = os.environ.get(
    "PHASE1_POSTGRES_URL",
    "postgresql+psycopg://alphatrade:alphatrade@127.0.0.1:5432/pr150_journal_fix",
)
ORG_A = uuid.UUID("00000000-0000-0000-0000-00000000c0a1")
USER_A = uuid.UUID("00000000-0000-0000-0000-00000000c0a2")
ORG_B = uuid.UUID("00000000-0000-0000-0000-00000000c0b1")
USER_B = uuid.UUID("00000000-0000-0000-0000-00000000c0b2")
JOURNAL_TEXT = "Journal this trade: BTCUSDT long 1h. I chased the breakout."


def _postgres_available() -> bool:
    try:
        engine = create_engine(POSTGRES_URL, poolclass=NullPool)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _postgres_available(), reason="Disposable PostgreSQL is down")


def _settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url=POSTGRES_URL,
        jwt_secret="interactive-agent-foundation-test-secret",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
        market_data_cache_use_redis=False,
    )


@pytest.fixture
def pg_db() -> Iterator[tuple[sessionmaker[Session], Settings]]:
    engine = create_engine(POSTGRES_URL, poolclass=NullPool)
    reset_public_schema(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = _settings()
    with factory() as session:
        session.add(Organization(id=ORG_A, name="Org A"))
        session.add(Organization(id=ORG_B, name="Org B"))
        session.add(User(id=USER_A, email="a@test.example", hashed_password="x"))
        session.add(User(id=USER_B, email="b@test.example", hashed_password="x"))
        session.flush()
        session.add(Membership(user_id=USER_A, organization_id=ORG_A, role=MembershipRole.OWNER))
        session.add(Membership(user_id=USER_B, organization_id=ORG_B, role=MembershipRole.OWNER))
        session.commit()
    yield factory, settings
    engine.dispose()


def _propose(
    factory: sessionmaker[Session],
    settings: Settings,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    with factory() as session:
        result = InteractiveAgentService(session, settings=settings).handle_turn(
            AgentTurnRequest(message=JOURNAL_TEXT),
            organization_id=ORG_A,
            user_id=USER_A,
        )
        session.commit()
    proposal = result.proposals[0]
    return result.conversation_id, proposal.proposal_id, proposal.content_hash


def _decision(digest: str, conversation_id: uuid.UUID, statement: str) -> ProposalDecisionRequest:
    return ProposalDecisionRequest(
        conversation_id=conversation_id,
        expected_content_hash=digest,
        statement=statement,
    )


def _journal_count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return int(session.scalar(select(func.count()).select_from(TradeJournal)) or 0)


def _stored_status(
    factory: sessionmaker[Session],
    conversation_id: uuid.UUID,
    proposal_id: uuid.UUID,
) -> ProposalLifecycle:
    with factory() as session:
        _message, proposal = find_proposal(
            session,
            conversation_id=conversation_id,
            organization_id=ORG_A,
            user_id=USER_A,
            proposal_id=proposal_id,
        )
        return proposal.status


def _lock_waiter_present(factory: sessionmaker[Session]) -> bool:
    engine = factory.kw["bind"]
    with engine.connect() as conn:
        waiting = conn.execute(
            text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
        ).scalar()
    return bool(waiting)


def _run_interleaving(
    factory: sessionmaker[Session],
    *,
    winner: str,
    first: Callable[[], object],
    second: Callable[[], object],
) -> tuple[dict[str, object], list[tuple[str, BaseException]]]:
    """Hold the winner's row lock until the loser is queued, then release it."""
    original = proposal_mod._lock_proposal
    entered = threading.Event()
    release = threading.Event()

    def locked(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        if threading.current_thread().name == winner:
            entered.set()
            assert release.wait(10), "winner was not released"
        return result

    proposal_mod._lock_proposal = locked  # type: ignore[assignment]
    outcomes: dict[str, object] = {}
    errors: list[tuple[str, BaseException]] = []

    def run(name: str, call: Callable[[], object]) -> None:
        try:
            outcomes[name] = call()
        except BaseException as exc:
            errors.append((name, exc))

    loser = "confirm" if winner == "reject" else "reject"
    try:
        primary = threading.Thread(target=run, args=(winner, first), name=winner)
        primary.start()
        assert entered.wait(10), "winner did not lock the proposal"
        secondary = threading.Thread(target=run, args=(loser, second), name=loser)
        secondary.start()
        for _cycle in range(200):
            if _lock_waiter_present(factory):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("loser did not wait on the proposal row lock")
        release.set()
        primary.join(10)
        secondary.join(10)
        assert not primary.is_alive()
        assert not secondary.is_alive()
    finally:
        release.set()
        proposal_mod._lock_proposal = original
    return outcomes, errors


def test_rejection_that_locks_first_blocks_confirmation_and_writes_nothing(
    pg_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = pg_db
    conversation_id, proposal_id, digest = _propose(factory, settings)

    def reject() -> str:
        with factory() as session:
            updated = InteractiveAgentService(session, settings=settings).reject(
                proposal_id,
                _decision(digest, conversation_id, "I reject"),
                organization_id=ORG_A,
                user_id=USER_A,
            )
            session.commit()
            return updated.status.value

    def confirm() -> str:
        with factory() as session:
            updated = InteractiveAgentService(session, settings=settings).confirm(
                proposal_id,
                _decision(digest, conversation_id, "I confirm"),
                organization_id=ORG_A,
                user_id=USER_A,
            )
            session.commit()
            return updated.status.value

    outcomes, errors = _run_interleaving(factory, winner="reject", first=reject, second=confirm)
    assert outcomes["reject"] == "rejected"
    assert _journal_count(factory) == 0
    assert _stored_status(factory, conversation_id, proposal_id) is ProposalLifecycle.REJECTED
    assert len(errors) == 1
    assert errors[0][0] == "confirm"
    assert isinstance(errors[0][1], ConflictError)
    with factory() as session, pytest.raises(ConflictError):
        InteractiveAgentService(session, settings=settings).confirm(
            proposal_id,
            _decision(digest, conversation_id, "I confirm"),
            organization_id=ORG_A,
            user_id=USER_A,
        )


def test_confirmation_that_locks_first_keeps_one_journal_against_rejection(
    pg_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = pg_db
    conversation_id, proposal_id, digest = _propose(factory, settings)

    def confirm() -> uuid.UUID:
        with factory() as session:
            updated = InteractiveAgentService(session, settings=settings).confirm(
                proposal_id,
                _decision(digest, conversation_id, "I confirm"),
                organization_id=ORG_A,
                user_id=USER_A,
            )
            session.commit()
            assert updated.resulting_record_id is not None
            return updated.resulting_record_id

    def reject() -> str:
        with factory() as session:
            updated = InteractiveAgentService(session, settings=settings).reject(
                proposal_id,
                _decision(digest, conversation_id, "I reject"),
                organization_id=ORG_A,
                user_id=USER_A,
            )
            session.commit()
            return updated.status.value

    outcomes, errors = _run_interleaving(factory, winner="confirm", first=confirm, second=reject)
    journal_id = outcomes["confirm"]
    assert isinstance(journal_id, uuid.UUID)
    assert _journal_count(factory) == 1
    assert _stored_status(factory, conversation_id, proposal_id) is ProposalLifecycle.APPLIED
    assert len(errors) == 1
    assert errors[0][0] == "reject"
    assert isinstance(errors[0][1], ConflictError)
    with factory() as session:
        with pytest.raises(ConflictError):
            InteractiveAgentService(session, settings=settings).reject(
                proposal_id,
                _decision(digest, conversation_id, "I reject"),
                organization_id=ORG_A,
                user_id=USER_A,
            )
        again = InteractiveAgentService(session, settings=settings).confirm(
            proposal_id,
            _decision(digest, conversation_id, "I confirm"),
            organization_id=ORG_A,
            user_id=USER_A,
        )
        session.commit()
        assert again.resulting_record_id == journal_id
        assert _journal_count(factory) == 1


def test_concurrent_confirms_rollback_and_reopen_keep_one_journal(
    pg_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = pg_db
    conversation_id, proposal_id, digest = _propose(factory, settings)
    with factory() as session:
        InteractiveAgentService(session, settings=settings).confirm(
            proposal_id,
            _decision(digest, conversation_id, "I confirm"),
            organization_id=ORG_A,
            user_id=USER_A,
        )
        session.rollback()
    assert _journal_count(factory) == 0

    barrier = threading.Barrier(2)
    found: list[uuid.UUID] = []
    errors: list[BaseException] = []

    def confirm() -> None:
        barrier.wait(timeout=10)
        try:
            with factory() as session:
                updated = InteractiveAgentService(session, settings=settings).confirm(
                    proposal_id,
                    _decision(digest, conversation_id, "I confirm"),
                    organization_id=ORG_A,
                    user_id=USER_A,
                )
                session.commit()
                assert updated.resulting_record_id is not None
                found.append(updated.resulting_record_id)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=confirm) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert errors == []
    assert len(found) == 2
    assert found[0] == found[1]
    assert _journal_count(factory) == 1

    engine = factory.kw["bind"]
    engine.dispose()
    reopened = sessionmaker(
        bind=create_engine(POSTGRES_URL, poolclass=NullPool),
        expire_on_commit=False,
    )
    with reopened() as session:
        again = InteractiveAgentService(session, settings=settings).confirm(
            proposal_id,
            _decision(digest, conversation_id, "I confirm"),
            organization_id=ORG_A,
            user_id=USER_A,
        )
        session.commit()
        assert again.resulting_record_id == found[0]
        assert int(session.scalar(select(func.count()).select_from(TradeJournal)) or 0) == 1


def test_changed_symbol_with_the_old_hash_does_not_write(
    pg_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = pg_db
    conversation_id, proposal_id, digest = _propose(factory, settings)
    with factory() as session:
        message, proposal = find_proposal(
            session,
            conversation_id=conversation_id,
            organization_id=ORG_A,
            user_id=USER_A,
            proposal_id=proposal_id,
        )
        payload = dict(message.payload or {})
        block = dict(payload.get(PAYLOAD_KEY) or {})
        items = []
        for item in block.get("proposals") or []:
            copied = dict(item)
            if str(copied.get("proposal_id")) == str(proposal.proposal_id):
                body = dict(copied.get("payload") or {})
                journal = dict(body.get("journal") or {})
                journal["symbol"] = "ETHUSDT"
                body["journal"] = journal
                copied["payload"] = body
            items.append(copied)
        block["proposals"] = items
        payload[PAYLOAD_KEY] = block
        message.payload = payload
        session.commit()
        with pytest.raises(ConflictError):
            InteractiveAgentService(session, settings=settings).confirm(
                proposal_id,
                _decision(digest, conversation_id, "I confirm"),
                organization_id=ORG_A,
                user_id=USER_A,
            )
        session.commit()
        _message, stored = find_proposal(
            session,
            conversation_id=conversation_id,
            organization_id=ORG_A,
            user_id=USER_A,
            proposal_id=proposal_id,
        )
        assert stored.content_hash == digest
        assert stored.payload["journal"]["symbol"] == "ETHUSDT"
        assert stored.status is ProposalLifecycle.PROPOSED
    assert _journal_count(factory) == 0


def test_stale_hash_unauthorized_caller_and_other_tenant_write_nothing(
    pg_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = pg_db
    conversation_id, proposal_id, digest = _propose(factory, settings)
    with factory() as session:
        service = InteractiveAgentService(session, settings=settings)
        with pytest.raises(ConflictError):
            service.confirm(
                proposal_id,
                _decision("a" * 64, conversation_id, "I confirm"),
                organization_id=ORG_A,
                user_id=USER_A,
            )
        with pytest.raises(ValidationAppError):
            service.confirm(
                proposal_id,
                _decision(digest, conversation_id, "> I confirm"),
                organization_id=ORG_A,
                user_id=USER_A,
            )
        with pytest.raises(NotFoundError):
            service.confirm(
                proposal_id,
                _decision(digest, conversation_id, "I confirm"),
                organization_id=ORG_B,
                user_id=USER_B,
            )
        confirmed = service.confirm(
            proposal_id,
            _decision(digest, conversation_id, "I confirm"),
            organization_id=ORG_A,
            user_id=USER_A,
        )
        session.commit()
        assert confirmed.status is ProposalLifecycle.APPLIED
        row = session.get(TradeJournal, confirmed.resulting_record_id)
        assert row is not None
        assert row.symbol == "BTCUSDT"
    assert _journal_count(factory) == 1


def test_rejected_status_with_an_existing_journal_does_not_replay(
    pg_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = pg_db
    conversation_id, proposal_id, digest = _propose(factory, settings)
    with factory() as session:
        service = InteractiveAgentService(session, settings=settings)
        applied = service.confirm(
            proposal_id,
            _decision(digest, conversation_id, "I confirm"),
            organization_id=ORG_A,
            user_id=USER_A,
        )
        session.commit()
        message, proposal = find_proposal(
            session,
            conversation_id=conversation_id,
            organization_id=ORG_A,
            user_id=USER_A,
            proposal_id=proposal_id,
        )
        payload = dict(message.payload or {})
        block = dict(payload.get(PAYLOAD_KEY) or {})
        items = []
        for item in block.get("proposals") or []:
            copied = dict(item)
            if str(copied.get("proposal_id")) == str(proposal.proposal_id):
                copied["status"] = ProposalLifecycle.REJECTED.value
                copied["applied"] = False
            items.append(copied)
        block["proposals"] = items
        payload[PAYLOAD_KEY] = block
        message.payload = payload
        session.commit()
        with pytest.raises(ConflictError):
            service.confirm(
                proposal_id,
                _decision(digest, conversation_id, "I confirm"),
                organization_id=ORG_A,
                user_id=USER_A,
            )
        session.commit()
        assert int(session.scalar(select(func.count()).select_from(TradeJournal)) or 0) == 1
        kept = session.get(TradeJournal, applied.resulting_record_id)
        assert kept is not None
        _message, stored = find_proposal(
            session,
            conversation_id=conversation_id,
            organization_id=ORG_A,
            user_id=USER_A,
            proposal_id=proposal_id,
        )
        assert stored.status is ProposalLifecycle.REJECTED
