"""Phase 2 — ModelRouter, least privilege, fallback, and actual-call telemetry."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.errors import PersistencePolicyError, ServiceUnavailableError
from app.core.operation_policy import operation_scope
from app.core.persistence_firewall import install_persistence_firewall
from app.db.base import Base
from app.db.models import JournalTrade, ModelCallAttempt, Organization, UsageEvent, User
from app.providers.llm import LLMCompletionRequest, LLMCompletionResult, LLMMessage, MockLLMProvider
from app.repositories.base import SQLAlchemyRepository
from app.schemas.agent import Intent, IntentDecision, OperationClass, PrincipalRef, RequestedAction
from app.schemas.common import CostSource, JournalTradeSource, TradeDirection, UsageStatus
from app.schemas.model_routing import (
    ModelCallAttempt as RoutedAttempt,
)
from app.schemas.model_routing import (
    ModelCallerScope,
    ModelContextScope,
    ModelFailureCategory,
    ModelFallbackPolicy,
    ModelResourceType,
    ModelRetentionCategory,
    ModelRoutingPurpose,
    ModelRoutingTier,
    ModelTaskRequest,
)
from app.services.model_call_telemetry import ModelCallTelemetryService, resolve_model_call_cost
from app.services.model_router import (
    CrossTenantModelContextError,
    ModelRouter,
    ModelRoutingError,
)
from app.services.usage_service import UsageService

ORG_A = uuid.UUID("00000000-0000-0000-0000-00000000a001")
ORG_B = uuid.UUID("00000000-0000-0000-0000-00000000b001")
USER_A = uuid.UUID("00000000-0000-0000-0000-00000000a002")
USER_B = uuid.UUID("00000000-0000-0000-0000-00000000b002")
ACCOUNT_A = uuid.UUID("00000000-0000-0000-0000-00000000a003")
ACCOUNT_B = uuid.UUID("00000000-0000-0000-0000-00000000a004")
STRATEGY_A = uuid.UUID("00000000-0000-0000-0000-00000000a005")
STRATEGY_B = uuid.UUID("00000000-0000-0000-0000-00000000a006")
JOURNAL_A = uuid.UUID("00000000-0000-0000-0000-00000000a007")
JOURNAL_B = uuid.UUID("00000000-0000-0000-0000-00000000a008")


class _FailingLLM:
    name = "failing-llm"
    kind = MockLLMProvider().kind

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._error = error or ServiceUnavailableError(
            "LLM provider is unavailable.",
            details={"reason": "openai_llm_unavailable"},
        )

    def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
        self.calls.append(request.model)
        raise self._error

    def status(self) -> object:
        return None


class _SelectiveFailLLM:
    """Fails for Tier A model, succeeds for Tier B model."""

    name = "selective-llm"
    kind = MockLLMProvider().kind

    def __init__(self, *, fail_model: str) -> None:
        self.fail_model = fail_model
        self.calls: list[str] = []

    def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
        self.calls.append(request.model)
        if request.model == self.fail_model:
            raise ServiceUnavailableError(
                "LLM provider is unavailable.",
                details={"reason": "openai_llm_timeout"},
            )
        return LLMCompletionResult(
            content='{"summary":"ok"}',
            model=request.model,
            provider=self.name,
            input_tokens=12,
            output_tokens=4,
            latency_ms=7.5,
            fallback_used=False,
            parsed_json={"summary": "ok"},
        )


class _JournalRepo(SQLAlchemyRepository[JournalTrade]):
    model = JournalTrade


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
    with factory() as sess:
        sess.add_all(
            [
                Organization(id=ORG_A, name="Org A"),
                Organization(id=ORG_B, name="Org B"),
                User(id=USER_A, email="a@example.com", hashed_password="x"),
                User(id=USER_B, email="b@example.com", hashed_password="x"),
            ]
        )
        sess.commit()
        yield sess
    Base.metadata.drop_all(engine)
    engine.dispose()


def _scope(
    purpose: ModelRoutingPurpose,
    *,
    org: uuid.UUID | None = ORG_A,
    user: uuid.UUID | None = USER_A,
    account: uuid.UUID | None = None,
    resource_type: ModelResourceType = ModelResourceType.CONVERSATION,
    resource_id: uuid.UUID | None = None,
) -> ModelContextScope:
    return ModelContextScope(
        organization_id=org,
        user_id=user,
        account_id=account,
        purpose=purpose,
        resource_type=resource_type,
        resource_id=resource_id,
        retention_category=ModelRetentionCategory.STANDARD,
    )


def _request(
    purpose: ModelRoutingPurpose,
    *,
    override: str | None = None,
    org: uuid.UUID | None = ORG_A,
    caller_org: uuid.UUID | None = ORG_A,
    user: uuid.UUID | None = USER_A,
    caller_user: uuid.UUID | None = USER_A,
    account: uuid.UUID | None = None,
    caller_account: uuid.UUID | None = None,
    resource_type: ModelResourceType = ModelResourceType.CONVERSATION,
    resource_id: uuid.UUID | None = None,
    caller_resource_type: ModelResourceType | None = None,
    caller_resource_id: uuid.UUID | None = None,
    caller_scope: ModelCallerScope = ModelCallerScope.USER,
    correlation_id: str = "corr-1",
) -> ModelTaskRequest:
    return ModelTaskRequest(
        purpose=purpose,
        context=_scope(
            purpose,
            org=org,
            user=user,
            account=account,
            resource_type=resource_type,
            resource_id=resource_id,
        ),
        correlation_id=correlation_id,
        caller_organization_id=caller_org,
        caller_user_id=caller_user,
        caller_scope=caller_scope,
        caller_account_id=caller_account,
        caller_resource_type=caller_resource_type,
        caller_resource_id=caller_resource_id,
        model_override=override,
    )


def _messages() -> list[LLMMessage]:
    return [LLMMessage(role="user", content="Summarize the frozen setup facts.")]


def _router(
    provider: object | None = None,
    *,
    fail_closed: bool = False,
    telemetry: ModelCallTelemetryService | None = None,
) -> ModelRouter:
    return ModelRouter(
        provider or MockLLMProvider(),  # type: ignore[arg-type]
        tier_a_model="gpt-4o",
        tier_b_model="gpt-4o-mini",
        fail_closed=fail_closed,
        telemetry=telemetry,
    )


def test_routing_by_purpose_assigns_tiers() -> None:
    router = _router()
    a = router.decide(_request(ModelRoutingPurpose.STRATEGY_REVIEW))
    b = router.decide(_request(ModelRoutingPurpose.INTENT_CLASSIFICATION))
    assert a.selected_tier is ModelRoutingTier.TIER_A
    assert a.selected_model == "gpt-4o"
    assert b.selected_tier is ModelRoutingTier.TIER_B
    assert b.selected_model == "gpt-4o-mini"
    assert a.policy_version == "model-router/v1"
    assert a.token_budget == 4096
    assert b.token_budget == 1024


def test_tier_a_selection() -> None:
    router = _router()
    decision = router.decide(_request(ModelRoutingPurpose.EVIDENCE_EXPLANATION))
    assert decision.selected_tier is ModelRoutingTier.TIER_A
    assert decision.selected_model == "gpt-4o"
    assert decision.retention_category is ModelRetentionCategory.AUDIT_REQUIRED


def test_agent_conversation_uses_configured_reasoning_tier() -> None:
    router = ModelRouter(MockLLMProvider(), tier_a_model="gpt-6.1-sol", tier_b_model="gpt-4o-mini")
    decision = router.decide(_request(ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS))
    assert decision.selected_tier is ModelRoutingTier.TIER_A
    assert decision.selected_model == "gpt-6.1-sol"
    assert decision.token_budget == 4096
    assert decision.latency_budget_ms == 60_000


def test_tier_b_selection() -> None:
    router = _router()
    decision = router.decide(_request(ModelRoutingPurpose.NARRATIVE_SYNTHESIS))
    assert decision.selected_tier is ModelRoutingTier.TIER_B
    assert decision.selected_model == "gpt-4o-mini"


def test_unauthorized_model_override_rejected() -> None:
    router = _router()
    with pytest.raises(ModelRoutingError, match="Unauthorized model override"):
        router.decide(_request(ModelRoutingPurpose.INTENT_CLASSIFICATION, override="gpt-4o"))


def test_tier_a_may_request_explicit_lower_fallback() -> None:
    router = _router()
    decision = router.decide(_request(ModelRoutingPurpose.STRATEGY_REVIEW, override="gpt-4o-mini"))
    assert decision.selected_model == "gpt-4o-mini"
    assert decision.selected_tier is ModelRoutingTier.TIER_A


def test_provider_fallback_uses_lower_model() -> None:
    provider = _SelectiveFailLLM(fail_model="gpt-4o")
    router = _router(provider)
    result = router.complete(_request(ModelRoutingPurpose.STRATEGY_REVIEW), _messages())
    assert result.fallback_used is True
    assert result.unavailable is False
    assert result.mutation_allowed is False
    assert provider.calls == ["gpt-4o", "gpt-4o-mini"]
    assert result.resolved_model == "gpt-4o-mini"
    assert any(not row.success for row in result.attempts)
    assert any(row.success for row in result.attempts)


def test_fail_closed_mode_returns_unavailable() -> None:
    provider = _FailingLLM()
    router = _router(provider, fail_closed=True)
    result = router.complete(_request(ModelRoutingPurpose.NARRATIVE_SYNTHESIS), _messages())
    assert result.unavailable is True
    assert result.mutation_allowed is False
    assert result.failure_category is ModelFailureCategory.PROVIDER_UNAVAILABLE
    assert provider.calls == ["gpt-4o-mini"]
    assert result.deterministic_facts is not None
    assert result.deterministic_facts["mutation_allowed"] is False


def test_open_fallback_returns_deterministic_facts() -> None:
    provider = _FailingLLM()
    router = _router(provider, fail_closed=False)
    result = router.complete(_request(ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS), _messages())
    assert result.unavailable is False
    assert result.fallback_used is True
    assert result.deterministic_facts is not None
    assert result.deterministic_facts["tier_c_authority"] is True
    assert result.mutation_allowed is False
    assert result.content == ""


def test_telemetry_success(session: Session) -> None:
    usage = UsageService(session)
    telemetry = ModelCallTelemetryService(session, usage)
    router = _router(telemetry=telemetry)
    result = router.complete(
        _request(ModelRoutingPurpose.NARRATIVE_SYNTHESIS, correlation_id="corr-ok"),
        _messages(),
    )
    assert result.unavailable is False
    rows = list(session.scalars(select(ModelCallAttempt)).all())
    assert len(rows) == 1
    row = rows[0]
    assert row.success is True
    assert row.purpose.value == "narrative_synthesis"
    assert row.tier.value == "tier_b"
    assert row.requested_model == "gpt-4o-mini"
    assert row.resolved_model
    assert row.input_tokens >= 1
    assert row.latency_ms is not None
    assert row.correlation_id == "corr-ok"
    assert row.organization_id == ORG_A
    assert row.mutation_allowed is False
    usage_rows = list(session.scalars(select(UsageEvent)).all())
    assert len(usage_rows) == 1
    assert usage_rows[0].feature == "agent_narrative"
    assert usage_rows[0].input_tokens == row.input_tokens


def test_telemetry_failure(session: Session) -> None:
    usage = UsageService(session)
    telemetry = ModelCallTelemetryService(session, usage)
    router = _router(_FailingLLM(), fail_closed=True, telemetry=telemetry)
    result = router.complete(_request(ModelRoutingPurpose.LESSON_SYNTHESIS), _messages())
    assert result.unavailable is True
    rows = list(session.scalars(select(ModelCallAttempt)).all())
    assert len(rows) == 1
    assert rows[0].success is False
    assert rows[0].failure_category.value == "provider_unavailable"
    assert rows[0].status is UsageStatus.FAILURE
    usage_rows = list(session.scalars(select(UsageEvent)).all())
    assert len(usage_rows) == 1
    assert usage_rows[0].status is UsageStatus.FAILURE


def test_token_counts_and_latency() -> None:
    provider = _SelectiveFailLLM(fail_model="never")
    result = _router(provider).complete(
        _request(ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS),
        _messages(),
    )
    assert result.input_tokens == 12
    assert result.output_tokens == 4
    assert result.total_latency_ms == 7.5
    assert result.attempts[0].latency_ms == 7.5


def test_cost_unavailable_for_unknown_model() -> None:
    cost = resolve_model_call_cost(model="mystery-model", input_tokens=100, output_tokens=20)
    assert cost.cost_source is CostSource.UNAVAILABLE
    assert cost.estimated_cost == Decimal("0")


def test_cost_static_estimated_for_known_model() -> None:
    cost = resolve_model_call_cost(model="gpt-4o-mini", input_tokens=1_000_000, output_tokens=0)
    assert cost.cost_source is CostSource.STATIC_ESTIMATED
    assert cost.estimated_cost > Decimal("0")


def test_cross_tenant_context_rejected() -> None:
    router = _router()
    with pytest.raises(CrossTenantModelContextError):
        router.decide(
            _request(
                ModelRoutingPurpose.STRATEGY_REVIEW,
                org=ORG_B,
                caller_org=ORG_A,
            )
        )


def test_fallback_cannot_mutate_domain_state(session: Session) -> None:
    before = session.scalar(select(JournalTrade).limit(1))
    assert before is None
    result = _router(_FailingLLM(), fail_closed=False).complete(
        _request(ModelRoutingPurpose.NARRATIVE_SYNTHESIS),
        _messages(),
    )
    assert result.mutation_allowed is False
    assert session.scalars(select(JournalTrade)).all() == []


def test_readonly_remains_readonly_during_model_failure(session: Session) -> None:
    usage = UsageService(session)
    telemetry = ModelCallTelemetryService(session, usage)
    router = _router(_FailingLLM(), fail_closed=True, telemetry=telemetry)
    decision = IntentDecision(
        intent=Intent.MARKET_ANALYSIS,
        operation_class=OperationClass.READ_ONLY,
        organization_id=ORG_A,
        principal=PrincipalRef(user_id=USER_A),
        requested_action=RequestedAction.NONE,
    )
    with operation_scope(decision):
        result = router.complete(_request(ModelRoutingPurpose.NARRATIVE_SYNTHESIS), _messages())
        assert result.unavailable is True
        attempts = list(session.scalars(select(ModelCallAttempt)).all())
        assert len(attempts) == 1
        with pytest.raises(PersistencePolicyError, match="READ_ONLY"):
            _JournalRepo(session).add(
                JournalTrade(
                    organization_id=ORG_A,
                    user_id=USER_A,
                    source=JournalTradeSource.MANUAL,
                    symbol="BTCUSDT",
                    timeframe="15m",
                    direction=TradeDirection.SHORT,
                )
            )


def test_secret_in_prompt_rejected() -> None:
    router = _router()
    with pytest.raises(ModelRoutingError, match="secrets"):
        router.complete(
            _request(ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS),
            [
                LLMMessage(
                    role="user",
                    content="Use OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz1234",
                )
            ],
        )


def test_usage_tracking_does_not_call_llm() -> None:
    from app.agents.nodes import usage_tracking
    from app.agents.runtime import AgentRuntime
    from app.core.config import Settings
    from app.services.risk_service import RiskService
    from app.services.strategy_service import StrategyService
    from app.strategies.registry import build_default_registry
    from app.tools.registry import build_default_registry as build_tools

    provider = _FailingLLM()
    settings = Settings(log_json=False, provider_mode="mock")
    runtime = AgentRuntime(
        settings=settings,
        risk_service=RiskService(),
        strategy_service=StrategyService(registry=build_default_registry()),
        tool_registry=build_tools(settings),
        llm_provider=provider,  # type: ignore[arg-type]
    )
    state = {
        "message": "analyze eth trend",
        "request_id": "no-meter-call",
        "organization_id": str(ORG_A),
        "user_id": str(USER_A),
        "tool_outputs": [],
        "tool_calls": [],
        "audit_events": [],
        "citations": [],
    }
    out = usage_tracking(state, runtime)
    assert provider.calls == []
    meta = out["usage_metadata"]
    assert meta["feature"] == "capacity_estimate"
    assert meta["provider"] == "none"
    assert meta["model"] == "none"
    assert meta["input_tokens"] > 0
    assert meta["cost_source"] == CostSource.UNAVAILABLE.value


def test_fallback_attempts_record_actual_tiers() -> None:
    provider = _SelectiveFailLLM(fail_model="gpt-4o")
    result = _router(provider).complete(_request(ModelRoutingPurpose.STRATEGY_REVIEW), _messages())
    assert [row.tier for row in result.attempts] == [
        ModelRoutingTier.TIER_A,
        ModelRoutingTier.TIER_B,
    ]
    assert [row.requested_model for row in result.attempts] == ["gpt-4o", "gpt-4o-mini"]
    assert result.attempts[0].success is False
    assert result.attempts[1].success is True
    assert result.decision.selected_tier is ModelRoutingTier.TIER_A


def test_one_successful_call_is_one_usage_event(session: Session) -> None:
    usage = UsageService(session)
    telemetry = ModelCallTelemetryService(session, usage)
    router = _router(telemetry=telemetry)
    result = router.complete(
        _request(ModelRoutingPurpose.NARRATIVE_SYNTHESIS, correlation_id="once-1"),
        _messages(),
    )
    session.commit()
    assert result.telemetry_persisted is True
    assert session.scalar(select(func.count()).select_from(ModelCallAttempt)) == 1
    assert session.scalar(select(func.count()).select_from(UsageEvent)) == 1


def test_tier_a_fail_plus_tier_b_fallback_is_two_attempts(session: Session) -> None:
    usage = UsageService(session)
    telemetry = ModelCallTelemetryService(session, usage)
    router = _router(_SelectiveFailLLM(fail_model="gpt-4o"), telemetry=telemetry)
    result = router.complete(
        _request(ModelRoutingPurpose.STRATEGY_REVIEW, correlation_id="fb-2"),
        _messages(),
    )
    session.commit()
    attempts = list(session.scalars(select(ModelCallAttempt)).all())
    events = list(session.scalars(select(UsageEvent)).all())
    assert len(attempts) == 2
    assert len(events) == 2
    assert {row.tier.value for row in attempts} == {"tier_a", "tier_b"}
    assert sum(row.input_tokens for row in attempts) == sum(event.input_tokens for event in events)
    assert result.telemetry_persisted is True


def test_usage_tracking_replay_does_not_duplicate(session: Session) -> None:
    from app.agents.nodes import usage_tracking
    from app.agents.runtime import AgentRuntime
    from app.core.config import Settings
    from app.services.risk_service import RiskService
    from app.services.strategy_service import StrategyService
    from app.strategies.registry import build_default_registry
    from app.tools.registry import build_default_registry as build_tools

    usage = UsageService(session)
    telemetry = ModelCallTelemetryService(session, usage)
    router = _router(telemetry=telemetry)
    result = router.complete(
        _request(ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS, correlation_id="replay-usage"),
        _messages(),
    )
    session.commit()
    settings = Settings(log_json=False, provider_mode="mock")
    runtime = AgentRuntime(
        settings=settings,
        risk_service=RiskService(),
        strategy_service=StrategyService(registry=build_default_registry()),
        tool_registry=build_tools(settings),
        llm_provider=MockLLMProvider(),
        model_router=router,
    )
    state = {
        "message": "analyze eth trend",
        "request_id": "replay-usage",
        "organization_id": str(ORG_A),
        "user_id": str(USER_A),
        "tool_outputs": [],
        "tool_calls": [],
        "audit_events": [],
        "citations": [],
    }
    usage_tracking(state, runtime)
    usage_tracking(state, runtime)
    session.commit()
    assert result.telemetry_persisted is True
    assert session.scalar(select(func.count()).select_from(UsageEvent)) == 1
    assert session.scalar(select(func.count()).select_from(ModelCallAttempt)) == 1


def test_no_model_call_writes_zero_provider_usage(session: Session) -> None:
    from app.agents.nodes import usage_tracking
    from app.agents.runtime import AgentRuntime
    from app.core.config import Settings
    from app.services.risk_service import RiskService
    from app.services.strategy_service import StrategyService
    from app.strategies.registry import build_default_registry
    from app.tools.registry import build_default_registry as build_tools

    settings = Settings(log_json=False, provider_mode="mock")
    runtime = AgentRuntime(
        settings=settings,
        risk_service=RiskService(),
        strategy_service=StrategyService(registry=build_default_registry()),
        tool_registry=build_tools(settings),
        llm_provider=MockLLMProvider(),
    )
    state = {
        "message": "analyze eth trend",
        "request_id": "capacity-only",
        "organization_id": str(ORG_A),
        "user_id": str(USER_A),
        "tool_outputs": [],
        "tool_calls": [],
        "audit_events": [],
        "citations": [],
    }
    out = usage_tracking(state, runtime)
    session.commit()
    assert out["usage_metadata"]["feature"] == "capacity_estimate"
    assert session.scalar(select(func.count()).select_from(UsageEvent)) == 0
    assert session.scalar(select(func.count()).select_from(ModelCallAttempt)) == 0


def test_openai_routed_calls_do_not_hide_upstream_failure() -> None:
    from app.core.errors import ServiceUnavailableError
    from app.providers.llm import LLMCompletionRequest, OpenAILLMProvider

    provider = OpenAILLMProvider(api_key="", base_url="https://example.invalid", model="gpt-4o")
    with pytest.raises(ServiceUnavailableError):
        provider.complete(
            LLMCompletionRequest(
                messages=_messages(),
                model="gpt-4o",
                allow_internal_fallback=False,
            )
        )
    fallback = provider.complete(
        LLMCompletionRequest(
            messages=_messages(),
            model="gpt-4o",
            allow_internal_fallback=True,
        )
    )
    assert fallback.fallback_used is True


def test_telemetry_persistence_failure_is_surfaced(session: Session) -> None:
    from app.services.model_call_telemetry import ModelTelemetryPersistenceError

    class _BoomTelemetry(ModelCallTelemetryService):
        def record(self, attempt: RoutedAttempt, *, feature: str) -> RoutedAttempt:
            raise ModelTelemetryPersistenceError("injected failure", attempt=attempt)

    router = _router(telemetry=_BoomTelemetry(session))
    result = router.complete(
        _request(ModelRoutingPurpose.NARRATIVE_SYNTHESIS, correlation_id="boom"),
        _messages(),
    )
    assert result.failure_category is ModelFailureCategory.TELEMETRY_PERSISTENCE_FAILED
    assert result.telemetry_persisted is False
    assert result.content
    assert result.deterministic_facts is not None
    assert result.deterministic_facts["provider_io_occurred"] is True
    assert session.scalars(select(ModelCallAttempt)).all() == []


def test_isolated_telemetry_survives_outer_rollback(session: Session) -> None:
    usage = UsageService(session)
    telemetry = ModelCallTelemetryService(session, usage, isolated=True)
    router = _router(telemetry=telemetry)
    result = router.complete(
        _request(ModelRoutingPurpose.NARRATIVE_SYNTHESIS, correlation_id="outer-rb"),
        _messages(),
    )
    assert result.telemetry_persisted is True
    session.rollback()
    rows = list(session.scalars(select(ModelCallAttempt)).all())
    events = list(session.scalars(select(UsageEvent)).all())
    assert len(rows) == 1
    assert len(events) == 1


def test_retry_same_attempt_id_is_exactly_once(session: Session) -> None:
    usage = UsageService(session)
    telemetry = ModelCallTelemetryService(session, usage, isolated=False)
    attempt = RoutedAttempt(
        attempt_id=uuid.UUID("00000000-0000-0000-0000-00000000aa01"),
        task_request_id=uuid.uuid4(),
        correlation_id="exact-once",
        purpose=ModelRoutingPurpose.NARRATIVE_SYNTHESIS,
        tier=ModelRoutingTier.TIER_B,
        provider="mock-llm",
        requested_model="gpt-4o-mini",
        resolved_model="gpt-4o-mini",
        policy_version="model-router/v1",
        fallback_policy=ModelFallbackPolicy.DETERMINISTIC_FACTS,
        success=True,
        organization_id=ORG_A,
        user_id=USER_A,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        input_tokens=8,
        output_tokens=2,
    )
    first = telemetry.record(attempt, feature="agent_narrative")
    second = telemetry.record(attempt, feature="agent_narrative")
    session.commit()
    assert first.usage_event_id == second.usage_event_id
    assert session.scalar(select(func.count()).select_from(ModelCallAttempt)) == 1
    assert session.scalar(select(func.count()).select_from(UsageEvent)) == 1


@pytest.mark.parametrize(
    "status,reason,expected_calls",
    [
        (429, "openai_llm_rate_limited", 2),
        (500, "openai_llm_upstream_error", 2),
        (401, "openai_llm_permission_denied", 1),
        (403, "openai_llm_permission_denied", 1),
        (404, "openai_llm_model_unavailable", 1),
        (None, "openai_llm_timeout", 1),
    ],
)
def test_application_retry_budget_accounts_each_real_attempt(
    session: Session, status: int | None, reason: str, expected_calls: int
) -> None:
    provider = _FailingLLM(
        error=ServiceUnavailableError(
            "Provider request failed.",
            details={
                "http_status": status,
                "reason": reason,
                "input_tokens": 7,
                "output_tokens": 2,
            },
        )
    )
    router = ModelRouter(
        provider,
        tier_a_model="unknown-frontier",
        tier_b_model="unknown-frontier",
        fail_closed=True,
        telemetry=ModelCallTelemetryService(session),
    )
    result = router.complete(_request(ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS), _messages())
    assert result.unavailable and len(provider.calls) == expected_calls
    attempts = list(session.scalars(select(ModelCallAttempt)))
    events = list(session.scalars(select(UsageEvent)))
    assert len(attempts) == len(events) == expected_calls
    assert len({row.id for row in attempts}) == expected_calls
    assert all(row.input_tokens == 7 and row.output_tokens == 2 for row in attempts)
    assert all(row.cost_source is CostSource.UNAVAILABLE for row in attempts)


def test_usage_event_write_failure_is_surfaced(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.usage_service import UsagePersistenceError, UsageService

    def _boom(self: UsageService, data: object) -> object:
        del self, data
        raise UsagePersistenceError("injected usage write failure")

    monkeypatch.setattr(UsageService, "record", _boom)
    telemetry = ModelCallTelemetryService(session, UsageService(session), isolated=False)
    router = _router(telemetry=telemetry)
    result = router.complete(
        _request(ModelRoutingPurpose.NARRATIVE_SYNTHESIS, correlation_id="usage-boom"),
        _messages(),
    )
    assert result.failure_category is ModelFailureCategory.TELEMETRY_PERSISTENCE_FAILED
    assert result.deterministic_facts is not None
    assert result.deterministic_facts["provider_io_occurred"] is True
    assert session.scalars(select(ModelCallAttempt)).all() == []
    assert session.scalars(select(UsageEvent)).all() == []


def test_attempt_row_write_failure_is_surfaced(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.orm import Session as OrmSession

    from app.db.models import ModelCallAttempt as ModelCallAttemptRow

    original_add = OrmSession.add

    def _add(self: OrmSession, instance: object, **kwargs: object) -> None:
        if isinstance(instance, ModelCallAttemptRow):
            raise RuntimeError("injected attempt write failure")
        original_add(self, instance, **kwargs)

    monkeypatch.setattr(OrmSession, "add", _add)
    telemetry = ModelCallTelemetryService(session, UsageService(session), isolated=False)
    router = _router(telemetry=telemetry)
    result = router.complete(
        _request(ModelRoutingPurpose.NARRATIVE_SYNTHESIS, correlation_id="attempt-boom"),
        _messages(),
    )
    assert result.failure_category is ModelFailureCategory.TELEMETRY_PERSISTENCE_FAILED
    assert result.deterministic_facts is not None
    assert result.deterministic_facts["provider_io_occurred"] is True


def test_same_org_wrong_account_rejected() -> None:
    router = _router()
    with pytest.raises(CrossTenantModelContextError, match="cross-account"):
        router.decide(
            _request(
                ModelRoutingPurpose.STRATEGY_REVIEW,
                account=ACCOUNT_B,
                caller_account=ACCOUNT_A,
            )
        )


def test_same_user_wrong_account_rejected() -> None:
    router = _router()
    with pytest.raises(CrossTenantModelContextError, match="cross-account"):
        router.decide(
            _request(
                ModelRoutingPurpose.NARRATIVE_SYNTHESIS,
                user=USER_A,
                caller_user=USER_A,
                account=ACCOUNT_B,
                caller_account=ACCOUNT_A,
            )
        )


def test_wrong_strategy_resource_rejected() -> None:
    router = _router()
    with pytest.raises(CrossTenantModelContextError, match="cross-resource"):
        router.decide(
            _request(
                ModelRoutingPurpose.STRATEGY_REVIEW,
                resource_type=ModelResourceType.STRATEGY,
                resource_id=STRATEGY_B,
                caller_resource_type=ModelResourceType.STRATEGY,
                caller_resource_id=STRATEGY_A,
            )
        )


def test_wrong_journal_resource_rejected() -> None:
    router = _router()
    with pytest.raises(CrossTenantModelContextError, match="cross-resource"):
        router.decide(
            _request(
                ModelRoutingPurpose.EVIDENCE_EXPLANATION,
                resource_type=ModelResourceType.JOURNAL_TRADE,
                resource_id=JOURNAL_B,
                caller_resource_type=ModelResourceType.JOURNAL_TRADE,
                caller_resource_id=JOURNAL_A,
            )
        )


def test_correct_strategy_resource_allowed() -> None:
    router = _router()
    decision = router.decide(
        _request(
            ModelRoutingPurpose.STRATEGY_REVIEW,
            resource_type=ModelResourceType.STRATEGY,
            resource_id=STRATEGY_A,
            caller_resource_type=ModelResourceType.STRATEGY,
            caller_resource_id=STRATEGY_A,
            account=ACCOUNT_A,
            caller_account=ACCOUNT_A,
        )
    )
    assert decision.resource_id == STRATEGY_A
    assert decision.account_id == ACCOUNT_A


def test_generic_public_context_allowed() -> None:
    router = _router()
    decision = router.decide(
        _request(
            ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS,
            org=None,
            caller_org=ORG_A,
            resource_type=ModelResourceType.GENERIC,
            resource_id=None,
        )
    )
    assert decision.resource_type is ModelResourceType.GENERIC


def test_same_organization_wrong_user_rejected() -> None:
    router = _router()
    with pytest.raises(CrossTenantModelContextError, match="cross-principal") as exc_info:
        router.decide(
            _request(
                ModelRoutingPurpose.STRATEGY_REVIEW,
                org=ORG_A,
                caller_org=ORG_A,
                user=USER_B,
                caller_user=USER_A,
            )
        )
    assert exc_info.value.details["reason"] == "wrong_user"


def test_same_organization_correct_user_allowed() -> None:
    router = _router()
    decision = router.decide(
        _request(
            ModelRoutingPurpose.STRATEGY_REVIEW,
            org=ORG_A,
            caller_org=ORG_A,
            user=USER_A,
            caller_user=USER_A,
        )
    )
    assert decision.organization_id == ORG_A
    assert decision.user_id == USER_A


def test_cross_organization_rejected() -> None:
    router = _router()
    with pytest.raises(CrossTenantModelContextError, match="cross-tenant") as exc_info:
        router.decide(
            _request(
                ModelRoutingPurpose.NARRATIVE_SYNTHESIS,
                org=ORG_B,
                caller_org=ORG_A,
                user=USER_A,
                caller_user=USER_A,
            )
        )
    assert exc_info.value.details["reason"] == "wrong_organization"


def test_organization_scope_does_not_bypass_user_mismatch() -> None:
    router = _router()
    with pytest.raises(CrossTenantModelContextError, match="cross-principal") as exc_info:
        router.decide(
            _request(
                ModelRoutingPurpose.STRATEGY_REVIEW,
                org=ORG_A,
                caller_org=ORG_A,
                user=USER_B,
                caller_user=USER_A,
                caller_scope=ModelCallerScope.ORGANIZATION,
            )
        )
    assert exc_info.value.details["reason"] == "wrong_user"


def test_user_scope_cannot_omit_user_binding() -> None:
    router = _router()
    with pytest.raises(CrossTenantModelContextError, match="user binding") as exc_info:
        router.decide(
            _request(
                ModelRoutingPurpose.STRATEGY_REVIEW,
                org=ORG_A,
                caller_org=ORG_A,
                user=None,
                caller_user=None,
            )
        )
    assert exc_info.value.details["reason"] == "missing_user"


def test_trusted_system_scope_may_omit_user_binding() -> None:
    router = _router()
    decision = router.decide(
        _request(
            ModelRoutingPurpose.STRATEGY_REVIEW,
            org=ORG_A,
            caller_org=ORG_A,
            user=None,
            caller_user=None,
            caller_scope=ModelCallerScope.TRUSTED_SYSTEM,
        )
    )
    assert decision.organization_id == ORG_A
    assert decision.user_id is None


def test_organization_scope_may_omit_user_binding() -> None:
    router = _router()
    decision = router.decide(
        _request(
            ModelRoutingPurpose.NARRATIVE_SYNTHESIS,
            org=ORG_A,
            caller_org=ORG_A,
            user=None,
            caller_user=None,
            caller_scope=ModelCallerScope.ORGANIZATION,
        )
    )
    assert decision.organization_id == ORG_A
    assert decision.user_id is None
