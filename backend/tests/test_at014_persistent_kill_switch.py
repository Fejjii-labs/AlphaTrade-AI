"""AT-014: persistent server-side kill switch."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.errors import ConflictError, TradingPolicyError
from app.db.base import Base
from app.db.models import (
    AuditLog,
    KillSwitchState,
    Membership,
    Organization,
    User,
)
from app.db.session import get_session
from app.main import create_app
from app.schemas.approval import ApprovalDecisionRequest
from app.schemas.common import (
    ActorType,
    ApprovalAction,
    AuditEventType,
    MembershipRole,
    RiskAction,
    RiskSeverity,
    StrategyId,
)
from app.schemas.execution import PaperOrderRequest
from app.schemas.proposal import ExitCriteria, TakeProfitLevel, TradeProposalCreate
from app.schemas.risk import KillSwitchMutationRequest, RiskCheckResult
from app.security.passwords import hash_password
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService
from app.services.execution_service import LEGACY_PAPER_EXECUTION_REASON, ExecutionService
from app.services.proposal_service import ProposalService
from app.services.risk.kill_switch import KillSwitchService

ORG_A = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
ORG_B = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
OWNER_A = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
TRADER_A = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
OWNER_B = uuid.UUID("00000000-0000-0000-0000-0000000000c3")

_SIZE = Decimal("0.005")
_ENTRY = Decimal("60000")
_STOP = Decimal("58000")
_PASSWORD = "TestPassword123!"


@pytest.fixture
def at014_db() -> Iterator[tuple[sessionmaker[Session], Settings]]:
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
        global_kill_switch_active=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="at014-kill-switch-secret-32-bytes-min",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
    )
    with factory() as session:
        session.add(Organization(id=ORG_A, name="AT014 Org A"))
        session.add(Organization(id=ORG_B, name="AT014 Org B"))
        for uid, email in (
            (OWNER_A, "owner-a@test.example"),
            (TRADER_A, "trader-a@test.example"),
            (OWNER_B, "owner-b@test.example"),
        ):
            session.add(
                User(
                    id=uid,
                    email=email,
                    hashed_password=hash_password(_PASSWORD, settings),
                )
            )
        session.flush()
        session.add(Membership(user_id=OWNER_A, organization_id=ORG_A, role=MembershipRole.OWNER))
        session.add(Membership(user_id=TRADER_A, organization_id=ORG_A, role=MembershipRole.TRADER))
        session.add(Membership(user_id=OWNER_B, organization_id=ORG_B, role=MembershipRole.OWNER))
        session.commit()
    yield factory, settings
    engine.dispose()


def _kill(session: Session, settings: Settings) -> KillSwitchService:
    return KillSwitchService(session, AuditService(session), settings)


def _activate(
    service: KillSwitchService,
    *,
    org: uuid.UUID = ORG_A,
    actor: uuid.UUID = OWNER_A,
    reason: str = "incident response",
    expected_version: int | None = None,
) -> None:
    service.activate(
        organization_id=org,
        actor_user_id=actor,
        payload=KillSwitchMutationRequest(
            confirm=True,
            reason=reason,
            expected_version=expected_version,
        ),
    )


def _exit() -> ExitCriteria:
    return ExitCriteria(
        invalidation="Close below stop.",
        stop_loss=_STOP,
        take_profits=[TakeProfitLevel(price=Decimal("62000"), size_fraction=0.5)],
    )


def _allow_risk() -> RiskCheckResult:
    return RiskCheckResult(
        action=RiskAction.ALLOW,
        severity=RiskSeverity.LOW,
        explanation="seed allow",
        approval_required=True,
    )


def _seed_approved(
    session: Session,
    *,
    org: uuid.UUID = ORG_A,
    user: uuid.UUID = OWNER_A,
) -> tuple[uuid.UUID, uuid.UUID]:
    audit = AuditService(session)
    proposals = ProposalService(session, audit)
    approvals = ApprovalService(session, audit)
    proposal = proposals.create(
        TradeProposalCreate(
            organization_id=org,
            user_id=user,
            strategy_id=StrategyId.HTF_TREND_PULLBACK,
            symbol="BTCUSDT",
            timeframe="4h",
            direction="long",
            entry_price=_ENTRY,
            position_size=_SIZE,
            leverage=Decimal("3"),
            exit=_exit(),
            confidence=0.7,
            risk_level=RiskSeverity.MEDIUM,
            rationale="at014",
            approval_required=True,
            risk_result=_allow_risk(),
        )
    )
    approval = approvals.create_for_proposal(
        proposal_id=proposal.id,  # type: ignore[arg-type]
        organization_id=org,
        user_id=user,
        risk_level=proposal.risk_level,
        confidence=float(proposal.confidence),
    )
    approvals.decide(approval.id, ApprovalDecisionRequest(action=ApprovalAction.APPROVE))
    session.commit()
    return proposal.id, approval.id  # type: ignore[return-value]


def _place(
    session: Session,
    settings: Settings,
    proposal_id: uuid.UUID,
    approval_id: uuid.UUID,
    *,
    key: str,
) -> None:
    ExecutionService(session, settings, AuditService(session)).place_paper_order(
        PaperOrderRequest(
            proposal_id=proposal_id,
            approval_id=approval_id,
            symbol="BTCUSDT",
            side="buy",
            type="market",
            size=_SIZE,
            idempotency_key=key,
        )
    )


def test_activation_persists_across_service_recreation(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = at014_db
    with factory() as session:
        _activate(_kill(session, settings))
        session.commit()

    with factory() as session:
        status = _kill(session, settings).get_status(organization_id=ORG_A)
        assert status.active is True
        assert status.reason == "incident response"
        assert status.activated_by == OWNER_A
        assert status.activated_at is not None


def test_multiple_service_instances_observe_same_state(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = at014_db
    with factory() as session:
        _activate(_kill(session, settings))
        session.commit()
        a = _kill(session, settings).evaluate(organization_id=ORG_A)
        b = _kill(session, settings).evaluate(organization_id=ORG_A)
        assert a.blocked is True
        assert b.blocked is True
        assert a.status is not None and b.status is not None
        assert a.status.version == b.status.version


def test_active_blocks_paper_placement(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = at014_db
    with factory() as session:
        pid, aid = _seed_approved(session)
        _activate(_kill(session, settings))
        session.commit()
        with pytest.raises(TradingPolicyError) as exc:
            _place(session, settings, pid, aid, key="ks-block-1")
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON


def test_inactive_allows_valid_paper_placement(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = at014_db
    with factory() as session:
        pid, aid = _seed_approved(session)
        order_key = "ks-allow-01"
        with pytest.raises(TradingPolicyError) as exc:
            _place(session, settings, pid, aid, key=order_key)
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON


def test_demo_path_cannot_bypass(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, base = at014_db
    settings = base.model_copy(update={"exchange_mode": "paper_exchange_demo"})
    with factory() as session:
        pid, aid = _seed_approved(session)
        _activate(_kill(session, settings))
        session.commit()
        fake = MagicMock()
        with pytest.raises(TradingPolicyError) as exc:
            ExecutionService(
                session,
                settings,
                AuditService(session),
                exchange_execution=fake,
            ).place_paper_order(
                PaperOrderRequest(
                    proposal_id=pid,
                    approval_id=aid,
                    symbol="BTCUSDT",
                    side="buy",
                    type="market",
                    size=_SIZE,
                    idempotency_key="demo-ks-001",
                )
            )
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON
        assert fake.place_order.call_count == 0


def test_unavailable_storage_refuses_execution(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = at014_db
    with factory() as session:
        pid, aid = _seed_approved(session)
        service = _kill(session, settings)
        with patch.object(service, "get_status", side_effect=RuntimeError("db down")):
            with pytest.raises(TradingPolicyError) as exc:
                service.assert_execution_allowed(organization_id=ORG_A, user_id=OWNER_A)
            assert exc.value.details.get("reason") == "kill_switch_unavailable"

        exec_svc = ExecutionService(session, settings, AuditService(session))
        with patch.object(
            exec_svc._kill_switch,
            "assert_execution_allowed",
            side_effect=TradingPolicyError(
                "Kill switch state is unavailable; execution refused.",
                details={"reason": "kill_switch_unavailable"},
            ),
        ):
            with pytest.raises(TradingPolicyError) as exc2:
                exec_svc.place_paper_order(
                    PaperOrderRequest(
                        proposal_id=pid,
                        approval_id=aid,
                        symbol="BTCUSDT",
                        side="buy",
                        type="market",
                        size=_SIZE,
                        idempotency_key="ks-unavail1",
                    )
                )
            assert exc2.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON


def test_idempotent_activate_deactivate(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = at014_db
    with factory() as session:
        svc = _kill(session, settings)
        first = svc.activate(
            organization_id=ORG_A,
            actor_user_id=OWNER_A,
            payload=KillSwitchMutationRequest(confirm=True, reason="first"),
        )
        second = svc.activate(
            organization_id=ORG_A,
            actor_user_id=OWNER_A,
            payload=KillSwitchMutationRequest(confirm=True, reason="second"),
        )
        assert first.active is True
        assert second.active is True
        assert second.version == first.version
        assert second.reason == "first"

        off1 = svc.deactivate(
            organization_id=ORG_A,
            actor_user_id=OWNER_A,
            payload=KillSwitchMutationRequest(confirm=True, reason="clear"),
        )
        off2 = svc.deactivate(
            organization_id=ORG_A,
            actor_user_id=OWNER_A,
            payload=KillSwitchMutationRequest(confirm=True, reason="clear again"),
        )
        assert off1.active is False
        assert off2.active is False
        assert off2.version == off1.version


def test_system_activation_is_not_stored_as_the_user(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = at014_db
    with factory() as session:
        svc = _kill(session, settings)
        status = svc.activate(
            organization_id=ORG_A,
            actor_user_id=OWNER_A,
            payload=KillSwitchMutationRequest(confirm=True, reason="monitor loss"),
            actor_type=ActorType.SYSTEM,
        )
        session.commit()
        assert status.active is True
        assert status.activated_by is None
        row = session.scalar(
            select(KillSwitchState).where(KillSwitchState.organization_id == ORG_A)
        )
        assert row is not None
        assert row.activated_by is None
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.organization_id == ORG_A,
                AuditLog.action == AuditEventType.KILL_SWITCH_ACTIVATED,
            )
        )
        assert audit is not None
        assert audit.actor_type is ActorType.SYSTEM
        assert audit.user_id is None
        assert audit.actor == "system"


def test_tenant_isolation(at014_db: tuple[sessionmaker[Session], Settings]) -> None:
    factory, settings = at014_db
    with factory() as session:
        _activate(_kill(session, settings), org=ORG_A)
        session.commit()
        assert _kill(session, settings).evaluate(organization_id=ORG_A).blocked is True
        assert _kill(session, settings).evaluate(organization_id=ORG_B).blocked is False


def test_audit_events_on_mutation(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = at014_db
    with factory() as session:
        svc = _kill(session, settings)
        svc.activate(
            organization_id=ORG_A,
            actor_user_id=OWNER_A,
            payload=KillSwitchMutationRequest(confirm=True, reason="audit on"),
        )
        svc.deactivate(
            organization_id=ORG_A,
            actor_user_id=OWNER_A,
            payload=KillSwitchMutationRequest(confirm=True, reason="audit off"),
        )
        session.commit()
        events = {
            row.action
            for row in session.scalars(
                select(AuditLog).where(AuditLog.organization_id == ORG_A)
            ).all()
        }
        assert AuditEventType.KILL_SWITCH_ACTIVATED in events
        assert AuditEventType.KILL_SWITCH_DEACTIVATED in events


def test_concurrent_version_conflict(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = at014_db
    with factory() as session:
        svc = _kill(session, settings)
        status = svc.get_status(organization_id=ORG_A)
        svc.activate(
            organization_id=ORG_A,
            actor_user_id=OWNER_A,
            payload=KillSwitchMutationRequest(confirm=True, reason="race"),
        )
        with pytest.raises(ConflictError) as exc:
            svc.deactivate(
                organization_id=ORG_A,
                actor_user_id=OWNER_A,
                payload=KillSwitchMutationRequest(
                    confirm=True,
                    reason="stale",
                    expected_version=status.version,
                ),
            )
        assert exc.value.details.get("reason") == "version_conflict"


def test_global_env_kill_switch_blocks(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, base = at014_db
    settings = base.model_copy(update={"global_kill_switch_active": True})
    with factory() as session:
        pid, aid = _seed_approved(session)
        with pytest.raises(TradingPolicyError) as exc:
            _place(session, settings, pid, aid, key="global-ks-1")
        assert exc.value.details.get("reason") == LEGACY_PAPER_EXECUTION_REASON


def _client(factory: sessionmaker[Session], settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)

    def _override() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": _PASSWORD},
    )
    assert response.status_code == 200, response.text
    token = response.json()["tokens"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_unauthorized_trader_cannot_activate(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = at014_db
    for client in _client(factory, settings):
        headers = _login(client, "trader-a@test.example")
        denied = client.post(
            "/risk/kill-switch/activate",
            headers=headers,
            json={"confirm": True, "reason": "should fail"},
        )
        assert denied.status_code == 403

        owner_headers = _login(client, "owner-a@test.example")
        ok = client.post(
            "/risk/kill-switch/activate",
            headers=owner_headers,
            json={"confirm": True, "reason": "owner ok"},
        )
        assert ok.status_code == 200
        assert ok.json()["active"] is True

        status = client.get("/risk/kill-switch", headers=headers)
        assert status.status_code == 200
        assert status.json()["active"] is True


def test_confirm_required(at014_db: tuple[sessionmaker[Session], Settings]) -> None:
    factory, settings = at014_db
    for client in _client(factory, settings):
        headers = _login(client, "owner-a@test.example")
        response = client.post(
            "/risk/kill-switch/activate",
            headers=headers,
            json={"confirm": False, "reason": "no confirm"},
        )
        assert response.status_code == 422


def test_paper_defaults_unchanged(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    _factory, settings = at014_db
    assert settings.execution_mode.value == "paper"
    assert settings.enable_real_trading is False
    assert settings.global_kill_switch_active is False


def test_row_persisted_in_table(
    at014_db: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = at014_db
    with factory() as session:
        _activate(_kill(session, settings))
        session.commit()
        row = session.scalar(
            select(KillSwitchState).where(KillSwitchState.organization_id == ORG_A)
        )
        assert row is not None
        assert row.active is True
        assert row.version >= 2
