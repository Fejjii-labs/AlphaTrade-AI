"""Validate final agent outputs before returning to clients."""

from __future__ import annotations

import re

from app.guardrails.citations import CITATION_NOT_IN_RETRIEVED_SET, verify_cited_chunk_ids
from app.guardrails.patterns import compile_rules, match_rules
from app.guardrails.types import GuardrailInput, GuardrailResult, GuardrailSeverity

_FORBIDDEN_SPECS: list[tuple[str, str, str]] = [
    ("guarantee_language", r"guaranteed\s+profit|sure\s+win|risk[- ]?free", "Guarantee language"),
    (
        "hidden_execution_claim",
        r"already\s+executed\s+on\s+exchange|order\s+placed\s+live|filled\s+on\s+binance",
        "Hidden execution claim",
    ),
]

_FORBIDDEN_RULES = compile_rules(_FORBIDDEN_SPECS)

_FALLBACK = (
    "I cannot return the prior response because it did not meet trading safety requirements. "
    "Ask for analysis that includes risk level, confidence, limitations, approval status, "
    "invalidation for any trade idea, and stop-loss context. Real exchange execution is "
    "disabled; paper mode requires explicit human approval."
)


class OutputValidationGuardrail:
    """Ensure trading-related replies include required safety fields."""

    def evaluate(self, data: GuardrailInput) -> GuardrailResult:
        text = (data.final_answer or "").strip()
        if not text:
            return GuardrailResult.block(
                rule_id="empty_output",
                reason="Empty agent output.",
                severity=GuardrailSeverity.HIGH,
                safe_message=_FALLBACK,
            )

        forbidden = match_rules(text, _FORBIDDEN_RULES)
        if forbidden:
            return GuardrailResult.block(
                rule_id=forbidden[0],
                reason="Output contains forbidden guarantee or execution claims.",
                severity=GuardrailSeverity.HIGH,
                safe_message=_FALLBACK,
                metadata={"forbidden_rules": forbidden},
            )

        if not self._is_trading_output(data, text):
            return GuardrailResult.pass_(reason="Non-trading output accepted.")

        missing = self._missing_trading_fields(data, text)
        grounding = _grounding(data)
        if not grounding.startswith("grounded:"):
            missing.append(grounding)
        if missing:
            return GuardrailResult(
                allowed=False,
                blocked=True,
                severity=GuardrailSeverity.HIGH,
                reason="Trading output missing required safety fields.",
                triggered_rules=missing,
                safe_message=_FALLBACK,
                audit_required=True,
                metadata={
                    "guardrail": "output_validation",
                    "missing": missing,
                    "grounding": grounding,
                },
            )
        return GuardrailResult.pass_(reason=f"Trading output validated ({grounding}).")

    def _is_trading_output(self, data: GuardrailInput, text: str) -> bool:
        if data.has_trade_proposal:
            return True
        intent = str(data.metadata.get("intent") or "")
        if intent in {
            "strategy_proposal_confirm",
            "strategy_proposal_reject",
        }:
            return False
        if intent in {
            "market_analysis",
            "setup_analysis",
            "plan_trade",
            "execute_paper_plan",
            "monitor",
            "review_trade",
        }:
            return True
        lowered = text.lower()
        return any(
            token in lowered
            for token in (
                "trade proposal",
                "approval status",
                "confidence",
                "risk",
                "stop loss",
                "invalidation",
            )
        )

    def _missing_trading_fields(self, data: GuardrailInput, text: str) -> list[str]:
        missing: list[str] = []
        lowered = text.lower()

        if not re.search(r"risk\s*level|risk_level", lowered):
            missing.append("risk_level")

        if "confidence" not in lowered and data.confidence is None:
            missing.append("confidence")

        if "limitation" not in lowered:
            missing.append("limitations")

        if "approval status" not in lowered and "approval_status" not in lowered:
            missing.append("approval_status")

        if data.has_trade_proposal and "invalidation" not in lowered:
            missing.append("invalidation")

        has_stop = "stop loss" in lowered or "stop-loss" in lowered
        has_no_trade_reason = any(
            phrase in lowered
            for phrase in (
                "no trade",
                "not take",
                "do not trade",
                "no executable proposal",
            )
        )
        if not has_stop and not has_no_trade_reason:
            missing.append("stop_loss_or_no_trade_reason")

        return missing


def _grounding(data: GuardrailInput) -> str:
    """Structured groundedness; prose mentioning "citation" no longer counts.

    Grounded when the answer cites >= 1 chunk id and every cited id was retrieved
    this turn, or when retrieval reported an explicit outcome with nothing to cite
    (no relevant source, or retrieval unavailable; the reply discloses either).
    Returns ``grounded:<basis>`` or the failing rule id.
    """
    check = verify_cited_chunk_ids(data.cited_chunk_ids, data.retrieved_chunk_ids)
    if check.unknown:
        return CITATION_NOT_IN_RETRIEVED_SET
    if check.grounded:
        return "grounded:cited_retrieved_chunks"
    if data.rag_outcome == "no_relevant_source":
        return "grounded:no_relevant_source"
    if data.rag_outcome == "retrieval_failed":
        return "grounded:retrieval_unavailable"
    return "citations_or_context"
