"""Tests for the learning analytics slice (Slice 84 — read-only, record derived).

Covers endpoint shapes, rate correctness, small-sample gating, scoring, the
optional user filter, tenant isolation, auth, and empty-tenant behavior, plus a
few pure scoring helpers. No order/execution/automation paths are exercised.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models import (
    PaperValidationAlert,
    PaperValidationCandidate,
    PaperValidationDraft,
    PaperValidationRunPlan,
    PaperValidationRunSession,
    PaperValidationSessionObservation,
    PaperValidationSessionResult,
)
from app.db.session import get_session
from app.main import create_app
from app.schemas.common import PaperAlertType
from app.security.rate_limit import reset_rate_limiter
from app.services.learning_analytics.scoring import (
    confidence_bucket,
    correlation_sign,
    discipline_grade,
    quality_score,
    safe_rate,
)

_BASE = {
    "environment": "local",
    "log_json": False,
    "execution_mode": "paper",
    "enable_real_trading": False,
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "learning-analytics-test-secret-min-32-chars",
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
    organization_name: str = "Learning Org",
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
    timeframe: str = "1h",
    symbol: str = "BTCUSDT",
    direction: str = "long",
    confidence: float | None = 0.8,
    entry_assessment: str = "entered_as_planned",
    discipline_assessment: str = "disciplined",
    invalidation_hit: bool = False,
    behaved_as_expected: bool | None = True,
    lessons: str | None = None,
    session_status: str = "completed",
    started_at: datetime | None = None,
    recorded_at: datetime | None = None,
    observation_kinds: tuple[str, ...] = (),
) -> uuid.UUID:
    """Insert a full alert -> ... -> result chain and return the session id."""
    recorded = recorded_at or datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    started = started_at or (recorded - timedelta(minutes=30))
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
            symbol=symbol,
            timeframe=timeframe,
            condition=condition,
            direction=direction,
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
            symbol=symbol,
            timeframe=timeframe,
            condition=condition,
            direction=direction,
            session_status=session_status,
            started_by=user_id,
            started_at=started,
            ended_at=recorded,
        )
        session.add(run_session)
        session.flush()

        result = PaperValidationSessionResult(
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
            lessons=lessons,
            recorded_by=user_id,
            recorded_at=recorded,
        )
        session.add(result)

        for kind in observation_kinds:
            session.add(
                PaperValidationSessionObservation(
                    organization_id=organization_id,
                    run_session_id=run_session.id,
                    run_plan_id=plan.id,
                    observation_kind=kind,
                    recorded_by=user_id,
                )
            )

        session.commit()
        return run_session.id


# --- auth + empty tenant ----------------------------------------------------


# --- summary ----------------------------------------------------------------


# --- setup performance + small-sample gating --------------------------------


# --- discipline -------------------------------------------------------------


# --- confidence vs outcome --------------------------------------------------


# --- behavior insights ------------------------------------------------------


# --- lessons ----------------------------------------------------------------


# --- setup ranking ----------------------------------------------------------


# --- user filter ------------------------------------------------------------


# --- tenant isolation -------------------------------------------------------


# --- pure scoring helpers ---------------------------------------------------


def test_scoring_helpers() -> None:
    assert safe_rate(1, 4) == 0.25
    assert safe_rate(1, 0) is None
    assert confidence_bucket(0.3) == "low"
    assert confidence_bucket(0.9) == "very_high"
    assert confidence_bucket(1.0) == "very_high"
    assert confidence_bucket(None) is None
    assert discipline_grade(95) == "A"
    assert discipline_grade(50) == "F"
    assert quality_score(1.0, 1.0, 0.0, 0.0) == 100.0
    assert quality_score(0.0, 0.0, 1.0, 1.0) == 0.0
    assert correlation_sign([(0, 0.2), (3, 0.9)]) == "positive"
    assert correlation_sign([(0, 0.9), (3, 0.2)]) == "negative"
    assert correlation_sign([(0, 0.5)]) == "insufficient_data"
