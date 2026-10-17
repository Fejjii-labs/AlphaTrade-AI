"""Slice 20 — MVP end-to-end workflow and journal RAG sync tests."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import TradingPolicyError
from app.providers.qdrant import reset_process_vector_store
from app.schemas.approval import ApprovalDecisionRequest
from app.schemas.common import (
    ApprovalAction,
    AuditEventType,
    DocumentSourceType,
    RiskAction,
    RiskRuleId,
    RiskSeverity,
    StrategyId,
)
from app.schemas.execution import PaperOrderRequest
from app.schemas.journal import JournalEntryCreate
from app.schemas.proposal import TradeProposalCreate
from app.schemas.rag import RagQuery
from app.schemas.risk import RiskCheckResult, TriggeredRule
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService
from app.services.execution_service import LEGACY_PAPER_EXECUTION_REASON, ExecutionService
from app.services.journal_rag_sync_service import JournalRagSyncService, sanitize_journal_text
from app.services.journal_service import JournalService
from app.services.proposal_service import ProposalService
from app.services.rag_service import build_rag_service
from tests.test_workflows import ORG_ID, USER_ID, _exit


def _default_allow_risk() -> RiskCheckResult:
    return RiskCheckResult(
        action=RiskAction.ALLOW,
        severity=RiskSeverity.LOW,
        explanation="mvp allow",
        approval_required=True,
    )


def _create_proposal_with_approval(
    session: Session,
    *,
    risk_result: RiskCheckResult | None = None,
    approve: bool = False,
) -> tuple[uuid.UUID, uuid.UUID]:
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
            rationale="MVP workflow test",
            approval_required=True,
            risk_result=_default_allow_risk() if risk_result is None else risk_result,
        )
    )
    approval = approvals.create_for_proposal(
        proposal_id=proposal.id,  # type: ignore[arg-type]
        organization_id=ORG_ID,
        user_id=USER_ID,
        risk_level=proposal.risk_level,
        confidence=float(proposal.confidence),
    )
    if approve:
        approvals.decide(approval.id, ApprovalDecisionRequest(action=ApprovalAction.APPROVE))
    session.commit()
    return proposal.id, approval.id  # type: ignore[return-value]


def test_full_proposal_approval_paper_flow(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = workflow_db
    with factory() as session:
        proposal_id, approval_id = _create_proposal_with_approval(session, approve=True)
        execution = ExecutionService(session, settings, AuditService(session))
        with pytest.raises(TradingPolicyError) as exc:
            execution.place_paper_order(
                PaperOrderRequest(
                    proposal_id=proposal_id,
                    approval_id=approval_id,
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size=Decimal("0.005"),
                    idempotency_key="mvp-flow-001",
                )
            )
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        session.commit()

    client, _ = _authed_client(factory, settings)
    with client:
        workflow = client.get(f"/proposals/{proposal_id}/workflow")
        assert workflow.status_code == 200
        body = workflow.json()
        assert body["can_execute_paper"] is True
        assert body["approval"]["status"] == "approved"

        audit = client.get("/audit/events", params={"limit": 20})
        assert audit.status_code == 200
        event_types = {item["event_type"] for item in audit.json()["items"]}
        assert AuditEventType.PAPER_ORDER_CREATED.value not in event_types


def test_rejected_approval_cannot_execute(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = workflow_db
    with factory() as session:
        proposal_id, approval_id = _create_proposal_with_approval(session)
        ApprovalService(session, AuditService(session)).decide(
            approval_id,
            ApprovalDecisionRequest(action=ApprovalAction.REJECT, reason="no"),
        )
        session.commit()

    with factory() as session:
        execution = ExecutionService(session, settings, AuditService(session))
        with pytest.raises(TradingPolicyError) as exc:
            execution.place_paper_order(
                PaperOrderRequest(
                    proposal_id=proposal_id,
                    approval_id=approval_id,
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size=Decimal("0.005"),
                    idempotency_key="mvp-reject-001",
                )
            )
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON


def test_needs_more_analysis_cannot_execute(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = workflow_db
    with factory() as session:
        proposal_id, approval_id = _create_proposal_with_approval(session)
        ApprovalService(session, AuditService(session)).decide(
            approval_id,
            ApprovalDecisionRequest(action=ApprovalAction.NEEDS_MORE_ANALYSIS, reason="charts"),
        )
        session.commit()

    with factory() as session:
        execution = ExecutionService(session, settings, AuditService(session))
        with pytest.raises(TradingPolicyError) as exc:
            execution.place_paper_order(
                PaperOrderRequest(
                    proposal_id=proposal_id,
                    approval_id=approval_id,
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size=Decimal("0.005"),
                    idempotency_key="mvp-nma-001",
                )
            )
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON


def test_risk_blocked_proposal_cannot_execute(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = workflow_db
    blocked = RiskCheckResult(
        action=RiskAction.BLOCK,
        severity=RiskSeverity.HIGH,
        triggered_rules=[
            TriggeredRule(
                rule_id=RiskRuleId.NO_STOP_LOSS,
                action=RiskAction.BLOCK,
                severity=RiskSeverity.HIGH,
                message="Stop required",
            )
        ],
        explanation="Blocked",
        approval_required=False,
    )
    with factory() as session:
        proposal_id, approval_id = _create_proposal_with_approval(
            session,
            risk_result=blocked,
            approve=True,
        )

    with factory() as session:
        execution = ExecutionService(session, settings, AuditService(session))
        with pytest.raises(TradingPolicyError) as exc:
            execution.place_paper_order(
                PaperOrderRequest(
                    proposal_id=proposal_id,
                    approval_id=approval_id,
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size=Decimal("0.005"),
                    idempotency_key="mvp-risk-001",
                )
            )
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON


def test_modified_approval_preserves_audit(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = workflow_db
    with factory() as session:
        _proposal_id, approval_id = _create_proposal_with_approval(session)
        ApprovalService(session, AuditService(session)).decide(
            approval_id,
            ApprovalDecisionRequest(
                action=ApprovalAction.MODIFY,
                reason="reduce size",
                modified_fields={"position_size": "0.005"},
            ),
        )
        session.commit()

    client, _ = _authed_client(factory, settings)
    with client:
        resp = client.get(f"/approvals/{approval_id}/workflow")
        assert resp.status_code == 200
        data = resp.json()
        assert data["approval"]["status"] == "modified"
        assert data["approval"]["modified_fields"]["position_size"] == "0.005"
        assert data["can_execute_paper"] is False

        audit = client.get("/audit/events", params={"limit": 50})
        decisions = [
            e
            for e in audit.json()["items"]
            if e["event_type"] == AuditEventType.APPROVAL_DECISION.value
        ]
        assert len(decisions) >= 1
        assert any(
            (e.get("redacted_metadata") or {}).get("modified_fields", {}).get("position_size")
            == "0.005"
            for e in decisions
        )


def test_journal_auto_ingest_creates_searchable_content(
    workflow_db: tuple[sessionmaker[Session], Settings],
) -> None:
    reset_process_vector_store()
    factory, settings = workflow_db
    unique_lesson = f"Unique journal lesson {uuid.uuid4().hex[:8]}"
    with factory() as session:
        audit = AuditService(session)
        rag = build_rag_service(settings, session, audit_service=audit)
        journal = JournalService(
            session,
            audit,
            rag_sync=JournalRagSyncService(rag, settings),
        )
        entry = journal.create(
            JournalEntryCreate(
                organization_id=ORG_ID,
                user_id=USER_ID,
                symbol="ETHUSDT",
                timeframe="1h",
                direction="long",
                entry_rationale="Pullback entry",
                lessons=unique_lesson,
                emotions=["calm"],
                mistakes=["early entry"],
                tags=["demo"],
            )
        )
        session.commit()

        from tests.support.knowledge_indexing import drain_indexing

        assert drain_indexing(rag) == ["ready"]
        results = rag.search(
            RagQuery(
                query=unique_lesson,
                organization_id=ORG_ID,
                user_id=USER_ID,
                top_k=5,
                source_types=[DocumentSourceType.TRADE_JOURNAL],
            )
        )
        assert any(unique_lesson in chunk.content for chunk in results.chunks)
        assert entry.id is not None


def test_journal_sanitize_strips_secrets() -> None:
    raw = "Notes api_key=supersecret123 and bearer abc.def.ghi"
    cleaned = sanitize_journal_text(raw)
    assert "supersecret123" not in cleaned
    assert "[REDACTED]" in cleaned


def test_real_trading_still_impossible(workflow_db: tuple[sessionmaker[Session], Settings]) -> None:
    _factory, settings = workflow_db
    assert settings.real_trading_enabled is False


def _authed_client(
    factory: sessionmaker[Session],
    settings: Settings,
) -> tuple[TestClient, object]:
    from collections.abc import Iterator

    from app.db.session import get_session
    from app.main import create_app

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
