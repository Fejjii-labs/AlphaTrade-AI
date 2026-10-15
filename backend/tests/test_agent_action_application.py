"""V3 applications use canonical authorities and preserve review/execution gates."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.api.routes import interactive_agent as agent_routes
from app.core.auth import get_current_tenant
from app.core.config import get_settings
from app.core.errors import (
    ConflictError,
    NotFoundError,
    QuotaExceededError,
    register_exception_handlers,
)
from app.db.canonical_candidates import CanonicalCandidateRow
from app.db.models import (
    BacktestRun,
    Chunk,
    ConversationMessage,
    Document,
    KnowledgeIndexingJob,
    Order,
    StrategyConversationProposal,
    TradeProposal,
    UserStrategyVersion,
)
from app.db.session import get_session
from app.interactive_agent import application
from app.interactive_agent.contracts import ProposalLifecycle
from app.interactive_agent.proposals import find_proposal, proposal_hash_body
from app.interactive_agent.service import InteractiveAgentService
from app.providers.embeddings import MockEmbeddingsProvider
from app.providers.qdrant import InMemoryVectorStore
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.common import BacktestRunStatus, MembershipRole, StrategyProposalStatus
from app.schemas.usage import OrganizationQuotaUpdate
from app.security.rate_limit import get_rate_limiter
from app.security.tenant import TenantContext
from app.services.canonical_serialization import canonical_sha256
from app.services.quota_service import QuotaService
from app.services.rag_service import RagService, build_rag_service
from tests.support.knowledge_indexing import drain_indexing
from tests.test_agent_action_orchestration import confirm, strategy, turn
from tests.test_interactive_agent_foundation import (
    ORG_A,
    ORG_B,
    USER_A,
    USER_A2,
    _count,
)

pytest_plugins = ("tests.test_interactive_agent_foundation",)


@pytest.fixture
def application_client(agent_db, monkeypatch):
    factory, settings = agent_db
    app = FastAPI()
    app.include_router(agent_routes.router)
    register_exception_handlers(app)

    def scoped_session():
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = scoped_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_current_tenant] = lambda: TenantContext(
        organization_id=ORG_A,
        user_id=USER_A,
        email="application@test.example",
        membership_role=MembershipRole.OWNER,
    )
    monkeypatch.setattr(
        agent_routes,
        "_service",
        lambda session, settings, organization_id: InteractiveAgentService(
            session, settings=settings
        ),
    )
    with TestClient(app) as client:
        yield client, factory


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("knowledge.propose", {"text": "Wait for the close"}),
        ("watcher.change", {"operation": "disable", "position": 2}),
    ],
)
def test_http_application_returns_canonical_identity_and_replays_receipt(
    application_client, name, arguments
):
    client, factory = application_client
    response = client.post(
        "/agent/turns",
        json={
            "message": "Please draft this action",
            "action": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200
    turn_body = response.json()
    proposal = turn_body["proposals"][0]
    path = f"/agent/proposals/{proposal['proposal_id']}/confirm"
    body = {
        "conversation_id": turn_body["conversation_id"],
        "expected_content_hash": proposal["content_hash"],
        "statement": "I confirm",
    }
    assert client.post(path, json=dict(body, expected_content_hash="0" * 64)).status_code == 409
    response = client.post(path, json=body)
    assert response.status_code == 200
    receipt = response.json()
    assert receipt["applied"] and receipt["resulting_record_id"]
    assert receipt["content_hash"] == proposal["content_hash"]
    assert client.post(path, json=body).json() == receipt
    with factory() as session:
        assert _count(session, Order) == _count(session, CanonicalCandidateRow) == 0


def test_http_validation_hands_one_committed_job_to_existing_scheduler(
    application_client, monkeypatch
):
    from app.api.routes import backtests

    client, factory = application_client
    scheduled = []

    def observe_enqueue(**kwargs):
        with factory() as session:
            assert session.get(BacktestRun, kwargs["result"].id) is not None
        scheduled.append(kwargs["result"].id)

    monkeypatch.setattr(backtests, "enqueue_backtest_if_needed", observe_enqueue)
    with factory() as session:
        target = strategy(session)
        session.commit()
    response = client.post(
        "/agent/turns",
        json={
            "message": "Please validate these dates",
            "action": {
                "name": "strategy.request_validation",
                "arguments": {
                    "text": "Historical validation",
                    "strategy_id": str(target.id),
                    "backtest": {
                        "assumptions": {
                            "symbol": "BTCUSDT",
                            "timeframe": "4h",
                            "start_date": "2026-09-01",
                            "end_date": "2026-09-02",
                        }
                    },
                },
            },
        },
    )
    assert response.status_code == 200
    turn_body = response.json()
    proposal = turn_body["proposals"][0]
    path = f"/agent/proposals/{proposal['proposal_id']}/confirm"
    body = {
        "conversation_id": turn_body["conversation_id"],
        "expected_content_hash": proposal["content_hash"],
        "statement": "I confirm",
    }
    applied = client.post(path, json=body)
    assert applied.status_code == 200 and applied.json()["status"] == "applied"
    assert len(scheduled) == 1
    assert client.post(path, json=body).json() == applied.json()
    assert len(scheduled) == 1


def test_http_application_shares_knowledge_ingest_rate_limit(application_client):
    client, factory = application_client
    limiter = get_rate_limiter()
    for _ in range(20):
        limiter.check(f"knowledge:ingest:user:{USER_A}", limit=20, window_seconds=3600)
    response = client.post(
        "/agent/turns",
        json={
            "message": "Draft a note",
            "action": {
                "name": "knowledge.propose",
                "arguments": {"text": "Wait"},
            },
        },
    )
    assert response.status_code == 200
    turn_body = response.json()
    proposal = turn_body["proposals"][0]
    response = client.post(
        f"/agent/proposals/{proposal['proposal_id']}/confirm",
        json={
            "conversation_id": turn_body["conversation_id"],
            "expected_content_hash": proposal["content_hash"],
            "statement": "I confirm",
        },
    )
    assert response.status_code == 429
    with factory() as session:
        assert _count(session, Document) == _count(session, Chunk) == 0


def test_knowledge_receipt_retains_provenance_links_hash_and_duplicate_identity(agent_db):
    factory, settings = agent_db
    with factory() as session:
        target = strategy(session)
        evidence = Document(
            organization_id=ORG_A, user_id=None, title="Shared evidence", source_type="review_note"
        )
        session.add(evidence)
        session.flush()
        service, result = turn(
            session,
            settings,
            name="knowledge.propose",
            arguments={
                "text": "Wait for a closed candle",
                "strategy_id": str(target.id),
                "evidence_document_ids": [str(evidence.id)],
            },
        )
        before = result.proposals[0]
        applied = confirm(service, result)
        session.commit()
        assert applied.status is ProposalLifecycle.APPLIED
        assert applied.payload == before.payload and applied.content_hash == before.content_hash
        assert canonical_sha256(proposal_hash_body(applied)) == before.content_hash
        doc = session.get(Document, applied.resulting_record_id)
        assert doc.organization_id == ORG_A and doc.user_id == USER_A
        assert str(result.conversation_id) in doc.uri
        chunks = session.scalars(select(Chunk).where(Chunk.document_id == doc.id)).all()
        assert chunks and all(chunk.embedding_ref is None for chunk in chunks)
        assert applied.application_result["vector_index_status"] == "pending"
        content = " ".join(chunk.content for chunk in chunks)
        assert str(evidence.id) in content and str(result.user_message_id) in content
        assert all(chunk.chunk_metadata["strategy_tag"] == str(target.id) for chunk in chunks)
        assert applied.application_result["evidence_links_persisted"] == [str(evidence.id)]
        assert confirm(service, result) == applied
        assert _count(session, Document) == 2
        assert drain_indexing(build_rag_service(settings, session)) == ["ready"]
        assert all(chunk.embedding_ref for chunk in chunks)


def test_identical_private_knowledge_from_same_org_users_never_deduplicates_across_users(agent_db):
    factory, settings = agent_db
    with factory() as session:
        identities = []
        for user in (USER_A, USER_A2):
            service, result = turn(
                session,
                settings,
                name="knowledge.propose",
                arguments={"text": "Wait"},
                user_id=user,
            )
            applied = confirm(service, result, user_id=user)
            session.commit()
            identities.append(applied.resulting_record_id)
            assert session.get(Document, applied.resulting_record_id).user_id == user
        assert len(set(identities)) == 2


def test_ingestion_and_agent_receipt_remain_in_the_callers_unit_of_work(agent_db):
    factory, settings = agent_db
    with factory() as session:
        service, result = turn(
            session, settings, name="knowledge.propose", arguments={"text": "Wait"}
        )
        commits = []

        def record_outer_commit(committed_session):
            if not committed_session.in_nested_transaction():
                commits.append(True)

        event.listen(session, "after_commit", record_outer_commit)
        applied = confirm(service, result)
        assert applied.applied and commits == []
        session.rollback()
        assert _count(session, Document) == _count(session, Chunk) == 0
        _, stored = find_proposal(
            session,
            conversation_id=result.conversation_id,
            organization_id=ORG_A,
            user_id=USER_A,
            proposal_id=result.proposals[0].proposal_id,
        )
        assert stored.status is ProposalLifecycle.PROPOSED
        applied = confirm(service, result)
        session.commit()
        assert confirm(service, result) == applied
        assert _count(session, Document) == 1


def test_ingestion_provider_outage_preserves_application_and_pending_indexing(
    agent_db, monkeypatch
):
    class UnavailableVectors(InMemoryVectorStore):
        def upsert(self, collection, points):
            raise RuntimeError("vector backend unavailable")

    factory, settings = agent_db
    store = UnavailableVectors()
    with factory() as session:
        monkeypatch.setattr(
            application,
            "build_rag_service",
            lambda *_args, **_kwargs: RagService(
                session,
                settings=settings,
                embeddings=MockEmbeddingsProvider(),
                vector_store=store,
            ),
        )
        service, result = turn(
            session, settings, name="knowledge.propose", arguments={"text": "Wait"}
        )
        applied = confirm(service, result)
        assert applied.applied and applied.application_result["vector_index_status"] == "pending"
        session.commit()
        assert _count(session, Document) == _count(session, Chunk) == 1
        rag = RagService(
            session, settings=settings, embeddings=MockEmbeddingsProvider(), vector_store=store
        )
        assert drain_indexing(rag) == ["pending"]
        job = session.scalar(select(KnowledgeIndexingJob))
        assert job.status == "pending" and job.attempts == 1 and job.error_code == "RuntimeError"
        assert all(chunk.embedding_ref is None for chunk in session.scalars(select(Chunk)))
        _, stored = find_proposal(
            session,
            conversation_id=result.conversation_id,
            organization_id=ORG_A,
            user_id=USER_A,
            proposal_id=result.proposals[0].proposal_id,
        )
        assert stored.status is ProposalLifecycle.APPLIED
        assert stored.resulting_record_id == applied.resulting_record_id
        assert confirm(service, result) == applied


def test_agent_ingestion_cannot_bypass_canonical_quota(agent_db):
    factory, settings = agent_db
    with factory() as session:
        QuotaService(session).update_quota(ORG_A, OrganizationQuotaUpdate(limit_rag_ingest=0))
        service, result = turn(
            session, settings, name="knowledge.propose", arguments={"text": "Wait"}
        )
        with pytest.raises(QuotaExceededError):
            confirm(service, result)
        assert _count(session, Document) == _count(session, Chunk) == 0


@pytest.mark.parametrize(
    "name", ["strategy.observation", "strategy.hypothesis", "strategy.associate_evidence"]
)
def test_research_records_persist_in_canonical_knowledge_without_strategy_activation(
    agent_db, name
):
    factory, settings = agent_db
    with factory() as session:
        target = strategy(session)
        evidence = Document(
            organization_id=ORG_A, user_id=USER_A, title="Research", source_type="review_note"
        )
        session.add(evidence)
        session.flush()
        versions = _count(session, UserStrategyVersion)
        service, result = turn(
            session,
            settings,
            name=name,
            arguments={
                "text": "Second pullbacks may be weaker",
                "strategy_id": str(target.id),
                "evidence_document_ids": [str(evidence.id)],
            },
        )
        applied = confirm(service, result)
        session.commit()
        assert applied.applied and applied.resulting_record_id != evidence.id
        assert applied.application_result["record_type"] == "documents"
        assert _count(session, UserStrategyVersion) == versions
        assert confirm(service, result) == applied


def test_research_confirmation_rechecks_evidence_scope(agent_db):
    factory, settings = agent_db
    with factory() as session:
        doc = Document(
            organization_id=ORG_A, user_id=USER_A, title="Private", source_type="review_note"
        )
        session.add(doc)
        session.flush()
        service, result = turn(
            session,
            settings,
            name="knowledge.propose",
            arguments={
                "text": "Wait",
                "evidence_document_ids": [str(doc.id)],
            },
        )
        doc.user_id = USER_A2
        session.commit()
        with pytest.raises(NotFoundError):
            confirm(service, result)
        assert _count(session, Document) == 1 and _count(session, Chunk) == 0


def test_watcher_application_fences_competing_requests_and_replays_original_receipt(agent_db):
    factory, settings = agent_db
    with factory() as session:
        repo = WatcherWatchlistRepository(session)
        other_tenant = repo.load(ORG_B)
        service, first = turn(
            session,
            settings,
            name="watcher.change",
            arguments={
                "operation": "replace",
                "position": 2,
                "symbol": "SOLUSDT",
            },
        )
        _, competing = turn(
            session,
            settings,
            name="watcher.change",
            arguments={
                "operation": "disable",
                "position": 2,
            },
        )
        applied = confirm(service, first)
        session.commit()
        with pytest.raises(ConflictError):
            confirm(service, competing)
        assert repo.load(ORG_B) == other_tenant
        assert len(repo.load(ORG_A).slots) == 5
        assert repo.load(ORG_A).slots[1].symbol == "SOLUSDT"
        service, later = turn(
            session,
            settings,
            name="watcher.change",
            arguments={
                "operation": "disable",
                "position": 2,
            },
        )
        confirm(service, later)
        session.commit()
        assert confirm(service, first) == applied
        assert repo.load(ORG_A).revision == 2


def test_confirmed_incomplete_watcher_replays_without_new_revision_checks(agent_db):
    factory, settings = agent_db
    with factory() as session:
        service, result = turn(
            session,
            settings,
            name="watcher.change",
            arguments={
                "operation": "replace",
            },
        )
        confirmed = confirm(service, result)
        session.commit()
        service, complete = turn(
            session,
            settings,
            name="watcher.change",
            arguments={
                "operation": "disable",
                "position": 2,
            },
        )
        confirm(service, complete)
        session.commit()
        assert confirmed.status is ProposalLifecycle.CONFIRMED_UNAPPLIED
        assert confirm(service, result) == confirmed


def test_changed_or_superseded_refinement_is_not_applied(agent_db):
    factory, settings = agent_db
    with factory() as session:
        target = strategy(session)
        service, result = turn(
            session,
            settings,
            name="strategy.refinement",
            arguments={
                "text": "Wait for the close",
                "strategy_id": str(target.id),
            },
        )
        draft = session.get(
            StrategyConversationProposal, result.proposals[0].linked_strategy_proposal_id
        )
        draft.context_refs = {"evidence_document_ids": [], "changed": True}
        session.commit()
        with pytest.raises(ConflictError):
            confirm(service, result)
        assert draft.status is StrategyProposalStatus.DRAFT
        assert _count(session, UserStrategyVersion) == 1


def test_complete_validation_creates_one_observable_canonical_job_without_inline_execution(
    agent_db,
):
    factory, settings = agent_db
    with factory() as session:
        target = strategy(session)
        version = session.scalar(
            select(UserStrategyVersion).where(UserStrategyVersion.strategy_id == target.id)
        )
        service, result = turn(
            session,
            settings,
            name="strategy.request_validation",
            arguments={
                "text": "Replay these dates",
                "strategy_id": str(target.id),
                "backtest": {
                    "assumptions": {
                        "symbol": "BTCUSDT",
                        "timeframe": "4h",
                        "start_date": "2026-09-01",
                        "end_date": "2026-09-02",
                    }
                },
            },
        )
        assert _count(session, BacktestRun) == 0
        assert result.proposals[0].payload["backtest_request"]["strategy_version_id"] == str(
            version.id
        )
        applied = confirm(service, result)
        session.commit()
        assert applied.status is ProposalLifecycle.APPLIED
        run = session.get(BacktestRun, applied.resulting_record_id)
        assert run.organization_id == ORG_A and run.user_id == USER_A
        assert run.status is BacktestRunStatus.QUEUED and run.result is None
        assert run.strategy_version_id == version.id
        assert applied.application_result["validation_ran"] is False
        assert confirm(service, result) == applied
        assert _count(session, BacktestRun) == 1


@pytest.mark.parametrize("backtest", [None, {}, {"assumptions": {"symbol": "BTCUSDT"}}])
def test_incomplete_replay_preserves_confirmed_unapplied_without_invented_job(agent_db, backtest):
    factory, settings = agent_db
    with factory() as session:
        target = strategy(session)
        service, result = turn(
            session,
            settings,
            name="strategy.request_validation",
            arguments={
                "text": "Validate later",
                "strategy_id": str(target.id),
                "backtest": backtest,
            },
        )
        confirmed = confirm(service, result)
        assert confirmed.status is ProposalLifecycle.CONFIRMED_UNAPPLIED
        assert confirmed.resulting_record_id is None
        assert _count(session, BacktestRun) == 0


def test_validation_rejects_a_version_from_another_tenant(agent_db):
    factory, settings = agent_db
    with factory() as session:
        target = strategy(session)
        foreign = strategy(session, organization_id=ORG_A, user_id=USER_A2)
        version = session.scalar(
            select(UserStrategyVersion).where(UserStrategyVersion.strategy_id == foreign.id)
        )
        session.commit()
        with pytest.raises(NotFoundError):
            turn(
                session,
                settings,
                name="strategy.request_validation",
                arguments={
                    "text": "Validate",
                    "strategy_id": str(target.id),
                    "backtest": {
                        "strategy_version_id": str(version.id),
                        "assumptions": {
                            "symbol": "BTCUSDT",
                            "timeframe": "4h",
                            "start_date": "2026-09-01",
                            "end_date": "2026-09-02",
                        },
                    },
                },
            )
        assert _count(session, BacktestRun) == 0


def test_complete_conversational_paper_request_runs_only_canonical_advisory_pretrade(agent_db):
    factory, settings = agent_db
    with factory() as session:
        service, result = turn(
            session,
            settings,
            message=(
                "Enter a paper trade BTCUSDT long 1h entry 100 stop 95 targets 110, 120. "
                "Account size 10000 max risk 1%. I confirm"
            ),
        )
        assert result.proposals[0].payload["missing_fields"] == []
        assert not session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.intent == "paper_pretrade_analysis"
            )
        )
        applied = confirm(service, result)
        session.commit()
        assert applied.status is ProposalLifecycle.APPLIED
        row = session.get(ConversationMessage, applied.resulting_record_id)
        assert row.organization_id == ORG_A and row.user_id == USER_A
        assert row.payload["pretrade_request"]["account_size"] == "10000"
        assert row.payload["stated_trade"] == {
            "entry": "100",
            "stop": "95",
            "targets": ["110", "120"],
        }
        assert row.payload["risk_assessed"] is row.payload["execution_attempted"] is False
        assert row.payload["pretrade_result"]["position_size"]
        assert confirm(service, result) == applied
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
        for model in (Order, CanonicalCandidateRow, TradeProposal):
            assert _count(session, model) == 0


def test_paper_pretrade_requires_explicit_risk_budget(agent_db):
    factory, settings = agent_db
    with factory() as session:
        service, result = turn(
            session,
            settings,
            name="paper_trade.propose",
            arguments={
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "direction": "long",
                "entry": "100",
                "stop": "95",
                "targets": ["110"],
                "pretrade": {
                    "symbol": "BTCUSDT",
                    "timeframe": "1h",
                    "direction": "long",
                    "account_size": "10000",
                },
            },
        )
        assert "pretrade.max_risk_per_trade" in result.proposals[0].payload["missing_fields"]
        assert confirm(service, result).status is ProposalLifecycle.CONFIRMED_UNAPPLIED
        assert _count(session, CanonicalCandidateRow) == _count(session, Order) == 0
