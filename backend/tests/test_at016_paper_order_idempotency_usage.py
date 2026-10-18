"""AT-016 follow-up — idempotent paper-order replay must not double-count usage or audits."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.errors import TradingPolicyError
from app.db.base import Base
from app.db.models import (
    AuditLog,
    Membership,
    Order,
    Organization,
    UsageEvent,
    User,
    UserRiskSettings,
)
from app.db.session import get_session
from app.main import create_app
from app.schemas.approval import ApprovalDecisionRequest
from app.schemas.common import (
    ApprovalAction,
    AuditEventType,
    MembershipRole,
    RiskAction,
    RiskSeverity,
    StrategyId,
)
from app.schemas.execution import PaperOrderRequest
from app.schemas.proposal import ExitCriteria, TakeProfitLevel, TradeProposalCreate
from app.schemas.risk import RiskCheckResult
from app.schemas.usage import UsageEventCreate
from app.security.passwords import hash_password
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService
from app.services.execution_service import LEGACY_PAPER_EXECUTION_REASON, ExecutionService
from app.services.proposal_service import ProposalService
from app.services.usage_service import UsageService

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000016")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000017")
_IDEM_KEY = "at016-idem-usage-001"
_OTHER_KEY = "at016-idem-usage-002"


@pytest.fixture
def idem_db() -> Iterator[tuple[sessionmaker[Session], Settings]]:
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
        exchange_mode="paper_internal",
        provider_mode="mock",
        market_data_provider="mock",
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="at016-idem-usage-secret-32-bytes-min",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        metrics_enabled=False,
    )
    with factory() as session:
        session.add(Organization(id=ORG_ID, name="AT016 Idem Org"))
        session.add(
            User(
                id=USER_ID,
                email="idem@test.example",
                hashed_password=hash_password("TestPassword123!", settings),
            )
        )
        session.flush()
        session.add(
            Membership(
                user_id=USER_ID,
                organization_id=ORG_ID,
                role=MembershipRole.OWNER,
            )
        )
        session.add(
            UserRiskSettings(
                organization_id=ORG_ID,
                user_id=USER_ID,
                default_account_balance=Decimal("100000"),
                max_risk_per_trade_percent=Decimal("5"),
            )
        )
        session.commit()
    yield factory, settings
    engine.dispose()


@pytest.fixture
def idem_client(idem_db: tuple[sessionmaker[Session], Settings]) -> Iterator[TestClient]:
    factory, settings = idem_db

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as client:
        login = client.post(
            "/auth/login",
            json={"email": "idem@test.example", "password": "TestPassword123!"},
        )
        assert login.status_code == 200
        token = login.json()["tokens"]["access_token"]
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


def _exit() -> ExitCriteria:
    return ExitCriteria(
        invalidation="Close below stop.",
        stop_loss=Decimal("58000"),
        take_profits=[TakeProfitLevel(price=Decimal("62000"), size_fraction=0.5)],
    )


def _seed_approved(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
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
            rationale="idem seed",
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


def _paper_payload(proposal_id: uuid.UUID, approval_id: uuid.UUID, *, key: str) -> dict[str, str]:
    return {
        "proposal_id": str(proposal_id),
        "approval_id": str(approval_id),
        "symbol": "BTCUSDT",
        "side": "buy",
        "type": "market",
        "size": "0.005",
        "idempotency_key": key,
    }


def _usage_count(session: Session, *, feature: str = "paper_execution") -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(UsageEvent)
            .where(
                UsageEvent.organization_id == ORG_ID,
                UsageEvent.feature == feature,
            )
        )
        or 0
    )


def _creation_audit_count(session: Session, *, request_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.request_id == request_id,
                AuditLog.action == AuditEventType.PAPER_ORDER_CREATED,
            )
        )
        or 0
    )


def _seed_pending_approval(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
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
            rationale="idem pending seed",
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
    session.commit()
    return proposal.id, approval.id  # type: ignore[return-value]


def _order_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Order)) or 0)


def test_service_contract_created_new_vs_replay(
    idem_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = idem_db
    with factory() as session:
        pid, aid = _seed_approved(session)
        svc = ExecutionService(session, settings, AuditService(session))
        req = PaperOrderRequest(
            proposal_id=pid,
            approval_id=aid,
            symbol="BTCUSDT",
            side="buy",
            type="market",
            size=Decimal("0.005"),
            idempotency_key=_IDEM_KEY,
        )
        with pytest.raises(TradingPolicyError) as first_exc:
            svc.place_paper_order(req)
        assert first_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        with pytest.raises(TradingPolicyError) as second_exc:
            svc.place_paper_order(req)
        assert second_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        assert _order_count(session) == 0


def test_first_request_creates_one_order_and_one_usage_row(
    idem_client: TestClient,
    idem_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, _settings = idem_db
    with factory() as session:
        pid, aid = _seed_approved(session)

    response = idem_client.post("/execution/paper", json=_paper_payload(pid, aid, key=_IDEM_KEY))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "trading_policy_violation"

    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0


def test_identical_replay_returns_same_order_id(
    idem_client: TestClient,
    idem_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, _settings = idem_db
    with factory() as session:
        pid, aid = _seed_approved(session)

    first = idem_client.post("/execution/paper", json=_paper_payload(pid, aid, key=_IDEM_KEY))
    replay = idem_client.post("/execution/paper", json=_paper_payload(pid, aid, key=_IDEM_KEY))
    assert first.status_code == 403
    assert replay.status_code == 403
    assert first.json()["error"]["code"] == "trading_policy_violation"
    assert replay.json()["error"]["code"] == "trading_policy_violation"


def test_replay_leaves_usage_count_unchanged(
    idem_client: TestClient,
    idem_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, _settings = idem_db
    with factory() as session:
        pid, aid = _seed_approved(session)

    payload = _paper_payload(pid, aid, key=_IDEM_KEY)
    assert idem_client.post("/execution/paper", json=payload).status_code == 403
    with factory() as session:
        after_first = _usage_count(session)

    assert idem_client.post("/execution/paper", json=payload).status_code == 403
    with factory() as session:
        assert _usage_count(session) == after_first == 0


def test_replay_does_not_duplicate_creation_audits(
    idem_client: TestClient,
    idem_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, _settings = idem_db
    with factory() as session:
        pid, aid = _seed_approved(session)

    payload = _paper_payload(pid, aid, key=_IDEM_KEY)
    assert idem_client.post("/execution/paper", json=payload).status_code == 403
    assert idem_client.post("/execution/paper", json=payload).status_code == 403

    with factory() as session:
        assert _creation_audit_count(session, request_id=_IDEM_KEY) == 0
        assert _order_count(session) == 0


def test_changed_idempotency_key_creates_new_order_and_usage(
    idem_client: TestClient,
    idem_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, _settings = idem_db
    with factory() as session:
        pid1, aid1 = _seed_approved(session)
        pid2, aid2 = _seed_approved(session)

    first_payload = _paper_payload(pid1, aid1, key=_IDEM_KEY)
    assert idem_client.post("/execution/paper", json=first_payload).status_code == 403
    second = idem_client.post("/execution/paper", json=_paper_payload(pid2, aid2, key=_OTHER_KEY))
    assert second.status_code == 403

    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0
        assert _creation_audit_count(session, request_id=_IDEM_KEY) == 0
        assert _creation_audit_count(session, request_id=_OTHER_KEY) == 0


def test_failed_request_does_not_record_usage(
    idem_client: TestClient,
    idem_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, _settings = idem_db
    with factory() as session:
        pid, aid = _seed_pending_approval(session)

    fail_key = "at016-idem-fail-001"
    response = idem_client.post("/execution/paper", json=_paper_payload(pid, aid, key=fail_key))
    assert response.status_code == 403

    with factory() as session:
        assert _usage_count(session) == 0
        assert _order_count(session) == 0
        usage = session.scalar(select(UsageEvent).where(UsageEvent.request_id == fail_key))
        assert usage is None


def test_concurrent_identical_requests_remain_safe(
    idem_db: tuple[sessionmaker[Session], Settings],
    tmp_path: object,
) -> None:
    """Concurrent first-writers converge server-side without client retry (AT-028)."""
    from pathlib import Path

    _factory, settings = idem_db
    db_path = Path(str(tmp_path)) / "at016-concurrent.sqlite"
    engine = create_engine(
        f"sqlite+pysqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    with factory() as session:
        session.add(Organization(id=ORG_ID, name="Concurrent Org"))
        session.add(
            User(
                id=USER_ID,
                email="concurrent@test.example",
                hashed_password=hash_password("TestPassword123!", settings),
            )
        )
        session.flush()
        session.add(Membership(user_id=USER_ID, organization_id=ORG_ID, role=MembershipRole.OWNER))
        session.add(
            UserRiskSettings(
                organization_id=ORG_ID,
                user_id=USER_ID,
                default_account_balance=Decimal("100000"),
                max_risk_per_trade_percent=Decimal("5"),
            )
        )
        pid, aid = _seed_approved(session)

    req = PaperOrderRequest(
        proposal_id=pid,
        approval_id=aid,
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size=Decimal("0.005"),
        idempotency_key="at016-concurrent-key-001",
    )
    results: list[uuid.UUID] = []
    errors: list[BaseException] = []
    created_new_flags: list[bool] = []
    lock = threading.Lock()
    start_barrier = threading.Barrier(4)

    def _route_place() -> None:
        start_barrier.wait(timeout=10)
        try:
            with factory() as session:
                svc = ExecutionService(session, settings, AuditService(session))
                usage = UsageService(session)
                placement = svc.place_paper_order(req)
                if placement.created_new:
                    usage.record(
                        UsageEventCreate(
                            request_id=req.idempotency_key,
                            organization_id=ORG_ID,
                            user_id=USER_ID,
                            feature="paper_execution",
                            provider="paper-engine",
                            input_tokens=0,
                            output_tokens=0,
                        )
                    )
                session.commit()
                with lock:
                    results.append(placement.order.id)
                    created_new_flags.append(placement.created_new)
        except BaseException as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_route_place) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors, errors
    assert all(
        isinstance(exc, TradingPolicyError)
        and exc.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        for exc in errors
    )
    assert results == []
    assert created_new_flags == []

    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0
        assert _creation_audit_count(session, request_id=req.idempotency_key) == 0

    engine.dispose()


def test_replay_does_not_flush_metered_side_effects_into_session(
    idem_db: tuple[sessionmaker[Session], Settings],
) -> None:
    """Replay must not add audit/usage rows to the caller session (AT-ADR-008 UoW)."""
    factory, settings = idem_db
    req = PaperOrderRequest(
        proposal_id=uuid.uuid4(),
        approval_id=uuid.uuid4(),
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size=Decimal("0.005"),
        idempotency_key="at016-session-replay-key",
    )

    with factory() as session:
        pid, aid = _seed_approved(session)
        req = req.model_copy(update={"proposal_id": pid, "approval_id": aid})
        svc = ExecutionService(session, settings, AuditService(session))
        with pytest.raises(TradingPolicyError) as first_exc:
            svc.place_paper_order(req)
        assert first_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        session.rollback()

    with factory() as session:
        pending = Organization(name="Unrelated Pending Org")
        session.add(pending)
        session.flush()
        pending_id = pending.id

        svc = ExecutionService(session, settings, AuditService(session))
        with pytest.raises(TradingPolicyError) as replay_exc:
            svc.place_paper_order(req)
        assert replay_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON

        session.rollback()

    with factory() as verify:
        assert verify.get(Organization, pending_id) is None
        assert _usage_count(verify) == 0
        assert _creation_audit_count(verify, request_id=req.idempotency_key) == 0


def test_rejected_service_path_raises_without_created_new(
    idem_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = idem_db
    with factory() as session:
        pid, aid = _seed_pending_approval(session)
        svc = ExecutionService(session, settings, AuditService(session))
        with pytest.raises(TradingPolicyError):
            svc.place_paper_order(
                PaperOrderRequest(
                    proposal_id=pid,
                    approval_id=aid,
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size=Decimal("0.005"),
                    idempotency_key="at016-reject-key",
                )
            )
