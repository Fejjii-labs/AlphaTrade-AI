"""AT-028 — server-side concurrent paper-order idempotency convergence."""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

import app.core.dependencies as dependencies
from app.core.config import ExchangeMode, Settings
from app.core.errors import TradingPolicyError
from app.db.base import Base
from app.db.models import (
    AuditLog,
    DailyRiskState,
    Membership,
    Order,
    Organization,
    OrganizationQuota,
    Position,
    TradeProposal,
    UsageEvent,
    User,
    UserRiskSettings,
)
from app.db.session import get_session
from app.main import create_app
from app.providers.exchange.base import (
    ExchangeOrderRequest,
    ExchangeOrderResult,
)
from app.repositories.quota import QuotaRepository
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
from app.services.quota_service import QuotaService
from app.services.usage_service import UsageService
from tests.support.postgres_persistence import reset_public_schema

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000028")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000029")
_IDEM_KEY = "at028-concurrent-key-001"
_OTHER_KEY = "at028-concurrent-key-002"
POSTGRES_URL = os.environ.get(
    "AT028_POSTGRES_URL",
    "postgresql+psycopg://alphatrade:alphatrade@localhost:5432/alphatrade_test",
)
_HTTP_PASSWORD = "AT028-Test-Password-123!"


class _TrackingSession(Session):
    """Count route-owned commits after test setup completes."""

    commit_count = 0
    commit_lock = threading.Lock()

    def commit(self) -> None:
        with self.commit_lock:
            _TrackingSession.commit_count += 1
        super().commit()


class _FakeDemoExecution:
    """Thread-safe demo provider proving winner-only mirror attempts."""

    name = "at028-fake-demo"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[str | None] = []

    def place_order(self, request: ExchangeOrderRequest) -> ExchangeOrderResult:
        with self._lock:
            self.calls.append(request.client_order_id)
        return ExchangeOrderResult(
            exchange_order_id="at028-demo-order",
            client_order_id=request.client_order_id,
            status="submitted",
            filled_size=Decimal("0"),
            average_price=None,
            position_mode="long_short_mode",
            position_side="long",
        )


def _postgres_available() -> bool:
    try:
        engine = create_engine(POSTGRES_URL, poolclass=NullPool)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason=f"PostgreSQL not reachable at {POSTGRES_URL}",
)


def _exit() -> ExitCriteria:
    return ExitCriteria(
        invalidation="Close below stop.",
        stop_loss=Decimal("58000"),
        take_profits=[TakeProfitLevel(price=Decimal("62000"), size_fraction=0.5)],
    )


def _settings(*, database_url: str) -> Settings:
    return Settings(
        environment="local",
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        exchange_mode="paper_internal",
        provider_mode="mock",
        market_data_provider="mock",
        database_url=database_url,
        jwt_secret="at028-concurrency-secret-32-bytes-min",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        metrics_enabled=False,
    )


def _seed_org_user(session: Session) -> None:
    session.add(Organization(id=ORG_ID, name="AT028 Org"))
    session.add(
        User(
            id=USER_ID,
            email="at028@test.example",
            hashed_password="not-used",
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
            rationale="at028 seed",
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


def _paper_request(
    proposal_id: uuid.UUID,
    approval_id: uuid.UUID,
    *,
    key: str = _IDEM_KEY,
) -> PaperOrderRequest:
    return PaperOrderRequest(
        proposal_id=proposal_id,
        approval_id=approval_id,
        symbol="BTCUSDT",
        side="buy",
        type="market",
        size=Decimal("0.005"),
        idempotency_key=key,
    )


def _route_place(
    factory: sessionmaker[Session],
    settings: Settings,
    req: PaperOrderRequest,
) -> tuple[uuid.UUID, bool]:
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
        return placement.order.id, placement.created_new


def _order_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Order)) or 0)


def _usage_count(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(UsageEvent)
            .where(
                UsageEvent.organization_id == ORG_ID,
                UsageEvent.feature == "paper_execution",
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


def _position_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Position)) or 0)


def _daily_risk_trade_count(session: Session) -> int:
    row = session.scalar(
        select(DailyRiskState).where(
            DailyRiskState.organization_id == ORG_ID,
            DailyRiskState.user_id == USER_ID,
        )
    )
    return int(row.trade_count if row is not None else 0)


