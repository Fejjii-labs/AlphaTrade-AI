"""PostgreSQL ActionEligibilityStore: uniqueness, append-safe history, identity."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.canonical_eligibility import (
    ActionEligibilityEvaluationRow,
    CanonicalEligibilityHistoryImmutabilityError,
)
from app.persistence.candidate_postgres import PostgresCandidateRepository
from app.persistence.eligibility_postgres import PostgresActionEligibilityStore
from app.signal_fusion.action_eligibility import ActionEligibilityService
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.errors import (
    ActionEligibilityLineageError,
    ConflictingActionEligibilityError,
)
from app.signal_fusion.evidence_window import CanonicalEvidenceWindowV1
from app.signal_fusion.lifecycle import CandidateLifecycleService, in_memory_candidate_lifecycle
from app.signal_fusion.memory import FrozenClock
from tests.support.phase5_market import EVALUATED_AT
from tests.support.phase6_eligibility import (
    VENUE_STATE_B,
    eligibility_command,
    market_action,
    risk_snapshot,
)
from tests.support.phase6_fusion import (
    ACCOUNT_ID,
    ORG_ID,
    make_assessment,
    make_creation_command,
    make_evidence_window,
)
from tests.support.postgres_persistence import persistence_session_factory, requires_postgres


def _services(
    factory: sessionmaker[Session] | None = None,
) -> tuple[
    sessionmaker[Session],
    CandidateLifecycleService,
    ActionEligibilityService,
    PostgresActionEligibilityStore,
]:
    session_factory = factory if factory is not None else persistence_session_factory()
    clock = FrozenClock(EVALUATED_AT)
    repository = PostgresCandidateRepository(session_factory, clock=clock)
    lifecycle = CandidateLifecycleService(repository=repository, clock=clock)
    store = PostgresActionEligibilityStore(session_factory)
    service = ActionEligibilityService(store=store, clock=clock)
    return session_factory, lifecycle, service, store


def _persist_candidate(
    lifecycle: CandidateLifecycleService,
) -> tuple[CanonicalEvidenceWindowV1, SetupAssessment, Candidate]:
    window = make_evidence_window()
    assessment = make_assessment(window)
    candidate = lifecycle.create_from_confirmed_setup(
        make_creation_command(window=window, assessment=assessment)
    )
    return window, assessment, candidate


@requires_postgres
def test_identical_evaluations_converge_after_restart() -> None:
    factory, lifecycle, service, _store = _services()
    window, assessment, candidate = _persist_candidate(lifecycle)
    command = eligibility_command(window=window, assessment=assessment, candidate=candidate)
    first = service.evaluate(command)
    second = service.evaluate(command)
    assert first.eligibility.eligibility_id == second.eligibility.eligibility_id
    assert first.uniqueness_hash == second.uniqueness_hash
    assert first.evaluation_revision == 1
    restarted = ActionEligibilityService(
        store=PostgresActionEligibilityStore(factory), clock=FrozenClock(EVALUATED_AT)
    )
    replay = restarted.evaluate(command)
    assert replay.eligibility.eligibility_id == first.eligibility.eligibility_id
    history = restarted.history(
        organization_id=ORG_ID,
        account_id=ACCOUNT_ID,
        candidate_id=candidate.candidate_id,
    )
    assert len(history) == 1
    assert history[0].content_hash == first.content_hash


@requires_postgres
def test_new_venue_state_appends_revision() -> None:
    _factory, lifecycle, service, store = _services()
    window, assessment, candidate = _persist_candidate(lifecycle)
    first = service.evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    second = service.evaluate(
        eligibility_command(
            window=window,
            assessment=assessment,
            candidate=candidate,
            market=market_action(
                venue_state_id=VENUE_STATE_B,
                required_action_evidence_fresh=False,
            ),
        )
    )
    assert second.uniqueness_hash != first.uniqueness_hash
    assert second.evaluation_revision == 2
    history = store.history(
        organization_id=ORG_ID,
        account_id=ACCOUNT_ID,
        candidate_id=candidate.candidate_id,
    )
    assert [item.evaluation_revision for item in history] == [1, 2]
    assert history[0].content_hash == first.content_hash


@requires_postgres
def test_reused_risk_snapshot_id_with_changed_facts_fails_closed() -> None:
    _factory, lifecycle, service, _store = _services()
    window, assessment, candidate = _persist_candidate(lifecycle)
    first = service.evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    with pytest.raises(ConflictingActionEligibilityError):
        service.evaluate(
            eligibility_command(
                window=window,
                assessment=assessment,
                candidate=candidate,
                risk=risk_snapshot(cooldown_active=True),
            )
        )
    history = service.history(
        organization_id=ORG_ID, account_id=ACCOUNT_ID, candidate_id=candidate.candidate_id
    )
    assert len(history) == 1
    assert history[0].content_hash == first.content_hash


@requires_postgres
def test_missing_canonical_candidate_fails_closed() -> None:
    factory = persistence_session_factory()
    clock = FrozenClock(EVALUATED_AT)
    window = make_evidence_window()
    assessment = make_assessment(window)
    memory = in_memory_candidate_lifecycle(now=EVALUATED_AT)
    candidate = memory.create_from_confirmed_setup(
        make_creation_command(window=window, assessment=assessment)
    )
    service = ActionEligibilityService(store=PostgresActionEligibilityStore(factory), clock=clock)
    with pytest.raises(ActionEligibilityLineageError, match="canonical Candidate"):
        service.evaluate(
            eligibility_command(window=window, assessment=assessment, candidate=candidate)
        )


@requires_postgres
def test_evaluations_are_append_only() -> None:
    _factory, lifecycle, service, store = _services()
    window, assessment, candidate = _persist_candidate(lifecycle)
    evaluation = service.evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    session = store._session_factory()
    try:
        row = session.scalars(
            select(ActionEligibilityEvaluationRow).where(
                ActionEligibilityEvaluationRow.eligibility_id
                == evaluation.eligibility.eligibility_id
            )
        ).first()
        assert row is not None
        row.state = "blocked"
        with pytest.raises(CanonicalEligibilityHistoryImmutabilityError):
            session.commit()
    finally:
        session.rollback()
        session.close()


@requires_postgres
def test_eligibility_binds_canonical_candidate_id() -> None:
    _factory, lifecycle, service, store = _services()
    window, assessment, candidate = _persist_candidate(lifecycle)
    evaluation = service.evaluate(
        eligibility_command(window=window, assessment=assessment, candidate=candidate)
    )
    session = store._session_factory()
    try:
        row = session.get(ActionEligibilityEvaluationRow, evaluation.eligibility.eligibility_id)
        assert row is not None
        assert row.candidate_id == candidate.candidate_id
        assert row.live_executable is False
        assert row.evaluation_revision == 1
    finally:
        session.close()
