"""Usage/cost event schemas.

Provider-reported token usage and cost is preferred for billing. Estimates are
explicitly labeled via :class:`CostSource` and must not be presented as
billing-grade.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import Field

from app.schemas.common import CostSource, NonNegativeDecimal, ORMModel, StrictModel, UsageStatus


class UsageEventCreate(StrictModel):
    """Input for recording metered usage."""

    request_id: str
    feature: str
    usage_event_id: UUID | None = None
    user_id: UUID | None = None
    organization_id: UUID | None = None
    provider: str | None = None
    model: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    cache_hit: bool = False
    fallback_used: bool = False
    latency_ms: float | None = Field(default=None, ge=0)
    status: UsageStatus = UsageStatus.SUCCESS
    timestamp: datetime | None = None
    provider_metadata: dict[str, int | float | str | bool] = Field(default_factory=dict)


class UsageEvent(StrictModel):
    """A single metered LLM/tool interaction."""

    usage_event_id: UUID | None = None
    request_id: str | None = None
    organization_id: UUID | None = None
    user_id: UUID | None = None
    feature: str
    model: str | None = None
    provider: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    provider_reported_cost: NonNegativeDecimal | None = None
    estimated_cost: NonNegativeDecimal = Decimal("0")
    cost_source: CostSource = CostSource.UNAVAILABLE
    cost_is_placeholder: bool = True
    tool_calls: int = Field(default=0, ge=0)
    cache_hit: bool = False
    fallback_used: bool = False
    latency_ms: float | None = Field(default=None, ge=0)
    status: UsageStatus = UsageStatus.SUCCESS
    timestamp: datetime

    @property
    def is_billing_grade(self) -> bool:
        return self.cost_source is CostSource.PROVIDER_REPORTED


class UsageSummary(ORMModel):
    """Aggregated usage for dashboards and cost review."""

    organization_id: UUID | None = None
    user_id: UUID | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    event_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_provider_reported_cost: NonNegativeDecimal = Decimal("0")
    total_estimated_cost: NonNegativeDecimal = Decimal("0")
    total_cost: NonNegativeDecimal = Decimal("0")
    billing_grade_cost: NonNegativeDecimal = Decimal("0")
    cost_is_placeholder: bool = True
    total_tool_calls: int = 0
    fallback_count: int = 0
    cache_hit_count: int = 0


class UsageFeatureBreakdown(StrictModel):
    feature: str
    event_count: int = 0
    total_tokens: int = 0
    total_cost: NonNegativeDecimal = Decimal("0")
    fallback_count: int = 0


class UsageProviderBreakdown(StrictModel):
    provider: str
    event_count: int = 0
    total_tokens: int = 0
    total_cost: NonNegativeDecimal = Decimal("0")
    fallback_count: int = 0


class PaginatedUsageEvents(StrictModel):
    items: list[UsageEvent]
    total: int
    limit: int
    offset: int


class OrganizationQuotaConfig(StrictModel):
    """Organization-level usage limits (configurable by OWNER)."""

    organization_id: UUID
    monthly_token_limit: int = Field(default=2_000_000, ge=0)
    monthly_cost_limit: NonNegativeDecimal = Decimal("100.00")
    daily_request_limit: int = Field(default=5_000, ge=0)
    limit_agent_chat: int = Field(default=2_000, ge=0)
    limit_rag_ingest: int = Field(default=500, ge=0)
    limit_market_analyze: int = Field(default=1_000, ge=0)
    limit_agent_narrative: int = Field(default=2_000, ge=0)
    limit_paper_execution: int = Field(default=200, ge=0)
    soft_warning_threshold: NonNegativeDecimal = Decimal("0.80")
    hard_block_threshold: NonNegativeDecimal = Decimal("1.00")
    plan_id: str = "free"


class OrganizationQuotaUpdate(StrictModel):
    """Partial quota update (OWNER only)."""

    monthly_token_limit: int | None = Field(default=None, ge=0)
    monthly_cost_limit: NonNegativeDecimal | None = None
    daily_request_limit: int | None = Field(default=None, ge=0)
    limit_agent_chat: int | None = Field(default=None, ge=0)
    limit_rag_ingest: int | None = Field(default=None, ge=0)
    limit_market_analyze: int | None = Field(default=None, ge=0)
    limit_agent_narrative: int | None = Field(default=None, ge=0)
    limit_paper_execution: int | None = Field(default=None, ge=0)
    soft_warning_threshold: NonNegativeDecimal | None = None
    hard_block_threshold: NonNegativeDecimal | None = None


class QuotaUsageSnapshot(StrictModel):
    """Current period consumption vs limits."""

    monthly_tokens_used: int = 0
    monthly_tokens_reserved: int = Field(default=0, ge=0)
    monthly_tokens_limit: int = 0
    monthly_tokens_pct: float = 0.0
    monthly_cost_used: NonNegativeDecimal = Decimal("0")
    monthly_cost_reserved: NonNegativeDecimal = Decimal("0")
    monthly_cost_limit: NonNegativeDecimal = Decimal("0")
    monthly_cost_pct: float = 0.0
    daily_requests_used: int = 0
    daily_requests_limit: int = 0
    daily_requests_pct: float = 0.0
    feature_usage: dict[str, int] = Field(default_factory=dict)


class QuotaStatus(StrictModel):
    """Quota configuration plus utilization and enforcement state."""

    quota: OrganizationQuotaConfig
    usage: QuotaUsageSnapshot
    soft_limit_reached: bool = False
    hard_limit_reached: bool = False
    warnings: list[str] = Field(default_factory=list)
    blocked_features: list[str] = Field(default_factory=list)