@pytest.fixture
def sqlite_file_db(tmp_path: Path) -> Iterator[tuple[sessionmaker[Session], Settings]]:
    db_path = tmp_path / "at028-concurrent.sqlite"
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
    settings = _settings(database_url=f"sqlite+pysqlite:///{db_path}")
    with factory() as session:
        _seed_org_user(session)
        session.commit()
    yield factory, settings
    engine.dispose()


@pytest.fixture
def postgres_db() -> Iterator[tuple[sessionmaker[Session], Settings]]:
    engine = create_engine(POSTGRES_URL, poolclass=NullPool)
    reset_public_schema(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = _settings(database_url=POSTGRES_URL)
    with factory() as session:
        _seed_org_user(session)
        session.commit()
    yield factory, settings
    reset_public_schema(engine)
    engine.dispose()


@pytest.fixture
def postgres_http_db(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[
    tuple[
        sessionmaker[_TrackingSession],
        Settings,
        TestClient,
        _FakeDemoExecution,
    ]
]:
    engine = create_engine(POSTGRES_URL, poolclass=NullPool)
    reset_public_schema(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        class_=_TrackingSession,
        expire_on_commit=False,
    )
    settings = _settings(database_url=POSTGRES_URL).model_copy(
        update={"exchange_mode": ExchangeMode.PAPER_EXCHANGE_DEMO}
    )
    with factory() as session:
        _seed_org_user(session)
        user = session.get(User, USER_ID)
        assert user is not None
        user.hashed_password = hash_password(_HTTP_PASSWORD, settings)
        session.commit()

    fake_demo = _FakeDemoExecution()
    monkeypatch.setattr(
        dependencies,
        "resolve_exchange_execution_provider",
        lambda _settings: fake_demo,
    )
    monkeypatch.setattr(
        "app.main.run_exchange_demo_startup_check",
        lambda _settings, _registry: None,
    )

    def _override_session() -> Iterator[_TrackingSession]:
        with factory() as session:
            yield session

    app = create_app(settings=settings)
    app.dependency_overrides[get_session] = _override_session
    with TestClient(app, raise_server_exceptions=False) as client:
        login = client.post(
            "/auth/login",
            json={"email": "at028@test.example", "password": _HTTP_PASSWORD},
        )
        assert login.status_code == 200, login.text
        client.headers.update({"Authorization": f"Bearer {login.json()['tokens']['access_token']}"})
        _TrackingSession.commit_count = 0
        yield factory, settings, client, fake_demo

    reset_public_schema(engine)
    engine.dispose()


def _run_concurrent_placements(
    factory: sessionmaker[Session],
    settings: Settings,
    req: PaperOrderRequest,
    *,
    workers: int,
) -> tuple[list[uuid.UUID], list[bool], list[BaseException]]:
    results: list[uuid.UUID] = []
    created_flags: list[bool] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    start_barrier = threading.Barrier(workers)

    def _worker() -> None:
        start_barrier.wait(timeout=10)
        try:
            order_id, created_new = _route_place(factory, settings, req)
            with lock:
                results.append(order_id)
                created_flags.append(created_new)
        except BaseException as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results, created_flags, errors


def _run_concurrent_http_placements(
    client: TestClient,
    payload: dict[str, object],
    *,
    workers: int,
) -> list[tuple[int, dict[str, object]]]:
    results: list[tuple[int, dict[str, object]]] = []
    lock = threading.Lock()
    start_barrier = threading.Barrier(workers)

    def _worker() -> None:
        start_barrier.wait(timeout=10)
        response = client.post("/execution/paper", json=payload)
        with lock:
            results.append((response.status_code, response.json()))

    threads = [threading.Thread(target=_worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not thread.is_alive() for thread in threads)
    return results


def _http_payload(
    proposal_id: uuid.UUID,
    approval_id: uuid.UUID,
    *,
    key: str,
) -> dict[str, object]:
    return {
        "proposal_id": str(proposal_id),
        "approval_id": str(approval_id),
        "symbol": "BTCUSDT",
        "side": "buy",
        "type": "market",
        "size": "0.005",
        "reduce_only": False,
        "idempotency_key": key,
    }


def test_two_concurrent_identical_requests_converge_sqlite(
    sqlite_file_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = sqlite_file_db
    with factory() as session:
        pid, aid = _seed_approved(session)
    req = _paper_request(pid, aid)

    results, created_flags, errors = _run_concurrent_placements(factory, settings, req, workers=2)
    assert results == []
    assert created_flags == []
    assert len(errors) == 2
    assert all(
        isinstance(exc, TradingPolicyError)
        and exc.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        for exc in errors
    )

    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0
        assert _creation_audit_count(session, request_id=_IDEM_KEY) == 0
        assert _position_count(session) == 0
        assert _daily_risk_trade_count(session) == 0


@requires_postgres
def test_two_concurrent_identical_requests_converge_postgres(
    postgres_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = postgres_db
    with factory() as session:
        pid, aid = _seed_approved(session)
    req = _paper_request(pid, aid)

    results, created_flags, errors = _run_concurrent_placements(factory, settings, req, workers=2)
    assert results == []
    assert created_flags == []
    assert len(errors) == 2
    assert all(
        isinstance(exc, TradingPolicyError)
        and exc.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        for exc in errors
    )

    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0
        assert _creation_audit_count(session, request_id=_IDEM_KEY) == 0
        assert _position_count(session) == 0


@requires_postgres
def test_five_concurrent_identical_requests_converge_postgres(
    postgres_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = postgres_db
    with factory() as session:
        pid, aid = _seed_approved(session)
    req = _paper_request(pid, aid, key="at028-five-way-key")

    results, created_flags, errors = _run_concurrent_placements(factory, settings, req, workers=5)
    assert results == []
    assert created_flags == []
    assert len(errors) == 5
    assert all(
        isinstance(exc, TradingPolicyError)
        and exc.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        for exc in errors
    )

    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0
        assert _creation_audit_count(session, request_id=req.idempotency_key) == 0


@requires_postgres
def test_two_concurrent_http_requests_converge_with_fresh_quota_postgres(
    postgres_http_db: tuple[
        sessionmaker[_TrackingSession],
        Settings,
        TestClient,
        _FakeDemoExecution,
    ],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, _settings, client, fake_demo = postgres_http_db
    with factory() as session:
        pid, aid = _seed_approved(session)

    lookup_barrier = threading.Barrier(2)
    lookup_state = threading.local()
    original_get = QuotaRepository.get_by_organization

    def _synchronized_get(
        repository: QuotaRepository,
        organization_id: uuid.UUID,
    ) -> OrganizationQuota | None:
        row = original_get(repository, organization_id)
        first_lookup = not getattr(lookup_state, "seen", False)
        lookup_state.seen = True
        if first_lookup and row is None:
            lookup_barrier.wait(timeout=10)
        return row

    monkeypatch.setattr(QuotaRepository, "get_by_organization", _synchronized_get)
    key = "at028-http-two-fresh-quota"
    _TrackingSession.commit_count = 0
    results = _run_concurrent_http_placements(
        client,
        _http_payload(pid, aid, key=key),
        workers=2,
    )

    assert [status_code for status_code, _body in results] == [403, 403]
    assert all(body["error"]["code"] == "trading_policy_violation" for _status, body in results)
    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0
        assert _position_count(session) == 0
    assert len(fake_demo.calls) == 0
    assert _TrackingSession.commit_count == 0


@requires_postgres
def test_five_concurrent_http_requests_serialize_and_converge_postgres(
    postgres_http_db: tuple[
        sessionmaker[_TrackingSession],
        Settings,
        TestClient,
        _FakeDemoExecution,
    ],
) -> None:
    factory, _settings, client, fake_demo = postgres_http_db
    with factory() as session:
        pid, aid = _seed_approved(session)
        QuotaService(session).get_or_create_quota(ORG_ID)
        session.commit()

    key = "at028-http-five-existing-quota"
    _TrackingSession.commit_count = 0
    results = _run_concurrent_http_placements(
        client,
        _http_payload(pid, aid, key=key),
        workers=5,
    )

    assert [status_code for status_code, _body in results] == [403] * 5
    assert all(body["error"]["code"] == "trading_policy_violation" for _status, body in results)
    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0
        assert _position_count(session) == 0
    assert len(fake_demo.calls) == 0
    assert _TrackingSession.commit_count == 0


@requires_postgres
def test_http_replay_skips_commit_and_different_key_creates_postgres(
    postgres_http_db: tuple[
        sessionmaker[_TrackingSession],
        Settings,
        TestClient,
        _FakeDemoExecution,
    ],
) -> None:
    factory, _settings, client, fake_demo = postgres_http_db
    with factory() as session:
        first_pid, first_aid = _seed_approved(session)
        second_pid, second_aid = _seed_approved(session)
        QuotaService(session).get_or_create_quota(ORG_ID)
        session.commit()

    first_payload = _http_payload(first_pid, first_aid, key="at028-http-replay")
    _TrackingSession.commit_count = 0
    first = client.post("/execution/paper", json=first_payload)
    commits_after_first = _TrackingSession.commit_count
    replay = client.post("/execution/paper", json=first_payload)
    commits_after_replay = _TrackingSession.commit_count
    other = client.post(
        "/execution/paper",
        json=_http_payload(second_pid, second_aid, key="at028-http-independent"),
    )

    assert first.status_code == replay.status_code == other.status_code == 403
    assert first.json()["error"]["code"] == "trading_policy_violation"
    assert commits_after_first == 0
    assert commits_after_replay == commits_after_first
    assert _TrackingSession.commit_count == 0
    assert len(fake_demo.calls) == 0
    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0
        assert _creation_audit_count(session, request_id="at028-http-replay") == 0
        assert _creation_audit_count(session, request_id="at028-http-independent") == 0


@requires_postgres
def test_http_convergence_exhaustion_is_sanitized_409_postgres(
    postgres_http_db: tuple[
        sessionmaker[_TrackingSession],
        Settings,
        TestClient,
        _FakeDemoExecution,
    ],
) -> None:
    factory, _settings, client, _fake_demo = postgres_http_db
    with factory() as session:
        pid, aid = _seed_approved(session)
        QuotaService(session).get_or_create_quota(ORG_ID)
        session.commit()

    _TrackingSession.commit_count = 0
    response = client.post(
        "/execution/paper",
        json=_http_payload(pid, aid, key="at028-http-exhaustion"),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "trading_policy_violation"
    assert _TrackingSession.commit_count == 0
    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0


@requires_postgres
def test_http_unrelated_integrity_error_remains_internal_failure_postgres(
    postgres_http_db: tuple[
        sessionmaker[_TrackingSession],
        Settings,
        TestClient,
        _FakeDemoExecution,
    ],
) -> None:
    factory, _settings, client, _fake_demo = postgres_http_db
    with factory() as session:
        pid, aid = _seed_approved(session)
        QuotaService(session).get_or_create_quota(ORG_ID)
        session.commit()

    unrelated_error = IntegrityError("insert", {}, Exception("unrelated constraint"))
    _TrackingSession.commit_count = 0
    with patch.object(
        ExecutionService,
        "_persist_new_paper_order",
        side_effect=unrelated_error,
    ):
        response = client.post(
            "/execution/paper",
            json=_http_payload(pid, aid, key="at028-http-unrelated"),
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "trading_policy_violation"
    assert _TrackingSession.commit_count == 0
    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0


def test_different_idempotency_keys_create_independent_orders(
    sqlite_file_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = sqlite_file_db
    with factory() as session:
        pid1, aid1 = _seed_approved(session)
        pid2, aid2 = _seed_approved(session)

    with pytest.raises(TradingPolicyError) as first_exc:
        _route_place(factory, settings, _paper_request(pid1, aid1, key=_IDEM_KEY))
    assert first_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
    with pytest.raises(TradingPolicyError) as second_exc:
        _route_place(factory, settings, _paper_request(pid2, aid2, key=_OTHER_KEY))
    assert second_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON

    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0


def test_sequential_replay_unchanged(
    sqlite_file_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = sqlite_file_db
    with factory() as session:
        pid, aid = _seed_approved(session)
    req = _paper_request(pid, aid, key="at028-seq-replay")

    with pytest.raises(TradingPolicyError) as first_exc:
        _route_place(factory, settings, req)
    assert first_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
    with pytest.raises(TradingPolicyError) as replay_exc:
        _route_place(factory, settings, req)
    assert replay_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON

    with factory() as session:
        assert _order_count(session) == 0
        assert _usage_count(session) == 0
        assert _creation_audit_count(session, request_id=req.idempotency_key) == 0


def test_winning_transaction_rollback_allows_later_create(
    sqlite_file_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = sqlite_file_db
    with factory() as session:
        pid, aid = _seed_approved(session)
    req = _paper_request(pid, aid, key="at028-rollback-recover")

    with factory() as session:
        svc = ExecutionService(session, settings, AuditService(session))
        with pytest.raises(TradingPolicyError) as first_exc:
            svc.place_paper_order(req)
        assert first_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        session.rollback()

    with pytest.raises(TradingPolicyError) as later_exc:
        _route_place(factory, settings, req)
    assert later_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON

    with factory() as session:
        assert _order_count(session) == 0


@requires_postgres
def test_winning_transaction_rollback_allows_later_create_postgres(
    postgres_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = postgres_db
    with factory() as session:
        pid, aid = _seed_approved(session)
    req = _paper_request(pid, aid, key="at028-rollback-recover-pg")

    with factory() as session:
        svc = ExecutionService(session, settings, AuditService(session))
        with pytest.raises(TradingPolicyError) as first_exc:
            svc.place_paper_order(req)
        assert first_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        session.rollback()

    with pytest.raises(TradingPolicyError) as later_exc:
        _route_place(factory, settings, req)
    assert later_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON

    with factory() as session:
        assert _order_count(session) == 0


def test_bounded_convergence_exhaustion_returns_retryable_error(
    sqlite_file_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = sqlite_file_db
    with factory() as session:
        pid, aid = _seed_approved(session)
        proposal = session.get(TradeProposal, pid)
        assert proposal is not None
        req = _paper_request(pid, aid, key="at028-exhaust-key")
        svc = ExecutionService(session, settings, AuditService(session))
        bound = svc._risk_gate.evaluate(
            proposal=proposal,
            approval=ApprovalService(session, AuditService(session)).get(aid),
            request=req,
        )
        with pytest.raises(TradingPolicyError) as exc_info:
            svc._create_or_converge_paper_order(
                request=req,
                proposal=proposal,
                organization_id=ORG_ID,
                user_id=USER_ID,
                bound=bound,
            )
        assert exc_info.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        session.rollback()


def test_loser_does_not_pollute_unrelated_pending_session_rows(
    sqlite_file_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = sqlite_file_db
    with factory() as session:
        pid, aid = _seed_approved(session)
    req = _paper_request(pid, aid, key="at028-session-pollution")

    with pytest.raises(TradingPolicyError) as first_exc:
        _route_place(factory, settings, req)
    assert first_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON

    with factory() as session:
        pending = Organization(name="Unrelated Pending Org")
        session.add(pending)
        session.flush()
        pending_id = pending.id

        with pytest.raises(TradingPolicyError) as replay_exc:
            ExecutionService(session, settings, AuditService(session)).place_paper_order(req)
        assert replay_exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        session.rollback()

    with factory() as verify:
        assert verify.get(Organization, pending_id) is None
        assert _usage_count(verify) == 0
        assert _creation_audit_count(verify, request_id=req.idempotency_key) == 0


@requires_postgres
def test_postgres_unique_conflict_is_recovered_without_client_retry(
    postgres_db: tuple[sessionmaker[Session], Settings],
) -> None:
    """Legacy paper placement is fail-closed; concurrent writers cannot create orders."""
    factory, settings = postgres_db
    with factory() as session:
        pid, aid = _seed_approved(session)
    req = _paper_request(pid, aid, key="at028-pg-unique-conflict")

    results, created_flags, errors = _run_concurrent_placements(factory, settings, req, workers=2)
    assert results == []
    assert created_flags == []
    assert len(errors) == 2
    assert all(
        isinstance(exc, TradingPolicyError)
        and exc.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        for exc in errors
    )
    with factory() as session:
        assert _order_count(session) == 0


def test_is_order_idempotency_unique_violation_detects_constraint_name() -> None:
    from app.services.paper_order_idempotency import is_order_idempotency_unique_violation

    class _Diag:
        constraint_name = "uq_orders_idempotency_key"

    class _Orig:
        diag = _Diag()

    assert is_order_idempotency_unique_violation(IntegrityError("stmt", {}, _Orig())) is True


def test_quota_creation_does_not_swallow_unrelated_integrity_error(
    sqlite_file_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, _settings = sqlite_file_db
    with factory() as session:
        service = QuotaService(session)
        unrelated = IntegrityError("insert", {}, Exception("unrelated constraint"))
        with (
            patch.object(service._quotas, "add", side_effect=unrelated),
            pytest.raises(IntegrityError) as exc_info,
        ):
            service.get_or_create_quota(ORG_ID)
        assert exc_info.value is unrelated
        session.rollback()


@requires_postgres
def test_postgres_quota_creation_does_not_swallow_unrelated_integrity_error(
    postgres_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, _settings = postgres_db
    with factory() as session:
        service = QuotaService(session)
        unrelated = IntegrityError("insert", {}, Exception("unrelated constraint"))
        with (
            patch.object(service._quotas, "add", side_effect=unrelated),
            pytest.raises(IntegrityError) as exc_info,
        ):
            service.get_or_create_quota(ORG_ID)
        assert exc_info.value is unrelated
        assert session.is_active is True
        session.rollback()
