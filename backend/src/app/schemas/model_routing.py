"""Typed model-routing contracts (Phase 2).

Callers request a purpose, never a free-form model name. Tier C is deterministic
application code and is not an LLM. Models never decide risk, kill switch,
freshness, fusion, permissions, execution eligibility, or strategy activation.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import CostSource, NonNegativeDecimal, StrictModel, UsageStatus


class ModelRoutingPurpose(StrEnum):
    """Task purposes justified by the current repository and target architecture."""

    INTENT_CLASSIFICATION = "intent_classification"
    NARRATIVE_SYNTHESIS = "narrative_synthesis"
    STRATEGY_REVIEW = "strategy_review"
    LESSON_SYNTHESIS = "lesson_synthesis"
    EVIDENCE_EXPLANATION = "evidence_explanation"
    GENERAL_AGENT_SYNTHESIS = "general_agent_synthesis"


class ModelRoutingTier(StrEnum):
    """Capability class. ``TIER_C`` is never dispatched to a model."""

    TIER_A = "tier_a"
    TIER_B = "tier_b"
    TIER_C = "tier_c"


class ModelFallbackPolicy(StrEnum):
    """Explicit fallback policy. Never weakens Tier C and never mutates domain state."""

    NONE = "none"
    ALLOW_LOWER_TIER_MODEL = "allow_lower_tier_model"
    DETERMINISTIC_FACTS = "deterministic_facts"
    UNAVAILABLE = "unavailable"


class ModelRetentionCategory(StrEnum):
    EPHEMERAL = "ephemeral"
    STANDARD = "standard"
    AUDIT_REQUIRED = "audit_required"


class ModelResourceType(StrEnum):
    CONVERSATION = "conversation"
    STRATEGY = "strategy"
    JOURNAL_TRADE = "journal_trade"
    CANDIDATE = "candidate"
    LESSON = "lesson"
    GENERIC = "generic"


class ModelCallerScope(StrEnum):
    """Typed caller authority. Only ORGANIZATION and TRUSTED_SYSTEM may omit user binding."""

    USER = "user"
    ORGANIZATION = "organization"
    TRUSTED_SYSTEM = "trusted_system"


class ModelFailureCategory(StrEnum):
    NONE = "none"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    UNAUTHORIZED_OVERRIDE = "unauthorized_override"
    CROSS_TENANT = "cross_tenant"
    SECRET_IN_CONTEXT = "secret_in_context"
    VALIDATION_FAILED = "validation_failed"
    QUOTA = "quota"
    TELEMETRY_PERSISTENCE_FAILED = "telemetry_persistence_failed"
    UNKNOWN = "unknown"


class ModelContextScope(StrictModel):
    """Least-privilege typed scope carried on every routed model request."""

    organization_id: UUID | None = None
    user_id: UUID | None = None
    account_id: UUID | None = None
    resource_type: ModelResourceType = ModelResourceType.GENERIC
    resource_id: UUID | None = None
    purpose: ModelRoutingPurpose
    retention_category: ModelRetentionCategory = ModelRetentionCategory.STANDARD


class ModelRoutingDecision(StrictModel):
    """Immutable explanation of why a model (or no model) was selected."""

    purpose: ModelRoutingPurpose
    selected_tier: ModelRoutingTier
    selected_model: str
    requested_model: str
    provider: str
    reason: str
    policy_version: str
    fallback_policy: ModelFallbackPolicy
    latency_budget_ms: int | None = None
    token_budget: int
    retention_category: ModelRetentionCategory
    organization_id: UUID | None = None
    user_id: UUID | None = None
    account_id: UUID | None = None
    resource_type: ModelResourceType = ModelResourceType.GENERIC
    resource_id: UUID | None = None


class ModelTaskRequest(StrictModel):
    """Caller contract: purpose + scoped context + redacted messages. No model elevation."""

    purpose: ModelRoutingPurpose
    context: ModelContextScope
    correlation_id: str = Field(min_length=1, max_length=128)
    caller_organization_id: UUID | None = None
    caller_user_id: UUID | None = None
    caller_scope: ModelCallerScope = ModelCallerScope.USER
    caller_account_id: UUID | None = None
    caller_resource_type: ModelResourceType | None = None
    caller_resource_id: UUID | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    response_format: dict[str, object] | None = None
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "low"
    max_output_tokens: int | None = Field(default=None, ge=64, le=65536)
    # Optional override is accepted only when it matches the policy model or an
    # explicitly allowed *lower* capability fallback. Elevation is rejected.
    model_override: str | None = Field(default=None, max_length=80)
    prompt_policy_version: str | None = Field(default=None, max_length=80)
    prompt_template_version: str | None = Field(default=None, max_length=80)


class ModelCallAttempt(StrictModel):
    """One actual provider call. Usage and cost are the sum of these records."""

    attempt_id: UUID
    task_request_id: UUID
    correlation_id: str
    purpose: ModelRoutingPurpose
    tier: ModelRoutingTier
    provider: str
    requested_model: str
    resolved_model: str
    policy_version: str
    fallback_policy: ModelFallbackPolicy
    fallback_used: bool = False
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    success: bool
    failure_category: ModelFailureCategory = ModelFailureCategory.NONE
    status: UsageStatus = UsageStatus.SUCCESS
    estimated_cost: NonNegativeDecimal = Decimal("0")
    cost_source: CostSource = CostSource.UNAVAILABLE
    organization_id: UUID | None = None
    user_id: UUID | None = None
    account_id: UUID | None = None
    resource_type: ModelResourceType = ModelResourceType.GENERIC
    resource_id: UUID | None = None
    retention_category: ModelRetentionCategory = ModelRetentionCategory.STANDARD
    usage_event_id: UUID | None = None
    started_at: datetime
    completed_at: datetime
    mutation_allowed: bool = False
    persisted: bool = False
    telemetry_durable: bool = False


class ModelTaskResult(StrictModel):
    """Outcome of a routed task. Fallback never authorizes domain mutation."""

    task_request_id: UUID
    decision: ModelRoutingDecision
    parsed_output: dict[str, object] | None = None
    content: str = ""
    resolved_model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: list[ModelCallAttempt] = Field(default_factory=list)
    total_tokens: int = 0
    total_latency_ms: float | None = None
    total_cost: NonNegativeDecimal = Decimal("0")
    cost_source: CostSource = CostSource.UNAVAILABLE
    fallback_used: bool = False
    unavailable: bool = False
    deterministic_facts: dict[str, object] | None = None
    failure_category: ModelFailureCategory = ModelFailureCategory.NONE
    mutation_allowed: bool = False
    telemetry_persisted: bool = False
    completed_at: datetime


MODEL_PURPOSE_TO_USAGE_FEATURE: dict[ModelRoutingPurpose, str] = {
    ModelRoutingPurpose.INTENT_CLASSIFICATION: "agent_chat",
    ModelRoutingPurpose.NARRATIVE_SYNTHESIS: "agent_narrative",
    ModelRoutingPurpose.STRATEGY_REVIEW: "agent_chat",
    ModelRoutingPurpose.LESSON_SYNTHESIS: "agent_narrative",
    ModelRoutingPurpose.EVIDENCE_EXPLANATION: "agent_narrative",
    ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS: "agent_chat",
}


TIER_A_PURPOSES: frozenset[ModelRoutingPurpose] = frozenset(
    {
        ModelRoutingPurpose.STRATEGY_REVIEW,
        ModelRoutingPurpose.LESSON_SYNTHESIS,
        ModelRoutingPurpose.EVIDENCE_EXPLANATION,
        ModelRoutingPurpose.GENERAL_AGENT_SYNTHESIS,
    }
)

TIER_B_PURPOSES: frozenset[ModelRoutingPurpose] = frozenset(
    {
        ModelRoutingPurpose.INTENT_CLASSIFICATION,
        ModelRoutingPurpose.NARRATIVE_SYNTHESIS,
    }
)
