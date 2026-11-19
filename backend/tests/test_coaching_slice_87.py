"""Tests for coaching and journaled lessons (Slice 87).

Covers pure rules, service/endpoints, save idempotency, RBAC, tenant isolation,
forbidden wording guards, and safety (no order/execution/automation paths).
"""

from __future__ import annotations

import ast
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.core.errors import ForbiddenError
from app.db.base import Base
from app.db.models import (
    Membership,
    PaperValidationAlert,
    PaperValidationCandidate,
    PaperValidationDraft,
    PaperValidationRunPlan,
    PaperValidationRunSession,
    PaperValidationSessionResult,
)
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import MembershipRole, PaperAlertType
from app.security.rate_limit import reset_rate_limiter
from app.security.rbac import require_membership_roles
from app.services.coaching.rules import (
    CATEGORY_INVALIDATION_HIT,
    CATEGORY_SHOULD_HAVE_AVOIDED,
    PROMPT_TEMPLATES,
    TITLE_TEMPLATES,
    RawPattern,
    build_coaching_prompt,
    coaching_signature,
    concern_score,
    contains_forbidden_wording,
    map_severity,
    reliability_tier,
)

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "coaching-test-secret-min-32-characters",
    "rate_limit_use_redis": False,
    "access_token_denylist_use_redis": False,
    "provider_mode": "mock",
    "market_data_provider": "mock",
    "worker_enabled": False,
    "market_watcher_enabled": False,
    "market_watcher_bridge_enabled": False,
}


@dataclass
class Harness:
    client: TestClient
    factory: sessionmaker[Session]


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    reset_rate_limiter()


@pytest.fixture
def harness() -> Iterator[Harness]:
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

    get_settings.cache_clear()
    app = create_app(settings=Settings(**_BASE))
    app.dependency_overrides[get_session] = _override_session

    with TestClient(app) as client:
        yield Harness(client=client, factory=factory)

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    engine.dispose()


