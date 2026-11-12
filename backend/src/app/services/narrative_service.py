"""Optional LLM narrative layer — polishes explanation; deterministic analysis is authoritative."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from app.guardrails.citations import CITATION_NOT_IN_RETRIEVED_SET, verify_cited_chunk_ids
from app.guardrails.context_sanitizer import sanitize_retrieved_snippet
from app.guardrails.narrative_validation import NarrativeValidationGuardrail
from app.guardrails.redaction import redact_mapping
from app.providers.llm import LLMCompletionRequest, LLMCompletionResult, LLMMessage, LLMProvider
from app.schemas.agent import AgentState, Intent
from app.schemas.analysis import TradingAnalysisDetail
from app.schemas.narrative import NarrativeMetadata, TradingNarrativeDetail
from app.schemas.strict_json_schema import strict_response_format
from app.schemas.usage import UsageEvent

logger = structlog.get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"
_NARRATIVE_TASK_MARKER = "__narrative_task__:"


@dataclass(frozen=True)
class NarrativeEnhancementResult:
    narrative: TradingNarrativeDetail
    metadata: NarrativeMetadata
    usage: UsageEvent | None
    used_llm: bool
    citation_violations: tuple[str, ...] = ()


class NarrativeService:
    """Sanitize context, call LLM, validate output, fall back to deterministic narrative."""

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        llm_model: str,
        narrative_validator: NarrativeValidationGuardrail | None = None,
        enabled: bool = True,
        model_router: Any | None = None,
    ) -> None:
        self._llm = llm_provider
        self._model = llm_model
        self._validator = narrative_validator or NarrativeValidationGuardrail()
        self._enabled = enabled
        self._router = model_router

    def select_prompt_name(self, agent: AgentState) -> str:
        if agent.intent is Intent.REVIEW:
            return "journal_review_coach"
        if agent.risk_result is not None:
            return "risk_explanation"
        return "trading_analysis_narrative"

    def enhance(
        self,
        agent: AgentState,
        analysis: TradingAnalysisDetail,
        *,
        persist_usage: Any | None = None,
    ) -> NarrativeEnhancementResult:
        """Return validated narrative; fall back to deterministic copy on any failure."""
        fallback = self.narrative_from_deterministic(analysis, agent)
        fallback_meta = NarrativeMetadata(
            source="deterministic_fallback",
            provider=self._llm.name,
            model=self._model,
            fallback_used=True,
            validation_passed=True,
            latency_ms=None,
        )

        if not self._enabled:
            return NarrativeEnhancementResult(
                narrative=fallback,
                metadata=fallback_meta,
                usage=None,
                used_llm=False,
            )

        context = build_sanitized_narrative_context(agent, analysis)
        prompt_name = self.select_prompt_name(agent)
        llm_result = self._call_llm(prompt_name, context, agent)

        usage = _usage_from_llm(agent, llm_result, feature="agent_narrative")
        if persist_usage is not None and self._router is None:
            persist_usage(agent, llm_result=llm_result, feature="agent_narrative")

        parsed = llm_result.parsed_json
        if parsed is None and llm_result.content.strip():
            try:
                parsed = json.loads(llm_result.content.strip())
            except json.JSONDecodeError:
                parsed = None

        narrative = _parse_narrative(parsed)
        if narrative is None:
            logger.info(
                "narrative_fallback_invalid_schema",
                request_id=agent.request_id,
                provider=llm_result.provider,
            )
            return NarrativeEnhancementResult(
                narrative=fallback,
                metadata=fallback_meta.model_copy(
                    update={"fallback_used": True, "validation_passed": False}
                ),
                usage=usage,
                used_llm=False,
            )

        validation = self._validator.evaluate_narrative(
            narrative,
            expected_risk_level=(
                analysis.risk_level.value if analysis.risk_level is not None else None
            ),
            expected_approval_status=analysis.approval_status,
            has_trade_proposal=agent.trade_proposal is not None,
            market_data_quality=analysis.market_data_quality,
        )
        if validation.blocked:
            logger.info(
                "narrative_fallback_validation_failed",
                request_id=agent.request_id,
                rules=validation.triggered_rules,
            )
            return NarrativeEnhancementResult(
                narrative=fallback,
                metadata=fallback_meta.model_copy(
                    update={"fallback_used": True, "validation_passed": False}
                ),
                usage=usage,
                used_llm=False,
            )

        retrieved_ids = [c.chunk_id for c in agent.retrieved_context]
        citations = verify_cited_chunk_ids(narrative.cited_chunk_ids, retrieved_ids)
        if citations.unknown or (retrieved_ids and not citations.grounded):
            # Foreign ids, or retrieved sources the narrative did not cite: reject
            # the LLM text; the deterministic narrative cites by construction.
            logger.info(
                "narrative_fallback_citation_check",
                request_id=agent.request_id,
                rule=CITATION_NOT_IN_RETRIEVED_SET if citations.unknown else "no_cited_source",
                unknown_count=len(citations.unknown),
            )
            return NarrativeEnhancementResult(
                narrative=fallback,
                metadata=fallback_meta.model_copy(
                    update={"fallback_used": True, "validation_passed": False}
                ),
                usage=usage,
                used_llm=False,
                citation_violations=citations.unknown,
            )
        narrative = narrative.model_copy(update={"cited_chunk_ids": list(citations.valid)})

        meta = NarrativeMetadata(
            source="llm",
            provider=llm_result.provider,
            model=llm_result.model,
            fallback_used=llm_result.fallback_used,
            validation_passed=True,
            latency_ms=llm_result.latency_ms,
        )
        return NarrativeEnhancementResult(
            narrative=narrative,
            metadata=meta,
            usage=usage,
            used_llm=True,
        )

    def narrative_from_deterministic(
        self, analysis: TradingAnalysisDetail, agent: AgentState
    ) -> TradingNarrativeDetail:
        """Deterministic fallback narrative — mirrors authoritative analysis fields."""
        # Citations by construction: only chunks retrieved (and above the floor).
        cited = agent.retrieved_context[:5]
        citations_used = [f"{c.title or c.document_id}: {(c.snippet or '')[:80]}" for c in cited]
        caution = [
            "Deterministic risk engine and approval workflow are the decision authority.",
            "This explanation does not constitute financial advice.",
        ]
        if agent.approval_required:
            caution.append("Human approval is required before any paper execution.")

        risk_label = analysis.risk_level.value if analysis.risk_level is not None else "unknown"
        limitations = [
            "Real exchange execution is disabled.",
            f"Risk level (deterministic): {risk_label}.",
            f"Approval status: {analysis.approval_status}.",
            f"Market data quality: {analysis.market_data_quality}.",
        ]

        return TradingNarrativeDetail(
            summary=analysis.summary,
            setup_interpretation=(
                f"Setup type: {analysis.setup_type or 'none'}. "
                "Interpretation follows deterministic strategy evaluation."
            ),
            evidence_explanation=(
                "; ".join(analysis.evidence) if analysis.evidence else "No evidence recorded."
            ),
            risk_explanation=(
                f"Risk level: {analysis.risk_level.value if analysis.risk_level else 'unknown'}. "
                f"{analysis.stop_loss_or_no_trade_reason}"
            ),
            invalidation_explanation=(
                analysis.invalidation or "No active trade proposal — invalidation not applicable."
            ),
            next_decision_point=(
                analysis.next_decision_point or "Review inputs and re-run analysis."
            ),
            caution_notes=caution,
            limitations=limitations,
            paper_mode_disclaimer=analysis.paper_mode_disclaimer
            or "Paper mode only — no real exchange execution.",
            citations_used=citations_used,
            cited_chunk_ids=[str(c.chunk_id) for c in cited],
        )

    def _call_llm(
        self,
        prompt_name: str,
        context: dict[str, Any],
        agent: AgentState,
    ) -> LLMCompletionResult:
        template = load_prompt(prompt_name)
        context_json = json.dumps(redact_mapping(context), default=str)
        filled = template.replace("{{context_json}}", context_json)
        system_content = f"{_NARRATIVE_TASK_MARKER}{prompt_name}\n{filled}"
        messages = [
            LLMMessage(role="system", content=system_content),
            LLMMessage(
                role="user",
                content="Produce the narrative JSON for the structured context above.",
            ),
        ]
        if self._router is not None:
            from app.schemas.model_routing import (
                ModelContextScope,
                ModelResourceType,
                ModelRetentionCategory,
                ModelRoutingPurpose,
                ModelTaskRequest,
            )

            result = self._router.complete(
                ModelTaskRequest(
                    purpose=ModelRoutingPurpose.NARRATIVE_SYNTHESIS,
                    context=ModelContextScope(
                        organization_id=agent.organization_id,
                        user_id=agent.user_id,
                        purpose=ModelRoutingPurpose.NARRATIVE_SYNTHESIS,
                        resource_type=ModelResourceType.CONVERSATION,
                        retention_category=ModelRetentionCategory.STANDARD,
                    ),
                    correlation_id=agent.request_id,
                    caller_organization_id=agent.organization_id,
                    caller_user_id=agent.user_id,
                    temperature=0.0,
                    response_format=narrative_response_format(),
                ),
                messages,
            )
            parsed = result.parsed_output
            return LLMCompletionResult(
                content=result.content,
                model=result.resolved_model or self._model,
                provider=result.provider or self._llm.name,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.total_latency_ms or 0.0,
                fallback_used=result.fallback_used or result.unavailable,
                parsed_json=parsed if isinstance(parsed, dict) else None,
            )
        return self._llm.complete(
            LLMCompletionRequest(
                messages=messages,
                model=self._model,
                temperature=0.0,
                max_tokens=900,
                response_format=narrative_response_format(),
            )
        )


def narrative_response_format() -> dict[str, Any]:
    """Strict json_schema for TradingNarrativeDetail; parse + fallback stay as defense in depth."""
    return strict_response_format("narrative", TradingNarrativeDetail)


def load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8")


def build_sanitized_narrative_context(
    agent: AgentState, analysis: TradingAnalysisDetail
) -> dict[str, Any]:
    """Structured facts only — no secrets, tokens, or raw auth data."""
    citations = [
        {
            "chunk_id": str(c.chunk_id),
            "document_id": str(c.document_id),
            "source_type": c.source_type.value,
            "title": c.title,
            "snippet": sanitize_retrieved_snippet(c.snippet or ""),
            "memory_kind": (
                "accepted_trading_lesson"
                if c.source_type.value == "review_note"
                else "pending_observation"
                if c.source_type.value == "trade_journal"
                else "reference"
            ),
        }
        for c in agent.retrieved_context[:8]
    ]
    return {
        "deterministic_analysis": analysis.model_dump(mode="json"),
        "intent": agent.intent.value,
        "approval_required": agent.approval_required,
        "has_trade_proposal": agent.trade_proposal is not None,
        "market_data_quality": analysis.market_data_quality,
        "paper_mode_only": True,
        "real_execution_disabled": True,
        "risk_result_action": (
            agent.risk_result.action.value if agent.risk_result is not None else None
        ),
        "citations": citations,
        "confidence": agent.confidence,
        "message_excerpt": agent.message[:300],
    }


def format_reply_with_narrative(
    analysis: TradingAnalysisDetail,
    narrative: TradingNarrativeDetail,
    metadata: NarrativeMetadata,
) -> str:
    """Human-readable reply including required safety fields for output_validation."""
    confidence_line = (
        f"Confidence: {analysis.confidence:.2f}"
        if analysis.confidence is not None
        else "Confidence: n/a"
    )
    source_line = (
        f"Narrative source: {metadata.source} ({metadata.provider}"
        f"{', fallback' if metadata.fallback_used else ''})"
    )
    lines = [
        f"Summary: {narrative.summary}",
        f"Setup interpretation: {narrative.setup_interpretation}",
        f"Evidence: {narrative.evidence_explanation}",
        f"Risk level: {analysis.risk_level.value if analysis.risk_level else 'unknown'}",
        f"Risk explanation: {narrative.risk_explanation}",
        confidence_line,
        f"Invalidation: {narrative.invalidation_explanation}",
        f"Stop loss / no-trade: {analysis.stop_loss_or_no_trade_reason}",
        f"Approval status: {analysis.approval_status}",
        f"Next decision point: {narrative.next_decision_point}",
        f"Market data: {analysis.market_data_quality} (do not treat mock data as live prices).",
        f"Citations: {len(narrative.citations_used)} reference(s) in narrative.",
        f"Limitations: {'; '.join(narrative.limitations)}",
        f"Caution: {'; '.join(narrative.caution_notes)}",
        f"Paper mode: {narrative.paper_mode_disclaimer}",
        "Real exchange execution disabled by default.",
        source_line,
        "Note: Deterministic analysis and risk engine remain the source of truth; "
        "LLM narrative only clarifies explanation.",
    ]
    return "\n".join(lines)


def _parse_narrative(parsed: dict[str, Any] | None) -> TradingNarrativeDetail | None:
    if parsed is None:
        return None
    try:
        return TradingNarrativeDetail.model_validate(parsed)
    except Exception:
        return None


def _usage_from_llm(agent: AgentState, result: LLMCompletionResult, *, feature: str) -> UsageEvent:
    input_tokens = result.input_tokens or max(len(agent.message) // 4, 1)
    output_tokens = result.output_tokens or 64
    return UsageEvent(
        request_id=agent.request_id,
        organization_id=agent.organization_id,
        user_id=agent.user_id,
        feature=feature,
        model=result.model,
        provider=result.provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        fallback_used=result.fallback_used,
        latency_ms=result.latency_ms,
        timestamp=datetime.now(UTC),
    )
