"""Stateful workflow persistence tests (Slice 13)."""

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
from app.core.errors import NotFoundError, TradingPolicyError
from app.db.base import Base
from app.db.models import (
    AuditLog,
    ExchangeFill,
    ExchangeOrder,
    Membership,
    Order,
    Organization,
    Position,
    User,
)
from app.db.session import get_session
from app.main import create_app
from app.providers.exchange.base import (
    ExchangeFill as ExchangeFillDTO,
)
from app.providers.exchange.base import (
    ExchangeOrderRequest,
    ExchangeOrderResult,
)
from app.providers.exchange.errors import ExchangeRequestError, VenueErrorDetails
from app.schemas.approval import ApprovalDecisionRequest
from app.schemas.common import (
    ApprovalAction,
    AuditEventType,
    MembershipRole,
    PositionStatus,
    RiskAction,
    RiskRuleId,
    RiskSeverity,
    StrategyId,
    TradeDirection,
)
from app.schemas.execution import PaperOrderRequest
from app.schemas.proposal import ExitCriteria, TakeProfitLevel, TradeProposalCreate
from app.schemas.risk import RiskCheckResult, TriggeredRule
from app.security.passwords import hash_password
from app.services.agent_service import AgentInvokeContext, build_agent_service
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService
from app.services.execution_service import LEGACY_PAPER_EXECUTION_REASON, ExecutionService
from app.services.proposal_service import ProposalService

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000030")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000031")


@pytest.fixture
def workflow_db() -> Iterator[tuple[sessionmaker[Session], Settings]]:
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
        jwt_secret="workflow-test-secret",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
    )
    with factory() as session:
        org = Organization(id=ORG_ID, name="Workflow Org")
        user = User(
            id=USER_ID,
            email="wf@test.example",
            hashed_password=hash_password("TestPassword123!", settings),
        )
        session.add(org)
        session.add(user)
        session.flush()
        session.add(
            Membership(
                user_id=USER_ID,
                organization_id=ORG_ID,
                role=MembershipRole.OWNER,
            )
        )
        session.commit()
    yield factory, settings
    engine.dispose()


