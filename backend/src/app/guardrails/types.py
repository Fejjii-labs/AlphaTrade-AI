"""Typed guardrail inputs and structured verdicts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from app.schemas.agent import AgentState
from app.schemas.common import SafetyVerdict, StrictModel
from app.schemas.proposal import TradeProposal
from app.schemas.risk import RiskCheckResult


class GuardrailSeverity(StrEnum):
    """Ordered severity for guardrail outcomes."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class GuardrailInput(StrictModel):
    """Normalized input for deterministic (and future LLM) guardrails."""

    message: str = ""
    final_answer: str | None = None
    request_id: str | None = None
    has_trade_proposal: bool = False
    trade_proposal: TradeProposal | None = None
    risk_result: RiskCheckResult | None = None
    approval_required: bool | None = None
    confidence: float | None = None
    has_market_context: bool = False
    #: True only when the answer cites >= 1 chunk id (not merely when retrieval ran).
    has_citations: bool = False
    cited_chunk_ids: list[str] = Field(default_factory=list)
    retrieved_chunk_ids: list[str] = Field(default_factory=list)
    #: retrieved | no_relevant_source | retrieval_failed; None = retrieval never reported.
    rag_outcome: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_agent_state(cls, state: AgentState) -> GuardrailInput:
        """Build guardrail input from the current agent workflow state."""
        return cls(
            message=state.message,
            final_answer=state.final_answer,
            request_id=state.request_id,
            has_trade_proposal=state.trade_proposal is not None,
            trade_proposal=state.trade_proposal,
            risk_result=state.risk_result,
            approval_required=state.approval_required,
            confidence=state.confidence,
            has_market_context=state.market_context is not None,
            has_citations=bool(state.cited_chunk_ids),
            cited_chunk_ids=[str(item) for item in state.cited_chunk_ids],
            retrieved_chunk_ids=[str(c.chunk_id) for c in state.retrieved_context],
            rag_outcome=state.rag_outcome,
            metadata={
                "intent": state.intent.value,
                "message_class": state.message_class.value,
            },
        )


class GuardrailResult(StrictModel):
    """Structured verdict returned by every guardrail implementation."""

    allowed: bool = True
    blocked: bool = False
    severity: GuardrailSeverity = GuardrailSeverity.INFO
    reason: str | None = None
    triggered_rules: list[str] = Field(default_factory=list)
    safe_message: str | None = None
    audit_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def pass_(cls, *, reason: str | None = None) -> GuardrailResult:
        return cls(allowed=True, blocked=False, reason=reason)

    @classmethod
    def block(
        cls,
        *,
        rule_id: str,
        reason: str,
        severity: GuardrailSeverity = GuardrailSeverity.CRITICAL,
        safe_message: str | None = None,
        audit_required: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> GuardrailResult:
        return cls(
            allowed=False,
            blocked=True,
            severity=severity,
            reason=reason,
            triggered_rules=[rule_id],
            safe_message=safe_message,
            audit_required=audit_required,
            metadata=metadata or {},
        )

    @classmethod
    def warn(
        cls,
        *,
        rule_id: str,
        reason: str,
        safe_message: str | None = None,
        audit_required: bool = False,
    ) -> GuardrailResult:
        return cls(
            allowed=True,
            blocked=False,
            severity=GuardrailSeverity.MEDIUM,
            reason=reason,
            triggered_rules=[rule_id],
            safe_message=safe_message,
            audit_required=audit_required,
        )

    def merge(self, other: GuardrailResult) -> GuardrailResult:
        """Combine two results, preferring the stricter outcome."""
        if other.blocked or self.blocked:
            blocked = True
            allowed = False
        else:
            blocked = False
            allowed = self.allowed and other.allowed

        severity_order = list(GuardrailSeverity)
        severity = max(
            self.severity,
            other.severity,
            key=lambda s: severity_order.index(s),
        )
        return GuardrailResult(
            allowed=allowed,
            blocked=blocked,
            severity=severity,
            reason=other.reason or self.reason,
            triggered_rules=[*self.triggered_rules, *other.triggered_rules],
            safe_message=other.safe_message or self.safe_message,
            audit_required=self.audit_required or other.audit_required,
            metadata={**self.metadata, **other.metadata},
        )

    def to_safety_verdict(self) -> SafetyVerdict:
        """Map structured verdict to graph ``SafetyVerdict`` enum."""
        if self.blocked:
            return SafetyVerdict.BLOCK
        if self.severity in {GuardrailSeverity.HIGH, GuardrailSeverity.CRITICAL}:
            return SafetyVerdict.FLAG
        if self.triggered_rules and self.severity in {
            GuardrailSeverity.MEDIUM,
            GuardrailSeverity.LOW,
        }:
            return SafetyVerdict.FLAG
        return SafetyVerdict.PASS
