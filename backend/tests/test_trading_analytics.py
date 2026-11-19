"""Slice 31 — trading analytics, discipline score, and analytics tool tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.models import Membership, Organization, User
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import (
    MembershipRole,
    RiskSeverity,
    StrategyId,
    TradeDirection,
)
from app.schemas.journal import JournalEntryCreate
from app.schemas.proposal import ExitCriteria, TakeProfitLevel, TradeProposalCreate
from app.security.passwords import hash_password
from app.services.analytics.discipline_score import DisciplineScoreService
from app.services.audit_service import AuditService
from app.services.journal_service import JournalService

ORG_A = uuid.UUID("00000000-0000-0000-0000-000000000040")
USER_A = uuid.UUID("00000000-0000-0000-0000-000000000041")
ORG_B = uuid.UUID("00000000-0000-0000-0000-000000000042")
USER_B = uuid.UUID("00000000-0000-0000-0000-000000000043")


@pytest.fixture
def analytics_db() -> Iterator[tuple[sessionmaker[Session], Settings]]:
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
    settings = Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="analytics-test-secret",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        journal_rag_sync_enabled=True,
    )
    with factory() as session:
        for org_id, user_id, email in (
            (ORG_A, USER_A, "analytics-a@test.example"),
            (ORG_B, USER_B, "analytics-b@test.example"),
        ):
            session.add(Organization(id=org_id, name=f"Org {org_id}"))
            session.add(
                User(
                    id=user_id,
                    email=email,
                    hashed_password=hash_password("TestPassword123!", settings),
                )
            )
            session.flush()
            session.add(
                Membership(user_id=user_id, organization_id=org_id, role=MembershipRole.OWNER)
            )
        session.commit()
    yield factory, settings
    engine.dispose()


def _auth_headers(client: TestClient, email: str) -> dict[str, str]:
    login = client.post(
        "/auth/login",
        json={"email": email, "password": "TestPassword123!"},
    )
    assert login.status_code == 200
    token = login.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_proposal(client: TestClient, headers: dict[str, str]) -> str:
    body = TradeProposalCreate(
        organization_id=ORG_A,
        user_id=USER_A,
        strategy_id=StrategyId.HTF_TREND_PULLBACK,
        symbol="BTCUSDT",
        timeframe="4h",
        direction=TradeDirection.LONG,
        entry_price=Decimal("60000"),
        position_size=Decimal("0.01"),
        leverage=Decimal("3"),
        exit=ExitCriteria(
            stop_loss=Decimal("58000"),
            invalidation="Close below 57500",
            take_profits=[TakeProfitLevel(price=Decimal("62000"), size_fraction=0.5)],
        ),
        confidence=0.72,
        risk_level=RiskSeverity.MEDIUM,
        rationale="Pullback to EMA in uptrend.",
        approval_required=True,
    )
    resp = client.post("/proposals", json=body.model_dump(mode="json"), headers=headers)
    assert resp.status_code == 200
    return resp.json()["id"]


@pytest.fixture
def analytics_client(
    analytics_db: tuple[sessionmaker[Session], Settings],
) -> Iterator[TestClient]:
    factory, settings = analytics_db

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        yield client


def test_discipline_score_bounded(analytics_db: tuple[sessionmaker[Session], Settings]) -> None:
    factory, _ = analytics_db
    with factory() as session:
        result = DisciplineScoreService(session).compute(organization_id=ORG_A, user_id=USER_A)
        assert 0 <= result.score <= 100
        assert result.grade in {"A", "B", "C", "D", "F"}


def test_journal_rag_sync_still_works(
    analytics_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = analytics_db
    with factory() as session:
        from app.services.journal_rag_sync_service import JournalRagSyncService
        from app.services.rag_service import build_rag_service

        audit = AuditService(session)
        rag = build_rag_service(settings, session, audit_service=audit)
        journal = JournalService(session, audit, rag_sync=JournalRagSyncService(rag, settings))
        unique = f"Unique lesson analytics {uuid.uuid4().hex[:8]}"
        journal.create(
            JournalEntryCreate(
                organization_id=ORG_A,
                user_id=USER_A,
                symbol="BTCUSDT",
                timeframe="1h",
                direction=TradeDirection.LONG,
                strategy_id=StrategyId.MANUAL_REVIEW,
                entry_rationale="Test trade",
                lessons=unique,
                improvement_rule="Wait for confirmation",
                mistakes=["fomo"],
                emotions=["anxious"],
            )
        )
        session.commit()
        from app.schemas.common import DocumentSourceType
        from app.schemas.rag import RagQuery
        from tests.support.knowledge_indexing import drain_indexing

        assert drain_indexing(rag) == ["ready"]
        search = rag.search(
            RagQuery(
                query=unique,
                organization_id=ORG_A,
                user_id=USER_A,
                top_k=3,
                source_types=[DocumentSourceType.TRADE_JOURNAL],
            )
        )
        assert any(unique in (c.content or "") for c in search.chunks)
