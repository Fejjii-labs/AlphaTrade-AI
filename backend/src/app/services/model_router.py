"""Thin typed ModelRouter over the existing LLMProvider (Phase 2).

Does not create a second provider stack. Does not decide risk, kill switch,
freshness, fusion, permissions, execution eligibility, or strategy activation.
Fallback never mutates domain state and never weakens Tier C.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog

from app.core.errors import ForbiddenError, ServiceUnavailableError, ValidationAppError
from app.core.provider_policy import provider_fail_closed
from app.providers.llm import LLMCompletionRequest, LLMCompletionResult, LLMMessage, LLMProvider
from app.schemas.common import CostSource, UsageStatus
from app.schemas.model_routing import (
    TIER_A_PURPOSES,
    TIER_B_PURPOSES,
    ModelCallAttempt,
    ModelCallerScope,
    ModelFailureCategory,
    ModelFallbackPolicy,
    ModelResourceType,
    ModelRetentionCategory,
    ModelRoutingDecision,
    ModelRoutingPurpose,
    ModelRoutingTier,
    ModelTaskRequest,
    ModelTaskResult,
)
from app.services.model_call_telemetry import (
    ModelCallTelemetryService,
    ModelTelemetryPersistenceError,
    resolve_model_call_cost,
)

logger = structlog.get_logger(__name__)

_POLICY_VERSION = "model-router/v1"

_TIER_A_TOKEN_BUDGET = 4096
_TIER_B_TOKEN_BUDGET = 1024
_TIER_A_LATENCY_MS = 60_000
_TIER_B_LATENCY_MS = 15_000

_SECRET_RE = re.compile(
    r"(?:sk-[a-zA-Z0-9]{16,}|sk_(?:test|live)_[a-zA-Z0-9]{16,}|whsec_[a-zA-Z0-9]{16,}"
    r"|(?:api[_-]?key|password)\s*[:=]\s*\S+|bearer\s+[a-zA-Z0-9\-._~+/]{12,}"
    r"|authorization:\s*\S+)",
    re.IGNORECASE,
)


class ModelRoutingError(ForbiddenError):
    """Raised when a caller attempts an unauthorized routing action."""

    code = "model_routing_rejected"


class CrossTenantModelContextError(ForbiddenError):
    """Raised when a model request would mix tenant context."""

    code = "model_cross_tenant_rejected"


class ModelRouter:
    """Route typed purposes to Tier A/B models and record actual-call telemetry."""

    def __init__(
        self,
        llm_provider: LLMProvider,
        *,
        tier_a_model: str,
        tier_b_model: str,
        provider_name: str | None = None,
        policy_version: str = _POLICY_VERSION,
        fail_closed: bool = False,
        telemetry: ModelCallTelemetryService | None = None,
    ) -> None:
        self._llm = llm_provider
        self._tier_a_model = (tier_a_model or "").strip() or "gpt-4o"
        self._tier_b_model = (tier_b_model or "").strip() or "gpt-4o-mini"
        self._provider_name = provider_name or llm_provider.name
        self._policy_version = policy_version
        self._fail_closed = fail_closed
        self._telemetry = telemetry or ModelCallTelemetryService()
        self._attempts: list[ModelCallAttempt] = []

    @classmethod
    def from_settings(
        cls,
        llm_provider: LLMProvider,
        settings: Any,
        telemetry: ModelCallTelemetryService | None = None,
    ) -> ModelRouter:
        fail_closed = bool(getattr(settings, "model_router_fail_closed", False))
        fail_closed = fail_closed or provider_fail_closed(settings)
        tier_b = str(getattr(settings, "llm_tier_b_model", "") or settings.llm_model).strip()
        tier_a = str(getattr(settings, "llm_tier_a_model", "") or "").strip() or "gpt-4o"
        policy = str(getattr(settings, "model_router_policy_version", "") or _POLICY_VERSION)
        return cls(
            llm_provider,
            tier_a_model=tier_a,
            tier_b_model=tier_b,
            provider_name=llm_provider.name,
            policy_version=policy,
            fail_closed=fail_closed,
            telemetry=telemetry,
        )

    def attempts_for(self, correlation_id: str) -> list[ModelCallAttempt]:
        return [row for row in self._attempts if row.correlation_id == correlation_id]

    def decide(self, request: ModelTaskRequest) -> ModelRoutingDecision:
        """Return the immutable routing decision without calling a provider."""
        self._assert_purpose_allowed(request.purpose)
        self._assert_tenant_scope(request)
        self._assert_context_matches_purpose(request)
        tier = self._tier_for(request.purpose)
        if tier is ModelRoutingTier.TIER_C:
            raise ModelRoutingError(
                "Tier C purposes are not dispatched to a model.",
                details={"purpose": request.purpose.value},
            )
        assigned = self._model_for(tier)
        selected, fallback_policy, reason = self._apply_override(request, tier, assigned)
        retention = (
            ModelRetentionCategory.AUDIT_REQUIRED
            if tier is ModelRoutingTier.TIER_A
            else request.context.retention_category
        )
        return ModelRoutingDecision(
            purpose=request.purpose,
            selected_tier=tier,
            selected_model=selected,
            requested_model=request.model_override or assigned,
            provider=self._provider_name,
            reason=reason,
            policy_version=request.prompt_policy_version or self._policy_version,
            fallback_policy=fallback_policy,
            latency_budget_ms=_TIER_A_LATENCY_MS
            if tier is ModelRoutingTier.TIER_A
            else _TIER_B_LATENCY_MS,
            token_budget=request.max_output_tokens
            or (_TIER_A_TOKEN_BUDGET if tier is ModelRoutingTier.TIER_A else _TIER_B_TOKEN_BUDGET),
            retention_category=retention,
            organization_id=request.context.organization_id,
            user_id=request.context.user_id,
            account_id=request.context.account_id,
            resource_type=request.context.resource_type,
            resource_id=request.context.resource_id,
        )

    def complete(
        self,
        request: ModelTaskRequest,
        messages: list[LLMMessage],
    ) -> ModelTaskResult:
        """Execute one routed completion and record actual-call telemetry."""
        from app.services.turn_context import current_turn

        turn = current_turn()
        task_id = turn.next_task_id(request.purpose.value) if turn else uuid.uuid4()
        started = datetime.now(UTC)
        if not messages:
            raise ValidationAppError("Model task messages must not be empty.")
        self._assert_no_secrets(messages)
        decision = self.decide(request)
        attempts: list[ModelCallAttempt] = []

        models_to_try = [decision.selected_model]
        if (
            not self._fail_closed
            and decision.selected_tier is ModelRoutingTier.TIER_A
            and decision.fallback_policy is ModelFallbackPolicy.ALLOW_LOWER_TIER_MODEL
            and self._tier_b_model != decision.selected_model
        ):
            models_to_try.append(self._tier_b_model)

        last_error: BaseException | None = None
        # At most one retry for a retryable provider rejection. Timeout/connection
        # outcomes are ambiguous: do not repeat potentially billable generation.
        queue = [(model, index > 0, False) for index, model in enumerate(models_to_try)]
        retry_available = True
        for index, (model, model_fallback, retry) in enumerate(queue):
            if turn is not None and request.context.organization_id is not None:
                from app.core.errors import QuotaExceededError
                from app.services.quota_service import QuotaService

                with turn.sessions() as quota_session:
                    quota = QuotaService(quota_session).check_feature(
                        request.context.organization_id,
                        _feature_for(request.purpose),
                        request_id=str(turn.turn_id),
                        user_id=request.context.user_id,
                    )
                    quota_session.commit()
                if quota.hard_blocked:
                    raise QuotaExceededError(quota.message)
            attempt_started = datetime.now(UTC)
            try:
                llm_result = self._llm.complete(
                    LLMCompletionRequest(
                        messages=messages,
                        model=model,
                        temperature=request.temperature,
                        max_tokens=decision.token_budget,
                        response_format=request.response_format,
                        allow_internal_fallback=False,
                        reasoning_effort=request.reasoning_effort,
                    )
                )
            except Exception as exc:
                last_error = exc
                category = _failure_category(exc)
                logger.warning(
                    "model_router_provider_failed",
                    purpose=request.purpose.value,
                    model=model,
                    reason=category.value,
                    error=type(exc).__name__,
                )
                attempt, telemetry_error = self._record_attempt(
                    task_id=task_id,
                    request=request,
                    decision=decision,
                    requested_model=model,
                    resolved_model=model,
                    fallback_used=model_fallback,
                    llm_result=LLMCompletionResult(
                        content="",
                        model=model,
                        provider=self._provider_name,
                        input_tokens=_error_tokens(exc, "input_tokens"),
                        output_tokens=_error_tokens(exc, "output_tokens"),
                        latency_ms=_elapsed_ms(attempt_started),
                    ),
                    success=False,
                    category=category,
                    started_at=attempt_started,
                    attempt_number=index,
                )
                attempts.append(attempt)
                if telemetry_error is not None:
                    return self._telemetry_failed_result(task_id, decision, attempts, None, started)
                status = getattr(exc, "details", {}).get("http_status")
                if retry_available and not retry and status in {408, 429, 500, 502, 503, 504}:
                    retry_available = False
                    queue.insert(index + 1, (model, model_fallback, True))
                    continue
                if self._fail_closed:
                    return self._unavailable_result(task_id, decision, attempts, category, started)
                continue

            fallback_used = model_fallback or llm_result.fallback_used
            attempt, telemetry_error = self._record_attempt(
                task_id=task_id,
                request=request,
                decision=decision,
                requested_model=model,
                resolved_model=llm_result.model or model,
                fallback_used=fallback_used,
                llm_result=llm_result,
                success=True,
                category=ModelFailureCategory.NONE,
                started_at=attempt_started,
                attempt_number=index,
            )
            attempts.append(attempt)
            if telemetry_error is not None:
                return self._telemetry_failed_result(
                    task_id, decision, attempts, llm_result, started
                )
            return self._success_result(
                task_id, decision, attempts, llm_result, fallback_used, started
            )

        if self._fail_closed:
            category = (
                _failure_category(last_error)
                if last_error
                else ModelFailureCategory.PROVIDER_UNAVAILABLE
            )
            return self._unavailable_result(task_id, decision, attempts, category, started)

        fallback_category = (
            _failure_category(last_error)
            if last_error
            else ModelFailureCategory.PROVIDER_UNAVAILABLE
        )
        return self._deterministic_fallback_result(
            task_id,
            decision,
            attempts,
            fallback_category,
            started,
        )

    def _record_attempt(
        self,
        *,
        task_id: uuid.UUID,
        request: ModelTaskRequest,
        decision: ModelRoutingDecision,
        requested_model: str,
        resolved_model: str,
        fallback_used: bool,
        llm_result: LLMCompletionResult | None,
        success: bool,
        category: ModelFailureCategory,
        started_at: datetime,
        attempt_number: int = 0,
    ) -> tuple[ModelCallAttempt, ModelTelemetryPersistenceError | None]:
        completed = datetime.now(UTC)
        input_tokens = llm_result.input_tokens if llm_result is not None else 0
        output_tokens = llm_result.output_tokens if llm_result is not None else 0
        latency = llm_result.latency_ms if llm_result is not None else _elapsed_ms(started_at)
        cost = resolve_model_call_cost(
            model=resolved_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        attempt = ModelCallAttempt(
            attempt_id=uuid.uuid5(task_id, f"attempt:{attempt_number}:{requested_model}"),
            task_request_id=task_id,
            correlation_id=request.correlation_id,
            purpose=request.purpose,
            tier=self._attempt_tier(requested_model, decision.selected_tier),
            provider=self._provider_name,
            requested_model=requested_model,
            resolved_model=resolved_model,
            policy_version=decision.policy_version,
            fallback_policy=decision.fallback_policy,
            fallback_used=fallback_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency,
            success=success,
            failure_category=category,
            status=UsageStatus.SUCCESS if success else UsageStatus.FAILURE,
            estimated_cost=cost.estimated_cost,
            cost_source=cost.cost_source,
            organization_id=request.context.organization_id,
            user_id=request.context.user_id,
            account_id=request.context.account_id,
            resource_type=request.context.resource_type,
            resource_id=request.context.resource_id,
            retention_category=decision.retention_category,
            started_at=started_at,
            completed_at=completed,
            mutation_allowed=False,
        )
        try:
            persisted = self._telemetry.record(attempt, feature=_feature_for(request.purpose))
        except ModelTelemetryPersistenceError as exc:
            failed = exc.attempt.model_copy(
                update={
                    "persisted": False,
                    "telemetry_durable": False,
                    "failure_category": ModelFailureCategory.TELEMETRY_PERSISTENCE_FAILED,
                }
            )
            self._attempts.append(failed)
            return failed, exc
        self._attempts.append(persisted)
        return persisted, None

    def _success_result(
        self,
        task_id: uuid.UUID,
        decision: ModelRoutingDecision,
        attempts: list[ModelCallAttempt],
        llm_result: LLMCompletionResult,
        fallback_used: bool,
        started: datetime,
    ) -> ModelTaskResult:
        parsed = llm_result.parsed_json
        parsed_out: dict[str, object] | None = parsed if isinstance(parsed, dict) else None
        cost_source = attempts[-1].cost_source if attempts else CostSource.UNAVAILABLE
        durable = [row for row in attempts if row.telemetry_durable]
        return ModelTaskResult(
            task_request_id=task_id,
            decision=decision,
            parsed_output=parsed_out,
            content=llm_result.content,
            resolved_model=llm_result.model,
            provider=llm_result.provider,
            input_tokens=llm_result.input_tokens,
            output_tokens=llm_result.output_tokens,
            attempts=attempts,
            total_tokens=llm_result.input_tokens + llm_result.output_tokens,
            total_latency_ms=llm_result.latency_ms or _elapsed_ms(started),
            total_cost=sum((row.estimated_cost for row in durable), start=Decimal("0")),
            cost_source=cost_source if durable else CostSource.UNAVAILABLE,
            fallback_used=fallback_used,
            unavailable=False,
            failure_category=ModelFailureCategory.NONE,
            mutation_allowed=False,
            telemetry_persisted=bool(attempts) and all(row.telemetry_durable for row in attempts),
            completed_at=datetime.now(UTC),
        )

    def _unavailable_result(
        self,
        task_id: uuid.UUID,
        decision: ModelRoutingDecision,
        attempts: list[ModelCallAttempt],
        category: ModelFailureCategory,
        started: datetime,
    ) -> ModelTaskResult:
        return ModelTaskResult(
            task_request_id=task_id,
            decision=decision,
            attempts=attempts,
            total_latency_ms=_elapsed_ms(started),
            cost_source=CostSource.UNAVAILABLE,
            fallback_used=True,
            unavailable=True,
            deterministic_facts={"status": "unavailable", "mutation_allowed": False},
            failure_category=category,
            mutation_allowed=False,
            telemetry_persisted=bool(attempts) and all(row.telemetry_durable for row in attempts),
            completed_at=datetime.now(UTC),
        )

    def _deterministic_fallback_result(
        self,
        task_id: uuid.UUID,
        decision: ModelRoutingDecision,
        attempts: list[ModelCallAttempt],
        category: ModelFailureCategory,
        started: datetime,
    ) -> ModelTaskResult:
        facts: dict[str, object] = {
            "status": "deterministic_facts",
            "purpose": decision.purpose.value,
            "tier_c_authority": True,
            "mutation_allowed": False,
            "reason": "provider_fallback",
        }
        return ModelTaskResult(
            task_request_id=task_id,
            decision=decision,
            content="",
            attempts=attempts,
            total_latency_ms=_elapsed_ms(started),
            cost_source=CostSource.UNAVAILABLE,
            fallback_used=True,
            unavailable=False,
            deterministic_facts=facts,
            failure_category=category,
            mutation_allowed=False,
            telemetry_persisted=bool(attempts) and all(row.telemetry_durable for row in attempts),
            completed_at=datetime.now(UTC),
        )

    def _telemetry_failed_result(
        self,
        task_id: uuid.UUID,
        decision: ModelRoutingDecision,
        attempts: list[ModelCallAttempt],
        llm_result: LLMCompletionResult | None,
        started: datetime,
    ) -> ModelTaskResult:
        parsed = llm_result.parsed_json if llm_result is not None else None
        parsed_out: dict[str, object] | None = parsed if isinstance(parsed, dict) else None
        return ModelTaskResult(
            task_request_id=task_id,
            decision=decision,
            parsed_output=parsed_out,
            content=llm_result.content if llm_result is not None else "",
            resolved_model=llm_result.model if llm_result is not None else "",
            provider=llm_result.provider if llm_result is not None else self._provider_name,
            input_tokens=llm_result.input_tokens if llm_result is not None else 0,
            output_tokens=llm_result.output_tokens if llm_result is not None else 0,
            attempts=attempts,
            total_tokens=(
                (llm_result.input_tokens + llm_result.output_tokens)
                if llm_result is not None
                else 0
            ),
            total_latency_ms=_elapsed_ms(started),
            total_cost=Decimal("0"),
            cost_source=CostSource.UNAVAILABLE,
            fallback_used=True,
            unavailable=llm_result is None,
            deterministic_facts={
                "status": "telemetry_persistence_failed",
                "provider_io_occurred": True,
                "mutation_allowed": False,
            },
            failure_category=ModelFailureCategory.TELEMETRY_PERSISTENCE_FAILED,
            mutation_allowed=False,
            telemetry_persisted=False,
            completed_at=datetime.now(UTC),
        )

    def _attempt_tier(self, model: str, selected: ModelRoutingTier) -> ModelRoutingTier:
        if model == self._tier_a_model:
            return ModelRoutingTier.TIER_A
        if model == self._tier_b_model:
            return ModelRoutingTier.TIER_B
        return selected

    def _apply_override(
        self,
        request: ModelTaskRequest,
        tier: ModelRoutingTier,
        assigned: str,
    ) -> tuple[str, ModelFallbackPolicy, str]:
        fallback_policy = self._fallback_policy_for(tier)
        override = (request.model_override or "").strip()
        if not override:
            return assigned, fallback_policy, f"policy:{self._policy_version}:{tier.value}"
        if override == assigned:
            return assigned, fallback_policy, f"policy:{self._policy_version}:assigned"
        # Lower-capability fallback is allowed for Tier A only, never elevation.
        if (
            tier is ModelRoutingTier.TIER_A
            and override == self._tier_b_model
            and not self._fail_closed
        ):
            return (
                override,
                ModelFallbackPolicy.ALLOW_LOWER_TIER_MODEL,
                f"policy:{self._policy_version}:explicit_lower_fallback",
            )
        raise ModelRoutingError(
            "Unauthorized model override rejected.",
            details={
                "purpose": request.purpose.value,
                "tier": tier.value,
                "assigned_model": assigned,
            },
        )

    def _fallback_policy_for(self, tier: ModelRoutingTier) -> ModelFallbackPolicy:
        if self._fail_closed:
            return ModelFallbackPolicy.UNAVAILABLE
        if tier is ModelRoutingTier.TIER_A:
            return ModelFallbackPolicy.ALLOW_LOWER_TIER_MODEL
        return ModelFallbackPolicy.DETERMINISTIC_FACTS

    def _model_for(self, tier: ModelRoutingTier) -> str:
        if tier is ModelRoutingTier.TIER_A:
            return self._tier_a_model
        return self._tier_b_model

    def _tier_for(self, purpose: ModelRoutingPurpose) -> ModelRoutingTier:
        if purpose in TIER_A_PURPOSES:
            return ModelRoutingTier.TIER_A
        if purpose in TIER_B_PURPOSES:
            return ModelRoutingTier.TIER_B
        return ModelRoutingTier.TIER_C

    def _assert_purpose_allowed(self, purpose: ModelRoutingPurpose) -> None:
        if purpose not in TIER_A_PURPOSES and purpose not in TIER_B_PURPOSES:
            raise ModelRoutingError(
                "Purpose is not eligible for model routing.",
                details={"purpose": purpose.value},
            )

    def _assert_context_matches_purpose(self, request: ModelTaskRequest) -> None:
        if request.context.purpose != request.purpose:
            raise ValidationAppError("Model context purpose must match the requested purpose.")

    def _assert_tenant_scope(self, request: ModelTaskRequest) -> None:
        ctx = request.context
        generic_ok = ctx.resource_type is ModelResourceType.GENERIC and ctx.resource_id is None
        public_ok = request.purpose is ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS and generic_ok
        privileged_scope = request.caller_scope in {
            ModelCallerScope.ORGANIZATION,
            ModelCallerScope.TRUSTED_SYSTEM,
        }
        if (
            request.caller_organization_id is not None
            and ctx.organization_id is not None
            and request.caller_organization_id != ctx.organization_id
        ):
            raise CrossTenantModelContextError(
                "Models must not receive cross-tenant context.",
                details={"resource_type": ctx.resource_type.value, "reason": "wrong_organization"},
            )
        if (
            request.caller_user_id is not None
            and ctx.user_id is not None
            and request.caller_user_id != ctx.user_id
        ):
            raise CrossTenantModelContextError(
                "Models must not receive cross-principal context.",
                details={"resource_type": ctx.resource_type.value, "reason": "wrong_user"},
            )
        if (
            not privileged_scope
            and not public_ok
            and (request.caller_user_id is None or ctx.user_id is None)
        ):
            raise CrossTenantModelContextError(
                "Private model context requires user binding.",
                details={"resource_type": ctx.resource_type.value, "reason": "missing_user"},
            )
        if (
            ctx.organization_id is None
            and request.caller_organization_id is not None
            and not public_ok
        ):
            raise CrossTenantModelContextError(
                "Private model context requires organization scope.",
                details={"purpose": request.purpose.value, "reason": "missing_organization"},
            )
        if ctx.account_id is not None and request.caller_account_id != ctx.account_id:
            raise CrossTenantModelContextError(
                "Models must not receive cross-account context.",
                details={"resource_type": ctx.resource_type.value, "reason": "wrong_account"},
            )
        private_resources = {
            ModelResourceType.STRATEGY,
            ModelResourceType.JOURNAL_TRADE,
            ModelResourceType.CANDIDATE,
            ModelResourceType.LESSON,
        }
        if ctx.resource_type in private_resources:
            if ctx.resource_id is None:
                raise CrossTenantModelContextError(
                    "Private model context requires an explicit resource identity.",
                    details={
                        "resource_type": ctx.resource_type.value,
                        "reason": "missing_resource",
                    },
                )
            if (
                request.caller_resource_type is not None
                and request.caller_resource_type != ctx.resource_type
            ):
                raise CrossTenantModelContextError(
                    "Models must not receive cross-resource context.",
                    details={
                        "resource_type": ctx.resource_type.value,
                        "reason": "wrong_resource_type",
                    },
                )
            if request.caller_resource_id != ctx.resource_id:
                raise CrossTenantModelContextError(
                    "Models must not receive cross-resource context.",
                    details={"resource_type": ctx.resource_type.value, "reason": "wrong_resource"},
                )

    def _assert_no_secrets(self, messages: list[LLMMessage]) -> None:
        if _SECRET_RE.search("\n".join(message.content for message in messages)):
            raise ModelRoutingError(
                "Model prompts must not contain secrets.",
                details={"reason": ModelFailureCategory.SECRET_IN_CONTEXT.value},
            )


def _feature_for(purpose: ModelRoutingPurpose) -> str:
    from app.schemas.model_routing import MODEL_PURPOSE_TO_USAGE_FEATURE

    return MODEL_PURPOSE_TO_USAGE_FEATURE[purpose]


def _elapsed_ms(started: datetime) -> float:
    return round((datetime.now(UTC) - started).total_seconds() * 1000, 2)


def _failure_category(exc: BaseException | None) -> ModelFailureCategory:
    if exc is None:
        return ModelFailureCategory.PROVIDER_UNAVAILABLE
    if isinstance(exc, ModelRoutingError):
        code = getattr(exc, "code", "")
        if code == "model_cross_tenant_rejected":
            return ModelFailureCategory.CROSS_TENANT
        return ModelFailureCategory.UNAUTHORIZED_OVERRIDE
    if isinstance(exc, CrossTenantModelContextError):
        return ModelFailureCategory.CROSS_TENANT
    if isinstance(exc, ServiceUnavailableError):
        details = getattr(exc, "details", {}) or {}
        reason = str(details.get("reason") or "")
        if "timeout" in reason:
            return ModelFailureCategory.TIMEOUT
        return ModelFailureCategory.PROVIDER_UNAVAILABLE
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return ModelFailureCategory.TIMEOUT
    return ModelFailureCategory.UNKNOWN


def _error_tokens(exc: BaseException, key: str) -> int:
    value = getattr(exc, "details", {}).get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
