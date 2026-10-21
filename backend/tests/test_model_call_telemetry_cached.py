"""Model call telemetry metadata for cached input tokens."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.persistence_firewall import install_persistence_firewall
from app.db.base import Base
from app.db.models import Organization, User
from app.providers.llm import LLMCompletionRequest, LLMCompletionResult, LLMMessage, MockLLMProvider
from app.schemas.common import CostSource, UsageStatus
from app.schemas.model_routing import (
    ModelCallAttempt,
    ModelContextScope,
    ModelFailureCategory,
    ModelFallbackPolicy,
    ModelRoutingPurpose,
    ModelRoutingTier,
    ModelTaskRequest,
)
from app.services.model_call_telemetry import ModelCallTelemetryService
from app.services.model_router import ModelRouter
from app.services.usage_service import UsageService

ORG = uuid.UUID("00000000-0000-0000-0000-00000000c001")
USER = uuid.UUID("00000000-0000-0000-0000-00000000c002")


@pytest.fixture
def session() -> Iterator[Session]:
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
    install_persistence_firewall()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all(
            [
                Organization(id=ORG, name="Cached Org"),
                User(id=USER, email="cached@example.com", hashed_password="x"),
            ]
        )
        db.commit()
        yield db
    Base.metadata.drop_all(engine)
    engine.dispose()


class _CachedTokenLLM:
    name = "cached-fixture-llm"
    kind = MockLLMProvider().kind

    def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
        return LLMCompletionResult(
            content='{"summary":"ok"}',
            model=request.model,
            provider=self.name,
            input_tokens=100,
            output_tokens=10,
            reasoning_tokens=3,
            cached_input_tokens=42,
            latency_ms=1.0,
        )

    def status(self) -> object:
        return None


def test_router_attempt_carries_cached_input_tokens(session: Session) -> None:
    usage = UsageService(session)
    telemetry = ModelCallTelemetryService(session, usage)
    router = ModelRouter(
        _CachedTokenLLM(),  # type: ignore[arg-type]
        tier_a_model="gpt-6.1-sol",
        tier_b_model="gpt-6-luna",
        telemetry=telemetry,
    )
    result = router.complete(
        ModelTaskRequest(
            purpose=ModelRoutingPurpose.NARRATIVE_SYNTHESIS,
            context=ModelContextScope(
                organization_id=ORG,
                user_id=USER,
                purpose=ModelRoutingPurpose.NARRATIVE_SYNTHESIS,
            ),
            correlation_id="cached-meta-corr",
            caller_organization_id=ORG,
            caller_user_id=USER,
        ),
        [LLMMessage(role="user", content="facts")],
    )
    assert result.attempts[0].cached_input_tokens == 42


def test_record_attempt_passes_cached_input_tokens_to_metadata(session: Session) -> None:
    usage = UsageService(session)
    telemetry = ModelCallTelemetryService(session, usage)
    now = datetime.now(UTC)
    task_id = uuid.uuid4()
    attempt = ModelCallAttempt(
        attempt_id=uuid.uuid4(),
        task_request_id=task_id,
        correlation_id="direct-cached",
        purpose=ModelRoutingPurpose.INTENT_CLASSIFICATION,
        tier=ModelRoutingTier.TIER_B,
        provider="mock",
        requested_model="gpt-6-luna",
        resolved_model="gpt-6-luna",
        policy_version="model-router/v1",
        fallback_policy=ModelFallbackPolicy.DETERMINISTIC_FACTS,
        input_tokens=50,
        output_tokens=5,
        reasoning_tokens=1,
        cached_input_tokens=17,
        success=True,
        failure_category=ModelFailureCategory.NONE,
        status=UsageStatus.SUCCESS,
        estimated_cost=0,
        cost_source=CostSource.STATIC_ESTIMATED,
        organization_id=ORG,
        user_id=USER,
        started_at=now,
        completed_at=now,
    )
    with patch("app.services.model_call_telemetry.build_provider_metadata") as mock_meta:
        mock_meta.return_value = {}
        telemetry.record(attempt, feature="agent_chat")
        mock_meta.assert_called_once()
        assert mock_meta.call_args.kwargs["cached_input_tokens"] == 17
