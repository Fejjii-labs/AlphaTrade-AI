"""Phase 8 composition: production runtime, ProposalService firewall, HTTP surface."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.errors import NotFoundError, TradingPolicyError
from app.db.canonical_trade_plans import PLAN_ROOT_ANALYSIS, PLAN_ROOT_CANONICAL
from app.db.models import TradeProposal as TradeProposalModel
from app.main import create_app
from app.runtime.canonical import (
    CanonicalRuntimeFlags,
    ProductionCanonicalRuntime,
    runtime_flags_from_settings,
)
from app.schemas.execution_protocol import ExecutePaperPlanHttpRequest, ExecutionCommandOutcome
from app.services.audit_service import AuditService
from app.services.canonical_execution_journal import CANONICAL_EXECUTION_SOURCE_SYSTEM
from app.services.proposal_service import ProposalService
from app.workers.lock import InMemoryWorkerLock
from app.workers.service import WorkerService
from tests.support.phase1_plan_fixtures import (
    EXECUTE_AT,
    execute_request,
    execution_service,
    prepared_authorized_plan,
    seed_support,
    sqlite_session,
)


@pytest.fixture
def session() -> Iterator[Session]:
    yield from sqlite_session()


def _local_settings(**updates: object) -> Settings:
    payload: dict[str, object] = {
        "environment": "local",
        "log_json": False,
        "execution_mode": "paper",
        "enable_real_trading": False,
        "exchange_mode": "paper_internal",
        "provider_mode": "mock",
        "market_data_provider": "mock",
        "database_url": "sqlite+pysqlite:///:memory:",
        "jwt_secret": "phase8-runtime-secret-32-bytes-min",
        "rate_limit_use_redis": False,
        "access_token_denylist_use_redis": False,
        "metrics_enabled": False,
        "worker_enabled": False,
    }
    payload.update(updates)
    return Settings(**payload)  # type: ignore[arg-type]


def test_runtime_flags_default_off() -> None:
    flags = CanonicalRuntimeFlags()
    assert flags.watcher_orchestration_enabled is False
    assert flags.market_watcher_enabled is False
    assert flags.telegram_interaction_enabled is False
    assert flags.real_trading_enabled is False
    derived = runtime_flags_from_settings(_local_settings())
    assert derived.watcher_orchestration_enabled is False
    assert derived.telegram_interaction_enabled is False
    assert derived.real_trading_enabled is False


def test_create_app_attaches_canonical_runtime_without_enabling_watcher() -> None:
    app = create_app(_local_settings())
    with TestClient(app):
        runtime = app.state.canonical_runtime
        assert isinstance(runtime, ProductionCanonicalRuntime)
        assert runtime.watcher_enabled is False
        assert runtime.telegram_enabled is False
        assert runtime.flags.real_trading_enabled is False


def test_proposal_create_is_analysis_proposal(session: Session) -> None:
    ids = seed_support(session)
    row = session.get(TradeProposalModel, ids["proposal_id"])
    assert row is not None
    assert row.plan_root_kind == PLAN_ROOT_ANALYSIS


def test_proposal_service_filters_and_rejects_canonical_plan_root(session: Session) -> None:
    ids = seed_support(session)
    row = session.get(TradeProposalModel, ids["proposal_id"])
    assert row is not None
    row.plan_root_kind = PLAN_ROOT_CANONICAL
    session.flush()
    service = ProposalService(session, AuditService(session))
    with pytest.raises(TradingPolicyError) as exc:
        service.get(ids["proposal_id"], organization_id=ids["organization"].id)
    assert exc.value.details["reason"] == "canonical_plan_root_not_proposal_authority"
    items, total = service.list_proposals(organization_id=ids["organization"].id)
    assert total == 0
    assert items == []
    with pytest.raises(TradingPolicyError) as scoped_exc:
        service.get(
            ids["proposal_id"],
            organization_id=ids["organization"].id,
            user_id=ids["user"].id,
        )
    assert scoped_exc.value.details["reason"] == "canonical_plan_root_not_proposal_authority"


def test_proposal_service_canonical_plan_root_is_not_an_existence_oracle(session: Session) -> None:
    ids = seed_support(session)
    row = session.get(TradeProposalModel, ids["proposal_id"])
    assert row is not None
    row.plan_root_kind = PLAN_ROOT_CANONICAL
    session.flush()
    service = ProposalService(session, AuditService(session))
    with pytest.raises(NotFoundError):
        service.get(
            ids["proposal_id"],
            organization_id=ids["other_organization"].id,
            user_id=ids["other_user"].id,
        )
    with pytest.raises(NotFoundError):
        service.list_revisions(
            ids["proposal_id"],
            organization_id=ids["other_organization"].id,
            user_id=ids["other_user"].id,
        )
    with pytest.raises(NotFoundError):
        service.get_revision(
            ids["proposal_id"],
            uuid4(),
            organization_id=ids["other_organization"].id,
            user_id=ids["other_user"].id,
        )


def test_paper_validation_execute_still_works_with_bound_runtime(session: Session) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)
    service._canonical_runtime = SimpleNamespace()  # type: ignore[assignment]
    result = service.execute_paper_plan(
        execute_request(ids, plan, authorization, key="paper-validation-with-runtime"),
        clock=lambda: EXECUTE_AT,
    )
    session.commit()
    assert result.outcome is ExecutionCommandOutcome.ALLOW
    assert result.replayed is False


def test_canonical_authority_without_runtime_fails_closed(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids, plan, authorization = prepared_authorized_plan(session)
    service = execution_service(session)

    def _canonical_row(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(plan_authority="canonical")

    monkeypatch.setattr(service._revisions, "get_scoped", _canonical_row)
    with pytest.raises(TradingPolicyError) as exc:
        service.execute_paper_plan(
            execute_request(ids, plan, authorization, key="unbound-canonical"),
            clock=lambda: EXECUTE_AT,
        )
    assert exc.value.details["reason"] == "canonical_runtime_unbound"


def test_http_body_forbids_executable_fields() -> None:
    with pytest.raises(ValidationError):
        ExecutePaperPlanHttpRequest.model_validate(
            {
                "account_id": str(uuid4()),
                "authorization_id": str(uuid4()),
                "revision_id": str(uuid4()),
                "idempotency_key": "k",
                "quantity": "2",
            }
        )


def test_execute_paper_plan_http_requires_auth() -> None:
    app = create_app(_local_settings())
    with TestClient(app) as client:
        response = client.post(
            "/execution/paper-plan",
            json={
                "account_id": str(uuid4()),
                "authorization_id": str(uuid4()),
                "revision_id": str(uuid4()),
                "idempotency_key": "http-unauth",
            },
        )
    assert response.status_code == 401


def test_canonical_journal_source_constant() -> None:
    assert CANONICAL_EXECUTION_SOURCE_SYSTEM == "canonical_paper_execution"


def test_worker_service_accepts_runtime_without_starting_watcher() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime = SimpleNamespace(
        watcher_enabled=False,
        telegram_enabled=False,
        flags=CanonicalRuntimeFlags(),
    )
    service = WorkerService(
        factory,
        InMemoryWorkerLock("phase8", ttl_seconds=60),
        worker_name="phase8-worker",
        canonical_runtime=runtime,  # type: ignore[arg-type]
    )
    assert service.canonical_runtime is runtime
    assert service.canonical_runtime.watcher_enabled is False  # type: ignore[union-attr]
    engine.dispose()
