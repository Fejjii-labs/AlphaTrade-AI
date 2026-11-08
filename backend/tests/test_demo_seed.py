"""Slice 50 — demo seed idempotency, safety, and dashboard integration."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Environment, Settings
from app.db.base import Base
from app.db.models import (
    LessonCandidate,
    Membership,
    Organization,
    PaperValidationAlert,
    User,
    UserStrategy,
)
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import LessonCandidateStatus, MembershipRole
from app.services.dashboard_summary_service import DashboardSummaryService
from app.services.demo_seed_service import (
    DEMO_EMAIL,
    DemoSeedService,
    assert_demo_seed_allowed,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def demo_seed_env() -> Iterator[tuple[sessionmaker[Session], Settings]]:
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
        demo_seed_enabled=True,
        log_json=False,
        execution_mode="paper",
        enable_real_trading=False,
        database_url="sqlite+pysqlite:///:memory:",
        jwt_secret="demo-seed-test-secret-key-32bytes",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        provider_mode="mock",
        market_data_provider="mock",
        require_email_verified=False,
    )
    yield factory, settings
    engine.dispose()


@pytest.fixture
def demo_client(demo_seed_env: tuple[sessionmaker[Session], Settings]) -> TestClient:
    factory, settings = demo_seed_env
    app = create_app(settings)

    def _override_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    return TestClient(app)


def test_demo_seed_refused_on_staging_without_flag() -> None:
    settings = Settings.model_construct(
        environment=Environment.STAGING,
        demo_seed_enabled=False,
        execution_mode="paper",
        enable_real_trading=False,
        real_trading_enabled=False,
    )
    with pytest.raises(Exception, match="DEMO_SEED_ENABLED"):
        assert_demo_seed_allowed(settings)


def test_demo_seed_refused_in_production() -> None:
    settings = Settings.model_construct(
        environment=Environment.PRODUCTION,
        demo_seed_enabled=True,
        execution_mode="paper",
        enable_real_trading=False,
        real_trading_enabled=False,
    )
    with pytest.raises(Exception, match="production"):
        assert_demo_seed_allowed(settings)


def test_demo_seed_idempotent_and_paper_only(
    demo_seed_env: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = demo_seed_env
    with factory() as session:
        service = DemoSeedService(session, settings)
        first = service.seed(password="DemoPaper2026!")
        second = service.seed(password="DemoPaper2026!")
        assert first.email == second.email == DEMO_EMAIL
        assert first.organization_id == second.organization_id

        strategy_count = session.scalar(
            select(func.count())
            .select_from(UserStrategy)
            .where(UserStrategy.organization_id == first.organization_id)
        )
        assert strategy_count == 3

        pending = session.scalar(
            select(func.count())
            .select_from(LessonCandidate)
            .where(
                LessonCandidate.organization_id == first.organization_id,
                LessonCandidate.status == LessonCandidateStatus.PENDING_REVIEW.value,
            )
        )
        accepted = session.scalar(
            select(func.count())
            .select_from(LessonCandidate)
            .where(
                LessonCandidate.organization_id == first.organization_id,
                LessonCandidate.status == LessonCandidateStatus.ACCEPTED.value,
            )
        )
        assert pending == 3
        assert accepted == 2

        alerts = session.scalars(
            select(PaperValidationAlert).where(
                PaperValidationAlert.organization_id == first.organization_id
            )
        ).all()
        assert len(alerts) == 4
        for alert in alerts:
            assert alert.delivery_status.value == "disabled"


def test_dashboard_summary_after_seed(
    demo_seed_env: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = demo_seed_env
    with factory() as session:
        result = DemoSeedService(session, settings).seed(password="DemoPaper2026!")
        summary = DashboardSummaryService(session, settings).summarize(
            organization_id=result.organization_id,
            user_id=result.user_id,
        )
        assert summary.safety.execution_mode == "paper"
        assert summary.safety.real_trading_enabled is False
        assert summary.daily_discipline is not None
        assert summary.strategy_readiness is not None
        assert len(summary.active_paper_validations) >= 1
        assert summary.alerts_lessons is not None


def test_demo_seed_endpoint_owner_only(demo_client: TestClient) -> None:
    register = demo_client.post(
        "/auth/register",
        json={
            "email": "other-owner@test.example",
            "password": "DemoPaper2026!",
            "organization_name": "Other Org",
        },
    )
    assert register.status_code == 201
    token = register.json()["tokens"]["access_token"]
    with demo_client as client:
        seeded = client.post(
            "/demo/seed",
            headers={"Authorization": f"Bearer {token}"},
            json={"password": "DemoPaper2026!"},
        )
        assert seeded.status_code == 200
        body = seeded.json()
        assert body["paper_only"] is True
        assert body["synthetic"] is True
        assert body["strategies_seeded"] == 3


def test_health_exposes_must_verify_email(demo_client: TestClient) -> None:
    health = demo_client.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["must_verify_email"] is False
    assert body["real_trading_enabled"] is False


def test_demo_seed_removes_bootstrap_accounts(
    demo_seed_env: tuple[sessionmaker[Session], Settings],
) -> None:
    factory, settings = demo_seed_env
    with factory() as session:
        bootstrap_org = Organization(
            id=uuid.uuid4(),
            name="Demo Seed Bootstrap",
        )
        bootstrap_user = User(
            id=uuid.uuid4(),
            email="demo-seed-bootstrap-9999999999@example.com",
            hashed_password="not-used",
            email_verified=False,
        )
        session.add(bootstrap_org)
        session.add(bootstrap_user)
        session.flush()
        session.add(
            Membership(
                user_id=bootstrap_user.id,
                organization_id=bootstrap_org.id,
                role=MembershipRole.OWNER,
            )
        )
        session.commit()

        DemoSeedService(session, settings).seed(password="DemoPaper2026!")

        assert session.get(User, bootstrap_user.id) is None
        assert session.get(Organization, bootstrap_org.id) is None
        demo_user = session.scalar(select(User).where(User.email == DEMO_EMAIL))
        assert demo_user is not None


def _register(client: TestClient, email: str, org: str) -> str:
    resp = client.post(
        "/auth/register",
        json={"email": email, "password": "DemoPaper2026!", "organization_name": org},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["tokens"]["access_token"]


def test_demo_seed_endpoint_refuses_non_demo_owner_once_demo_org_exists(
    demo_client: TestClient,
    demo_seed_env: tuple[sessionmaker[Session], Settings],
) -> None:
    """H6: after bootstrap only demo-org owners may re-seed; others get 403."""
    factory, settings = demo_seed_env
    with factory() as session:
        DemoSeedService(session, settings).seed(password="DemoPaper2026!")
        bystander_org = Organization(id=uuid.uuid4(), name="Bystander")
        bystander = User(
            id=uuid.uuid4(),
            email="seed-bootstrap-1111111111@example.com",
            hashed_password="not-used",
            email_verified=False,
        )
        session.add_all([bystander_org, bystander])
        session.flush()
        session.add(
            Membership(
                user_id=bystander.id,
                organization_id=bystander_org.id,
                role=MembershipRole.OWNER,
            )
        )
        session.commit()
        bystander_id = bystander.id

    with demo_client as client:
        token = _register(client, "attacker@test.example", "Attacker Org")
        refused = client.post(
            "/demo/seed",
            headers={"Authorization": f"Bearer {token}"},
            json={"password": "AttackerChosen2026!"},
        )
        assert refused.status_code == 403, refused.text

        # The demo password is unchanged and other tenants' rows are untouched.
        assert (
            client.post(
                "/auth/login",
                json={"email": DEMO_EMAIL, "password": "AttackerChosen2026!"},
            ).status_code
            == 401
        )
        demo_login = client.post(
            "/auth/login", json={"email": DEMO_EMAIL, "password": "DemoPaper2026!"}
        )
        assert demo_login.status_code == 200, demo_login.text
        demo_token = demo_login.json()["tokens"]["access_token"]

        reseed = client.post("/demo/seed", headers={"Authorization": f"Bearer {demo_token}"})
        assert reseed.status_code == 200, reseed.text

    with factory() as session:
        assert session.get(User, bystander_id) is not None


def test_demo_seed_endpoint_bootstrap_only_removes_caller(
    demo_client: TestClient,
    demo_seed_env: tuple[sessionmaker[Session], Settings],
) -> None:
    """H6: API bootstrap cleanup never removes other tenants' bootstrap accounts."""
    factory, _settings = demo_seed_env
    with factory() as session:
        other_org = Organization(id=uuid.uuid4(), name="Other Bootstrap")
        other = User(
            id=uuid.uuid4(),
            email="seed-bootstrap-2222222222@example.com",
            hashed_password="not-used",
            email_verified=False,
        )
        session.add_all([other_org, other])
        session.flush()
        session.add(
            Membership(user_id=other.id, organization_id=other_org.id, role=MembershipRole.OWNER)
        )
        session.commit()
        other_id = other.id

    with demo_client as client:
        token = _register(client, "demo-seed-bootstrap-3333333333@example.com", "Boot Org")
        seeded = client.post("/demo/seed", headers={"Authorization": f"Bearer {token}"})
        assert seeded.status_code == 200, seeded.text

    with factory() as session:
        # Another tenant's bootstrap-looking account is never touched from the API
        # (removing the caller's own account is best-effort).
        assert session.get(User, other_id) is not None
        assert session.get(Organization, other_org.id) is not None


def test_resolve_demo_password_ignores_explicit_outside_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H6: a body-supplied password is never used on staging."""
    from app.core.errors import ValidationAppError
    from app.services.demo_seed_service import resolve_demo_password

    staging = Settings.model_construct(environment=Environment.STAGING)
    monkeypatch.delenv("DEMO_SEED_PASSWORD", raising=False)
    with pytest.raises(ValidationAppError, match="DEMO_SEED_PASSWORD"):
        resolve_demo_password(staging, explicit="AttackerChosen2026!")
    monkeypatch.setenv("DEMO_SEED_PASSWORD", "ServerSideDemo2026!")
    assert resolve_demo_password(staging, explicit="AttackerChosen2026!") == "ServerSideDemo2026!"