@pytest.fixture
def client_with_workflow_db(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> Iterator[TestClient]:
    factory, settings = workflow_db

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as test_client:
        login = test_client.post(
            "/auth/login",
            json={"email": "wf@test.example", "password": "TestPassword123!"},
        )
        assert login.status_code == 200
        token = login.json()["tokens"]["access_token"]
        test_client.headers.update({"Authorization": f"Bearer {token}"})
        yield test_client


def _exit() -> ExitCriteria:
    return ExitCriteria(
        invalidation="Close below stop.",
        stop_loss=Decimal("58000"),
        take_profits=[TakeProfitLevel(price=Decimal("62000"), size_fraction=0.5)],
    )


def _proposal_payload(**overrides: object) -> dict:
    payload = {
        "organization_id": str(ORG_ID),
        "user_id": str(USER_ID),
        "strategy_id": "htf_trend_pullback",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "direction": "long",
        "entry_price": "60000",
        "position_size": "0.01",
        "leverage": "3",
        "exit": {
            "invalidation": "Close below stop.",
            "stop_loss": "58000",
            "take_profits": [{"price": "62000", "size_fraction": 0.5}],
        },
        "confidence": 0.7,
        "risk_level": "medium",
        "rationale": "Test proposal",
        "approval_required": True,
    }
    payload.update(overrides)
    return payload


def _seed_approved_proposal(session: Session, settings: Settings) -> tuple[uuid.UUID, uuid.UUID]:
    audit = AuditService(session)
    proposals = ProposalService(session, audit)
    approvals = ApprovalService(session, audit)
    proposal = proposals.create(
        TradeProposalCreate(
            organization_id=ORG_ID,
            user_id=USER_ID,
            strategy_id=StrategyId.HTF_TREND_PULLBACK,
            symbol="BTCUSDT",
            timeframe="4h",
            direction="long",
            entry_price=Decimal("60000"),
            position_size=Decimal("0.005"),
            leverage=Decimal("3"),
            exit=_exit(),
            confidence=0.7,
            risk_level=RiskSeverity.MEDIUM,
            rationale="seed",
            approval_required=True,
            risk_result=RiskCheckResult(
                action=RiskAction.ALLOW,
                severity=RiskSeverity.LOW,
                explanation="seed risk ok",
                approval_required=True,
            ),
        )
    )
    approval = approvals.create_for_proposal(
        proposal_id=proposal.id,  # type: ignore[arg-type]
        organization_id=ORG_ID,
        user_id=USER_ID,
        risk_level=proposal.risk_level,
        confidence=float(proposal.confidence),
    )
    approvals.decide(approval.id, ApprovalDecisionRequest(action=ApprovalAction.APPROVE))
    session.commit()
    return proposal.id, approval.id  # type: ignore[return-value]


def test_watchlist_crud(client_with_workflow_db: TestClient) -> None:
    client = client_with_workflow_db
    create = client.post(
        "/market/watchlist",
        json={
            "organization_id": str(ORG_ID),
            "user_id": str(USER_ID),
            "symbol": "BTCUSDT",
            "exchange": "binance",
            "timeframes": ["4h", "1h"],
            "strategy_ids": ["htf_trend_pullback"],
        },
    )
    assert create.status_code == 200
    item_id = create.json()["id"]

    listing = client.get(
        "/market/watchlist",
        params={"limit": 50},
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    updated = client.patch(
        f"/market/watchlist/{item_id}",
        params={"limit": 50},
        json={"enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False

    deleted = client.delete(
        f"/market/watchlist/{item_id}",
        params={"limit": 50},
    )
    assert deleted.status_code == 204


def test_proposal_create_list_and_status(client_with_workflow_db: TestClient) -> None:
    client = client_with_workflow_db
    created = client.post("/proposals", json=_proposal_payload())
    assert created.status_code == 200
    proposal_id = created.json()["id"]

    fetched = client.get(f"/proposals/{proposal_id}")
    assert fetched.status_code == 200

    listed = client.get("/proposals")
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1

    patched = client.patch(
        f"/proposals/{proposal_id}/status",
        json={"status": "approved"},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "approved"


def _authed_test_client(
    factory: sessionmaker[Session],
    settings: Settings,
) -> tuple[TestClient, object]:
    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    login = client.post(
        "/auth/login",
        json={"email": "wf@test.example", "password": "TestPassword123!"},
    )
    assert login.status_code == 200
    client.headers.update({"Authorization": f"Bearer {login.json()['tokens']['access_token']}"})
    return client, app


def test_approval_decisions_via_api(workflow_db: tuple[sessionmaker[Session], Settings]) -> None:
    factory, settings = workflow_db
    with factory() as session:
        audit = AuditService(session)
        proposals = ProposalService(session, audit)
        approvals = ApprovalService(session, audit)
        proposal = proposals.create(
            TradeProposalCreate(
                organization_id=ORG_ID,
                user_id=USER_ID,
                strategy_id=StrategyId.HTF_TREND_PULLBACK,
                symbol="BTCUSDT",
                timeframe="4h",
                direction="long",
                entry_price=Decimal("60000"),
                position_size=Decimal("0.005"),
                leverage=Decimal("3"),
                exit=_exit(),
                confidence=0.7,
                risk_level=RiskSeverity.MEDIUM,
                rationale="approval test",
                approval_required=True,
            )
        )
        approval = approvals.create_for_proposal(
            proposal_id=proposal.id,  # type: ignore[arg-type]
            organization_id=ORG_ID,
            user_id=USER_ID,
            risk_level=proposal.risk_level,
            confidence=float(proposal.confidence),
        )
        session.commit()
        approval_id = approval.id

    client, _app = _authed_test_client(factory, settings)
    with client:
        approved = client.post(f"/approvals/{approval_id}/approve", json={"reason": "ok"})
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        rejected_id = client.post("/proposals", json=_proposal_payload()).json()["id"]
        with factory() as session:
            audit = AuditService(session)
            row = ApprovalService(session, audit).create_for_proposal(
                proposal_id=uuid.UUID(rejected_id),
                organization_id=ORG_ID,
                user_id=USER_ID,
                risk_level=RiskSeverity.MEDIUM,
                confidence=0.7,
            )
            session.commit()
            reject_target = row.id

        rejected = client.post(f"/approvals/{reject_target}/reject", json={"reason": "no"})
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"

        with factory() as session:
            audit = AuditService(session)
            nma_proposal = ProposalService(session, audit).create(
                TradeProposalCreate(
                    organization_id=ORG_ID,
                    user_id=USER_ID,
                    strategy_id=StrategyId.HTF_TREND_PULLBACK,
                    symbol="BTCUSDT",
                    timeframe="4h",
                    direction="long",
                    entry_price=Decimal("60000"),
                    position_size=Decimal("0.005"),
                    leverage=Decimal("3"),
                    exit=_exit(),
                    confidence=0.7,
                    risk_level=RiskSeverity.MEDIUM,
                    rationale="nma test",
                    approval_required=True,
                )
            )
            nma = ApprovalService(session, audit).create_for_proposal(
                proposal_id=nma_proposal.id,  # type: ignore[arg-type]
                organization_id=ORG_ID,
                user_id=USER_ID,
                risk_level=RiskSeverity.MEDIUM,
                confidence=0.7,
            )
            session.commit()
            nma_id = nma.id

        nma_resp = client.post(
            f"/approvals/{nma_id}/needs-more-analysis",
            json={"reason": "need charts"},
        )
        assert nma_resp.status_code == 200
        assert nma_resp.json()["status"] == "needs_more_analysis"


def test_paper_execution_success_and_idempotency(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = workflow_db
    with factory() as session:
        proposal_id, approval_id = _seed_approved_proposal(session, settings)
        execution = ExecutionService(session, settings, AuditService(session))
        request = PaperOrderRequest(
            proposal_id=proposal_id,
            approval_id=approval_id,
            symbol="BTCUSDT",
            side="buy",
            type="market",
            size=Decimal("0.005"),
            idempotency_key="idem-key-001",
        )
        with pytest.raises(TradingPolicyError) as first_exc:
            execution.place_paper_order(request)
        assert first_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        with pytest.raises(TradingPolicyError) as second_exc:
            execution.place_paper_order(request)
        assert second_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        session.commit()
        assert session.query(Order).count() == 0


def test_paper_execution_blocked_without_approval(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = workflow_db
    with factory() as session:
        audit = AuditService(session)
        proposals = ProposalService(session, audit)
        approvals = ApprovalService(session, audit)
        execution = ExecutionService(session, settings, audit)
        proposal = proposals.create(
            TradeProposalCreate(
                organization_id=ORG_ID,
                user_id=USER_ID,
                strategy_id=StrategyId.HTF_TREND_PULLBACK,
                symbol="BTCUSDT",
                timeframe="4h",
                direction="long",
                entry_price=Decimal("60000"),
                position_size=Decimal("0.005"),
                leverage=Decimal("3"),
                exit=_exit(),
                confidence=0.7,
                risk_level=RiskSeverity.MEDIUM,
                rationale="exec block test",
                approval_required=True,
            )
        )
        approval = approvals.create_for_proposal(
            proposal_id=proposal.id,  # type: ignore[arg-type]
            organization_id=ORG_ID,
            user_id=USER_ID,
            risk_level=proposal.risk_level,
            confidence=float(proposal.confidence),
        )
        with pytest.raises(TradingPolicyError) as exc:
            execution.place_paper_order(
                PaperOrderRequest(
                    proposal_id=proposal.id,  # type: ignore[arg-type]
                    approval_id=approval.id,
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size=Decimal("0.005"),
                    idempotency_key="idem-key-002",
                )
            )
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON


def test_paper_execution_blocked_by_risk(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = workflow_db
    with factory() as session:
        audit = AuditService(session)
        proposals = ProposalService(session, audit)
        approvals = ApprovalService(session, audit)
        execution = ExecutionService(session, settings, audit)
        blocked_risk = RiskCheckResult(
            action=RiskAction.BLOCK,
            severity=RiskSeverity.HIGH,
            triggered_rules=[
                TriggeredRule(
                    rule_id=RiskRuleId.NO_STOP_LOSS,
                    action=RiskAction.BLOCK,
                    severity=RiskSeverity.HIGH,
                    message="Stop loss required",
                )
            ],
            explanation="Blocked by risk engine",
            approval_required=False,
        )
        proposal = proposals.create(
            TradeProposalCreate(
                organization_id=ORG_ID,
                user_id=USER_ID,
                strategy_id=StrategyId.HTF_TREND_PULLBACK,
                symbol="BTCUSDT",
                timeframe="4h",
                direction="long",
                entry_price=Decimal("60000"),
                position_size=Decimal("0.005"),
                leverage=Decimal("3"),
                exit=_exit(),
                confidence=0.7,
                risk_level=RiskSeverity.HIGH,
                rationale="risk block",
                approval_required=True,
                risk_result=blocked_risk,
            )
        )
        approval = approvals.create_for_proposal(
            proposal_id=proposal.id,  # type: ignore[arg-type]
            organization_id=ORG_ID,
            user_id=USER_ID,
            risk_level=proposal.risk_level,
            confidence=float(proposal.confidence),
        )
        approvals.decide(approval.id, ApprovalDecisionRequest(action=ApprovalAction.APPROVE))
        with pytest.raises(TradingPolicyError) as exc:
            execution.place_paper_order(
                PaperOrderRequest(
                    proposal_id=proposal.id,  # type: ignore[arg-type]
                    approval_id=approval.id,
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size=Decimal("0.005"),
                    idempotency_key="idem-key-003",
                )
            )
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON


def test_position_list_and_close(workflow_db: tuple[sessionmaker[Session], Settings]) -> None:
    factory, settings = workflow_db
    with factory() as session:
        proposal_id, _approval_id = _seed_approved_proposal(session, settings)
        from datetime import UTC, datetime

        session.add(
            Position(
                organization_id=ORG_ID,
                user_id=USER_ID,
                strategy_id=StrategyId.HTF_TREND_PULLBACK,
                linked_proposal_id=proposal_id,
                symbol="BTCUSDT",
                direction=TradeDirection.LONG,
                size=Decimal("0.005"),
                entry_price=Decimal("60000"),
                leverage=Decimal("3"),
                stop_loss=Decimal("58000"),
                take_profits=[],
                status=PositionStatus.OPEN,
                opened_at=datetime.now(UTC),
            )
        )
        session.commit()

    client, _app = _authed_test_client(factory, settings)
    with client:
        listed = client.get("/positions")
        assert listed.status_code == 200
        assert listed.json()["total"] >= 1
        position_id = listed.json()["items"][0]["id"]

        updated = client.patch(
            f"/positions/{position_id}",
            json={"stop_loss": "59000"},
        )
        assert updated.status_code == 200

        closed = client.post(
            f"/positions/{position_id}/close-paper",
            json={"exit_price": "61000", "reason": "target hit"},
        )
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"


def test_journal_crud(client_with_workflow_db: TestClient) -> None:
    client = client_with_workflow_db
    created = client.post(
        "/journal/entries",
        json={
            "organization_id": str(ORG_ID),
            "user_id": str(USER_ID),
            "symbol": "BTCUSDT",
            "timeframe": "4h",
            "direction": "long",
            "entry_rationale": "Pullback into support.",
            "lessons": "Wait for confirmation.",
            "tags": ["pullback"],
        },
    )
    assert created.status_code == 200
    entry_id = created.json()["id"]

    listed = client.get("/journal/entries")
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1

    fetched = client.get(f"/journal/entries/{entry_id}")
    assert fetched.status_code == 200

    updated = client.patch(
        f"/journal/entries/{entry_id}",
        json={"lessons": "Patience paid off."},
    )
    assert updated.status_code == 200

    deleted = client.delete(f"/journal/entries/{entry_id}")
    assert deleted.status_code == 204


def test_agent_persists_proposal_and_approval(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = workflow_db
    with factory() as session:
        service = build_agent_service(settings=settings, session=session)
        response = service.run(
            "Plan trade BTC pullback [test_low_confidence]",
            AgentInvokeContext(
                request_id="wf-agent-001",
                user_id=USER_ID,
                organization_id=ORG_ID,
            ),
            symbol="BTCUSDT",
            timeframe="4h",
        )
        assert response.proposal_id
        assert response.approval_id
        assert response.approval_status == "pending"


def test_audit_events_emitted(workflow_db: tuple[sessionmaker[Session], Settings]) -> None:
    factory, _settings = workflow_db
    with factory() as session:
        audit = AuditService(session)
        ProposalService(session, audit).create(
            TradeProposalCreate(
                organization_id=ORG_ID,
                user_id=USER_ID,
                strategy_id=StrategyId.HTF_TREND_PULLBACK,
                symbol="BTCUSDT",
                timeframe="4h",
                direction="long",
                entry_price=Decimal("60000"),
                position_size=Decimal("0.005"),
                leverage=Decimal("3"),
                exit=_exit(),
                confidence=0.7,
                risk_level=RiskSeverity.MEDIUM,
                rationale="audit test",
            )
        )
        session.commit()
        _records, total = audit.list_records(organization_id=ORG_ID)
        assert total >= 1


def test_real_trading_remains_disabled(workflow_db: tuple[sessionmaker[Session], Settings]) -> None:
    _factory, settings = workflow_db
    assert settings.real_trading_enabled is False
    service = build_agent_service(settings=settings)
    assert service.runtime.real_trading_allowed is False


# --- Slice 61: BloFin demo paper execution --------------------------------


class _FakeDemoExecution:
    """In-memory stand-in for the BloFin demo execution provider."""

    name = "fake-demo-execution"

    def __init__(
        self,
        *,
        fail: bool = False,
        fail_with: Exception | None = None,
    ) -> None:
        self.fail = fail
        self.fail_with = fail_with
        self.calls: list[ExchangeOrderRequest] = []

    def place_order(self, request: ExchangeOrderRequest) -> ExchangeOrderResult:
        self.calls.append(request)
        if self.fail_with is not None:
            raise self.fail_with
        if self.fail:
            raise RuntimeError("demo venue unavailable")
        return ExchangeOrderResult(
            exchange_order_id="demo-abc",
            client_order_id=request.client_order_id,
            status="filled",
            filled_size=request.size,
            average_price=Decimal("60000"),
            fills=(
                ExchangeFillDTO(
                    fill_id="t1",
                    order_id="demo-abc",
                    price=Decimal("60000"),
                    size=request.size,
                    fee=Decimal("0.01"),
                    fee_currency="USDT",
                ),
            ),
        )

    def cancel_order(self, *, inst_id: str, exchange_order_id: str) -> None: ...

    def get_order(self, *, inst_id: str, exchange_order_id: str) -> ExchangeOrderResult:
        raise NotImplementedError


def _demo_settings() -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        exchange_mode="paper_exchange_demo",
        blofin_demo_enabled=True,
        blofin_api_key="demo-key",
        blofin_api_secret="demo-secret",
        blofin_api_passphrase="demo-pass",
        blofin_demo_rest_base_url="https://demo-trading-openapi.blofin.com",
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="workflow-test-secret",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
    )


def test_demo_execution_mirrors_paper_order(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, base_settings = workflow_db
    settings = _demo_settings()
    assert settings.exchange_demo_active is True
    assert settings.real_trading_enabled is False

    with factory() as session:
        proposal_id, approval_id = _seed_approved_proposal(session, base_settings)
        fake = _FakeDemoExecution()
        execution = ExecutionService(
            session,
            settings,
            AuditService(session),
            exchange_execution=fake,
        )
        with pytest.raises(TradingPolicyError) as exc:
            execution.place_paper_order(
                PaperOrderRequest(
                    proposal_id=proposal_id,
                    approval_id=approval_id,
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size=Decimal("0.005"),
                    idempotency_key="demo-idem-001",
                )
            )
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        assert fake.calls == []
        assert session.query(Order).count() == 0
        assert session.query(ExchangeOrder).count() == 0
        assert session.query(ExchangeFill).count() == 0


def test_demo_execution_failure_does_not_break_paper_order(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, base_settings = workflow_db
    settings = _demo_settings()

    with factory() as session:
        proposal_id, approval_id = _seed_approved_proposal(session, base_settings)
        fake = _FakeDemoExecution(fail=True)
        execution = ExecutionService(
            session,
            settings,
            AuditService(session),
            exchange_execution=fake,
        )
        with pytest.raises(TradingPolicyError) as exc:
            execution.place_paper_order(
                PaperOrderRequest(
                    proposal_id=proposal_id,
                    approval_id=approval_id,
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size=Decimal("0.005"),
                    idempotency_key="demo-idem-002",
                )
            )
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        assert fake.calls == []
        assert session.query(Order).count() == 0
        assert session.query(ExchangeOrder).count() == 0


def test_demo_mirror_failure_persists_sanitized_audit_metadata(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, base_settings = workflow_db
    settings = _demo_settings()
    venue_error = ExchangeRequestError(
        "BloFin error 51008: Order price is out of the allowable range",
        details=VenueErrorDetails(
            venue_error_code="51008",
            venue_error_message="Order price is out of the allowable range",
            http_status=200,
            endpoint_name="POST /api/v1/trade/order",
        ),
    )

    with factory() as session:
        proposal_id, approval_id = _seed_approved_proposal(session, base_settings)
        fake = _FakeDemoExecution(fail_with=venue_error)
        execution = ExecutionService(
            session,
            settings,
            AuditService(session),
            exchange_execution=fake,
        )
        with pytest.raises(TradingPolicyError) as exc:
            execution.place_paper_order(
                PaperOrderRequest(
                    proposal_id=proposal_id,
                    approval_id=approval_id,
                    symbol="BTCUSDT",
                    side="buy",
                    type="limit",
                    size=Decimal("0.005"),
                    price=Decimal("60000"),
                    idempotency_key="slice66b-demo-limit-001",
                )
            )
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        assert fake.calls == []
        assert (
            session.query(AuditLog)
            .filter(AuditLog.action == AuditEventType.EXCHANGE_DEMO_ORDER_FAILED)
            .count()
            == 0
        )
        assert session.query(ExchangeOrder).count() == 0


def test_demo_routing_skipped_when_real_trading_would_be_enabled(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    """Defense-in-depth: even if a provider is wired, paper_internal never mirrors."""
    factory, settings = workflow_db  # paper_internal settings
    with factory() as session:
        proposal_id, approval_id = _seed_approved_proposal(session, settings)
        fake = _FakeDemoExecution()
        execution = ExecutionService(
            session,
            settings,
            AuditService(session),
            exchange_execution=fake,
        )
        with pytest.raises(TradingPolicyError) as exc:
            execution.place_paper_order(
                PaperOrderRequest(
                    proposal_id=proposal_id,
                    approval_id=approval_id,
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size=Decimal("0.005"),
                    idempotency_key="demo-idem-003",
                )
            )
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        assert fake.calls == []
        assert session.query(ExchangeOrder).all() == []
        assert session.query(Order).count() == 0


def test_demo_mirror_idempotency_one_exchange_order(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, _base_settings = workflow_db
    settings = _demo_settings()
    allowed_risk = RiskCheckResult(
        action=RiskAction.ALLOW,
        severity=RiskSeverity.LOW,
        explanation="ok",
        approval_required=True,
    )

    with factory() as session:
        audit = AuditService(session)
        proposals = ProposalService(session, audit)
        approvals = ApprovalService(session, audit)
        proposal = proposals.create(
            TradeProposalCreate(
                organization_id=ORG_ID,
                user_id=USER_ID,
                strategy_id=StrategyId.HTF_TREND_PULLBACK,
                symbol="BTCUSDT",
                timeframe="4h",
                direction="long",
                entry_price=Decimal("60000"),
                position_size=Decimal("0.005"),
                leverage=Decimal("3"),
                exit=_exit(),
                confidence=0.7,
                risk_level=RiskSeverity.MEDIUM,
                rationale="idem test",
                approval_required=True,
                risk_result=allowed_risk,
            )
        )
        approval = approvals.create_for_proposal(
            proposal_id=proposal.id,  # type: ignore[arg-type]
            organization_id=ORG_ID,
            user_id=USER_ID,
            risk_level=proposal.risk_level,
            confidence=float(proposal.confidence),
        )
        approvals.decide(approval.id, ApprovalDecisionRequest(action=ApprovalAction.APPROVE))
        proposal_id, approval_id = proposal.id, approval.id  # type: ignore[assignment]

        fake = _FakeDemoExecution()
        execution = ExecutionService(
            session,
            settings,
            audit,
            exchange_execution=fake,
        )
        request = PaperOrderRequest(
            proposal_id=proposal_id,
            approval_id=approval_id,
            symbol="BTCUSDT",
            side="buy",
            type="market",
            size=Decimal("0.005"),
            idempotency_key="demo-idem-dup-001",
        )
        with pytest.raises(TradingPolicyError) as first_exc:
            execution.place_paper_order(request)
        assert first_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        with pytest.raises(TradingPolicyError) as second_exc:
            execution.place_paper_order(request)
        assert second_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        assert fake.calls == []
        assert session.query(Order).count() == 0
        assert session.query(ExchangeOrder).count() == 0


def test_demo_mirror_requires_risk_result(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, _base_settings = workflow_db
    settings = _demo_settings()
    fake = _FakeDemoExecution()

    with factory() as session:
        audit = AuditService(session)
        proposals = ProposalService(session, audit)
        approvals = ApprovalService(session, audit)
        proposal = proposals.create(
            TradeProposalCreate(
                organization_id=ORG_ID,
                user_id=USER_ID,
                strategy_id=StrategyId.HTF_TREND_PULLBACK,
                symbol="BTCUSDT",
                timeframe="4h",
                direction="long",
                entry_price=Decimal("60000"),
                position_size=Decimal("0.005"),
                leverage=Decimal("3"),
                exit=_exit(),
                confidence=0.7,
                risk_level=RiskSeverity.MEDIUM,
                rationale="no risk result",
                approval_required=True,
            )
        )
        approval = approvals.create_for_proposal(
            proposal_id=proposal.id,  # type: ignore[arg-type]
            organization_id=ORG_ID,
            user_id=USER_ID,
            risk_level=proposal.risk_level,
            confidence=float(proposal.confidence),
        )
        approvals.decide(approval.id, ApprovalDecisionRequest(action=ApprovalAction.APPROVE))
        proposal_id, approval_id = proposal.id, approval.id  # type: ignore[assignment]
        execution = ExecutionService(
            session,
            settings,
            audit,
            exchange_execution=fake,
        )
        with pytest.raises(TradingPolicyError) as exc:
            execution.place_paper_order(
                PaperOrderRequest(
                    proposal_id=proposal_id,
                    approval_id=approval_id,
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size=Decimal("0.005"),
                    idempotency_key="demo-no-risk-001",
                )
            )
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        assert fake.calls == []


COLLEAGUE_ID = uuid.UUID("00000000-0000-0000-0000-000000000032")


def _add_colleague_and_login(
    client: TestClient, factory: sessionmaker[Session], settings: Settings
) -> dict[str, str]:
    with factory() as session:
        session.add(
            User(
                id=COLLEAGUE_ID,
                email="colleague@test.example",
                hashed_password=hash_password("TestPassword123!", settings),
            )
        )
        session.flush()
        session.add(
            Membership(user_id=COLLEAGUE_ID, organization_id=ORG_ID, role=MembershipRole.TRADER)
        )
        session.commit()
    login = client.post(
        "/auth/login",
        json={"email": "colleague@test.example", "password": "TestPassword123!"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}


def test_trader_cannot_act_on_colleagues_position_or_legacy_approval(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    """M15: positions and legacy approvals are per-user inside an org (404 for colleagues)."""
    from datetime import UTC, datetime

    from app.db.models import ApprovalRequest as ApprovalRow
    from app.db.models import Position as PositionRow
    from app.schemas.common import ApprovalStatus, PositionStatus, TradeDirection

    factory, settings = workflow_db
    with factory() as session:
        audit = AuditService(session)
        proposal = ProposalService(session, audit).create(
            TradeProposalCreate(
                organization_id=ORG_ID,
                user_id=USER_ID,
                strategy_id=StrategyId.HTF_TREND_PULLBACK,
                symbol="BTCUSDT",
                timeframe="4h",
                direction="long",
                entry_price=Decimal("60000"),
                position_size=Decimal("0.005"),
                leverage=Decimal("3"),
                exit=_exit(),
                confidence=0.7,
                risk_level=RiskSeverity.MEDIUM,
                rationale="M15 scoping test",
                approval_required=True,
            )
        )
        approval = ApprovalService(session, audit).create_for_proposal(
            proposal_id=proposal.id,  # type: ignore[arg-type]
            organization_id=ORG_ID,
            user_id=USER_ID,
            risk_level=proposal.risk_level,
            confidence=float(proposal.confidence),
        )
        position = PositionRow(
            organization_id=ORG_ID,
            user_id=USER_ID,
            symbol="BTCUSDT",
            direction=TradeDirection.LONG,
            size=Decimal("0.01"),
            entry_price=Decimal("60000"),
            leverage=Decimal("1"),
            status=PositionStatus.OPEN,
            opened_at=datetime.now(UTC),
        )
        session.add(position)
        session.commit()
        approval_id, position_id = approval.id, position.id

    app = create_app(settings=settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        colleague = _add_colleague_and_login(client, factory, settings)
        assert client.get(f"/positions/{position_id}", headers=colleague).status_code == 404
        assert (
            client.patch(
                f"/positions/{position_id}", headers=colleague, json={"stop_loss": "1"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/positions/{position_id}/close-paper",
                headers=colleague,
                json={"exit_price": "61000"},
            ).status_code
            == 404
        )
        for action in ("approve", "reject", "needs-more-analysis"):
            response = client.post(
                f"/approvals/{approval_id}/{action}", headers=colleague, json={"reason": "x"}
            )
            assert response.status_code == 404, (action, response.text)
        assert (
            client.get(f"/approvals/{approval_id}/workflow", headers=colleague).status_code == 404
        )

    with factory() as session:
        row = session.get(PositionRow, position_id)
        assert row is not None and row.status is PositionStatus.OPEN
        assert session.get(ApprovalRow, approval_id).status is ApprovalStatus.PENDING

    # Service-level guard: a legacy approval decided with another principal is not found.
    with factory() as session, pytest.raises(NotFoundError):
        ApprovalService(session, AuditService(session)).decide(
            approval_id,
            ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
            principal_organization_id=ORG_ID,
            principal_user_id=COLLEAGUE_ID,
        )
