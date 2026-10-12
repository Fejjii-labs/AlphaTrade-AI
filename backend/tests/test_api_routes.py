"""Integration tests for wired API routes (slices 5-8)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_session
from app.main import create_app
from app.rag.indexing import IndexingRunner


@pytest.fixture
def client_with_db() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    settings = Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="api-route-test-secret-32-bytes-minimum",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as test_client:
        register = test_client.post(
            "/auth/register",
            json={
                "email": "routes@test.example",
                "password": "secure-password-1",
                "organization_name": "Routes Org",
            },
        )
        assert register.status_code == 201
        token = register.json()["tokens"]["access_token"]
        test_client.headers.update({"Authorization": f"Bearer {token}"})
        yield test_client, factory

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_audit_events_endpoint(client_with_db: tuple[TestClient, object]) -> None:
    client, _factory = client_with_db
    response = client.get("/audit/events")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body


def test_usage_events_and_summary(client_with_db: tuple[TestClient, object]) -> None:
    client, _factory = client_with_db
    events = client.get("/usage/events")
    assert events.status_code == 200
    summary = client.get("/usage/summary")
    assert summary.status_code == 200
    assert summary.json()["cost_is_placeholder"] is True


def test_risk_check_endpoint(client_with_db: tuple[TestClient, object]) -> None:
    client, _factory = client_with_db
    response = client.post(
        "/risk/check",
        json={
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": "60000",
            "position_size": "0.01",
            "leverage": "3",
            "account_equity": "10000",
            "stop_loss": "58000",
        },
    )
    assert response.status_code == 200
    assert response.json()["action"] in {"allow", "warn", "block"}


def test_strategies_list_and_evaluate(client_with_db: tuple[TestClient, object]) -> None:
    client, _factory = client_with_db
    listing = client.get("/strategies/modules")
    assert listing.status_code == 200
    assert len(listing.json()) == 7

    response = client.post(
        "/strategies/evaluate",
        json={
            "strategy_id": "liquidity_sweep_reversal",
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "close": "60000",
            "volume": "1000000",
            "liquidity_sweep_detected": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["signal"] is not None
    assert body["research_only"] is True
    assert body["mutates_strategy_authority"] is False
    assert any("research-only" in note.lower() for note in body["limitations"])


def test_tools_list(client_with_db: tuple[TestClient, object]) -> None:
    client, _factory = client_with_db
    response = client.get("/tools")
    assert response.status_code == 200
    assert len(response.json()) >= 10


def test_knowledge_ingest_and_search(
    client_with_db: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = client_with_db
    ingest = client.post(
        "/knowledge/ingest",
        json={
            "source_type": "risk_policy",
            "title": "Risk Policy",
            "text": "Stop loss is mandatory on every trade.",
            "risk_tag": "stop_loss",
        },
    )
    assert ingest.status_code == 200
    body = ingest.json()
    assert body["chunk_count"] >= 1
    assert body["duplicate"] is False
    assert body["vector_index_status"] == "pending"
    resolved = client.app.state.resolved_providers
    assert IndexingRunner(
        factory,
        embeddings=resolved.embeddings,
        vector_store=resolved.vector_store,
        settings=client.app.state.settings,
    ).run_once() == ["ready"]

    search = client.post(
        "/knowledge/search",
        json={
            "query": "stop loss",
            "source_types": ["risk_policy"],
            "top_k": 3,
        },
    )
    assert search.status_code == 200
    hits = search.json()
    assert hits["chunks"]
    assert hits["citations"]

    documents = client.get("/knowledge/documents")
    assert documents.status_code == 200
    assert documents.json()["total"] >= 1

    chunks = client.get(
        "/knowledge/chunks",
        params={"document_id": body["document_id"]},
    )
    assert chunks.status_code == 200
    assert chunks.json()["total"] >= 1


def test_knowledge_create_document(
    client_with_db: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _factory = client_with_db
    response = client.post(
        "/knowledge/documents",
        json={
            "source_type": "general_note",
            "title": "Architecture Note",
        },
    )
    assert response.status_code == 200
    assert response.json()["title"] == "Architecture Note"


def test_execution_paper_order(client_with_db: tuple[TestClient, sessionmaker[Session]]) -> None:
    from decimal import Decimal

    from app.db.models import ApprovalRequest, TradeProposal
    from app.schemas.common import (
        ApprovalStatus,
        RiskAction,
        RiskSeverity,
        StrategyId,
        TradeDirection,
    )
    from app.schemas.risk import RiskCheckResult

    client, factory = client_with_db
    me = client.get("/auth/me").json()
    org_id = uuid.UUID(me["organization"]["id"])
    user_id = uuid.UUID(me["user"]["id"])
    with factory() as session:
        proposal = TradeProposal(
            organization_id=org_id,
            user_id=user_id,
            strategy_id=StrategyId.HTF_TREND_PULLBACK,
            symbol="BTCUSDT",
            timeframe="4h",
            direction=TradeDirection.LONG,
            entry_price=Decimal("60000"),
            position_size=Decimal("0.005"),
            leverage=Decimal("3"),
            stop_loss=Decimal("58000"),
            take_profits=[{"price": "62000", "size_fraction": 0.5}],
            invalidation="test",
            confidence=0.7,
            risk_level=RiskSeverity.MEDIUM,
            rationale="test",
            risk_result=RiskCheckResult(
                action=RiskAction.ALLOW,
                severity=RiskSeverity.LOW,
                explanation="api route allow",
                approval_required=True,
            ).model_dump(mode="json"),
        )
        session.add(proposal)
        session.flush()
        approval = ApprovalRequest(
            proposal_id=proposal.id,
            organization_id=org_id,
            user_id=user_id,
            status=ApprovalStatus.APPROVED,
            risk_level=RiskSeverity.MEDIUM,
            confidence=0.7,
        )
        session.add(approval)
        session.commit()
        proposal_id = proposal.id
        approval_id = approval.id

    response = client.post(
        "/execution/paper",
        json={
            "proposal_id": str(proposal_id),
            "approval_id": str(approval_id),
            "symbol": "BTCUSDT",
            "side": "buy",
            "type": "market",
            "size": "0.005",
            "idempotency_key": "test-key-001",
        },
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "trading_policy_violation"
    assert "EXECUTE_PAPER_PLAN" in body["error"]["message"]


def test_execute_paper_plan_http_rejects_extra_fields(
    client_with_db: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _factory = client_with_db
    response = client.post(
        "/execution/paper-plan",
        json={
            "account_id": str(uuid.uuid4()),
            "authorization_id": str(uuid.uuid4()),
            "revision_id": str(uuid.uuid4()),
            "idempotency_key": "http-extra",
            "side": "buy",
        },
    )
    assert response.status_code == 422


def test_execute_paper_plan_http_unknown_revision_is_not_found(
    client_with_db: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _factory = client_with_db
    response = client.post(
        "/execution/paper-plan",
        json={
            "account_id": str(uuid.uuid4()),
            "authorization_id": str(uuid.uuid4()),
            "revision_id": str(uuid.uuid4()),
            "idempotency_key": "http-missing",
        },
    )
    assert response.status_code == 404