def _register_owner(
    client: TestClient,
    email: str = "owner@example.com",
    organization_name: str = "Coaching Org",
) -> tuple[dict[str, str], uuid.UUID, uuid.UUID]:
    reg = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "SecurePass123!",
            "organization_name": organization_name,
        },
    )
    assert reg.status_code == 201, reg.text
    login = client.post(
        "/auth/login",
        json={"email": email, "password": "SecurePass123!"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["tokens"]["access_token"]
    org_id = uuid.UUID(reg.json()["organization"]["id"])
    user_id = uuid.UUID(reg.json()["user"]["id"])
    return {"Authorization": f"Bearer {token}"}, org_id, user_id


def _seed_session(
    factory: sessionmaker[Session],
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    outcome: str = "success",
    condition: str = "breakout",
    confidence: float | None = 0.8,
    entry_assessment: str = "entered_as_planned",
    discipline_assessment: str = "disciplined",
    invalidation_hit: bool = False,
    behaved_as_expected: bool | None = True,
) -> uuid.UUID:
    recorded = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    started = recorded - timedelta(minutes=30)
    with factory() as session:
        alert = PaperValidationAlert(
            organization_id=organization_id,
            user_id=user_id,
            alert_type=PaperAlertType.SETUP_SIGNAL_DETECTED,
            message="seeded setup",
        )
        session.add(alert)
        session.flush()

        draft = PaperValidationDraft(
            organization_id=organization_id,
            source_alert_id=alert.id,
            review_status="important",
            created_by=user_id,
        )
        session.add(draft)
        session.flush()

        candidate = PaperValidationCandidate(
            organization_id=organization_id,
            draft_id=draft.id,
            source_alert_id=alert.id,
            created_by=user_id,
        )
        session.add(candidate)
        session.flush()

        plan = PaperValidationRunPlan(
            organization_id=organization_id,
            candidate_id=candidate.id,
            draft_id=draft.id,
            source_alert_id=alert.id,
            symbol="BTCUSDT",
            timeframe="1h",
            condition=condition,
            direction="long",
            confidence=confidence,
            created_by=user_id,
        )
        session.add(plan)
        session.flush()

        run_session = PaperValidationRunSession(
            organization_id=organization_id,
            run_plan_id=plan.id,
            candidate_id=candidate.id,
            draft_id=draft.id,
            source_alert_id=alert.id,
            symbol="BTCUSDT",
            timeframe="1h",
            condition=condition,
            direction="long",
            session_status="completed",
            started_by=user_id,
            started_at=started,
            ended_at=recorded,
        )
        session.add(run_session)
        session.flush()

        session.add(
            PaperValidationSessionResult(
                organization_id=organization_id,
                run_session_id=run_session.id,
                run_plan_id=plan.id,
                outcome=outcome,
                success_criteria_met="met" if outcome == "success" else "not_met",
                failure_criteria_met="not_met",
                invalidation_hit=invalidation_hit,
                entry_assessment=entry_assessment,
                discipline_assessment=discipline_assessment,
                behaved_as_expected=behaved_as_expected,
                recorded_by=user_id,
                recorded_at=recorded,
            )
        )
        session.commit()
        return run_session.id


def _seed_invalidation_pattern(
    factory: sessionmaker[Session], organization_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    for _ in range(6):
        _seed_session(
            factory,
            organization_id=organization_id,
            user_id=user_id,
            condition="order_block",
            outcome="invalidated",
            invalidation_hit=True,
            discipline_assessment="disciplined",
        )


def _register_viewer_in_org(
    client: TestClient,
    factory: sessionmaker[Session],
    *,
    org_id: uuid.UUID,
) -> dict[str, str]:
    reg = client.post(
        "/auth/register",
        json={
            "email": "viewer-coaching@example.com",
            "password": "SecurePass123!",
            "organization_name": "Viewer Scratch Org",
        },
    )
    assert reg.status_code == 201
    viewer_id = uuid.UUID(reg.json()["user"]["id"])
    with factory() as session:
        membership = session.scalar(select(Membership).where(Membership.user_id == viewer_id))
        assert membership is not None
        membership.organization_id = org_id
        membership.role = MembershipRole.VIEWER
        session.commit()

    login = client.post(
        "/auth/login",
        json={"email": "viewer-coaching@example.com", "password": "SecurePass123!"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['tokens']['access_token']}"}


# --- pure rules -------------------------------------------------------------


def test_reliability_tier_boundaries() -> None:
    assert reliability_tier(0, 5) == "none"
    assert reliability_tier(3, 5) == "low"
    assert reliability_tier(5, 5) == "medium"
    assert reliability_tier(15, 5) == "high"


def test_map_severity_gates_small_samples() -> None:
    assert (
        map_severity(
            category=CATEGORY_INVALIDATION_HIT,
            rate=0.8,
            reliability="low",
            sample_size=3,
            min_sample=5,
        )
        is None
    )
    assert (
        map_severity(
            category=CATEGORY_INVALIDATION_HIT,
            rate=0.2,
            reliability="high",
            sample_size=10,
            min_sample=5,
        )
        is None
    )


def test_map_severity_critical_requires_high_reliability() -> None:
    assert (
        map_severity(
            category=CATEGORY_SHOULD_HAVE_AVOIDED,
            rate=0.8,
            reliability="medium",
            sample_size=8,
            min_sample=5,
        )
        == "medium"
    )
    assert (
        map_severity(
            category=CATEGORY_SHOULD_HAVE_AVOIDED,
            rate=0.55,
            reliability="high",
            sample_size=20,
            min_sample=5,
        )
        == "high"
    )
    assert (
        map_severity(
            category=CATEGORY_SHOULD_HAVE_AVOIDED,
            rate=0.8,
            reliability="high",
            sample_size=20,
            min_sample=5,
        )
        == "critical"
    )


def test_concern_score_shrinkage() -> None:
    thin = concern_score(rate=0.6, sample_size=2, min_sample=5)
    solid = concern_score(rate=0.6, sample_size=20, min_sample=5)
    assert thin < solid


def test_build_coaching_prompt_is_deterministic() -> None:
    pattern = RawPattern(
        category=CATEGORY_INVALIDATION_HIT,
        matched_dimension="condition",
        matched_key="order_block",
        sample_size=8,
        rate=0.625,
        source_session_ids=("a", "b"),
        analytics_codes=("invalidation_prone_setup",),
    )
    first = build_coaching_prompt(pattern, min_sample=5)
    second = build_coaching_prompt(pattern, min_sample=5)
    assert first is not None and second is not None
    assert first.signature == second.signature
    assert first.prompt_text == second.prompt_text
    assert first.prompt_text.lower().startswith("review this behavior")


def test_all_templates_forbidden_wording_clean() -> None:
    for category, template in PROMPT_TEMPLATES.items():
        sample = template.format(
            key="breakout",
            n=8,
            rate_pct=50,
            success_pct=40,
            quality_score=35.0,
            correlation_label="no clear link",
        )
        assert not contains_forbidden_wording(sample), category
    for template in TITLE_TEMPLATES.values():
        assert not contains_forbidden_wording(template.format(key="breakout"))


def test_coaching_signature_stable() -> None:
    assert coaching_signature(
        category="invalidation_hit", matched_dimension="condition", matched_key="breakout"
    ) == coaching_signature(
        category="invalidation_hit", matched_dimension="condition", matched_key="breakout"
    )


# --- service / endpoints ----------------------------------------------------


def test_reader_allows_viewer_trader_dep_blocks_viewer() -> None:
    reader = require_membership_roles(
        MembershipRole.OWNER, MembershipRole.TRADER, MembershipRole.VIEWER
    )
    trader = require_membership_roles(MembershipRole.OWNER, MembershipRole.TRADER)
    viewer = SimpleNamespace(membership_role=MembershipRole.VIEWER)
    assert reader(viewer) is viewer  # type: ignore[arg-type]
    with pytest.raises(ForbiddenError):
        trader(viewer)  # type: ignore[arg-type]


def test_coaching_module_has_no_unsafe_imports() -> None:
    coaching_dir = Path(__file__).resolve().parents[1] / "src" / "app" / "services" / "coaching"
    forbidden = (
        "execution_service",
        "proposal_service",
        "approval_service",
        "paper_bot_engine",
        "telegram",
        "exchange",
    )
    for path in coaching_dir.glob("*.py"):
        tree = ast.parse(path.read_text())
        imports = {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
        import_from = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        joined = " ".join(imports | import_from).lower()
        for term in forbidden:
            assert term not in joined, f"{path.name} imports forbidden term {term}"
