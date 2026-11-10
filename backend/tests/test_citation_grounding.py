"""Structured citation verification replaces the "citation" substring heuristic (item 4)."""

from __future__ import annotations

import json
import uuid

from app.agents.nodes import memory_update
from app.guardrails.citations import CITATION_NOT_IN_RETRIEVED_SET, verify_cited_chunk_ids
from app.guardrails.output_validation import OutputValidationGuardrail
from app.guardrails.types import GuardrailInput
from app.providers.llm import LLMCompletionRequest, LLMCompletionResult, MockLLMProvider
from app.schemas.agent import AgentState
from app.schemas.analysis import TradingAnalysisDetail
from app.schemas.common import DocumentSourceType, RiskSeverity
from app.schemas.rag import Citation
from app.services.narrative_service import NarrativeService

RETRIEVED = str(uuid.uuid4())
FOREIGN = str(uuid.uuid4())

# Contains every required trading safety field and the word "citation".
ANSWER = (
    "Risk level: medium. Confidence: 0.55. Limitations: mock data. "
    "Approval status: not_required. No trade: stop loss not applicable. "
    "Citation: see the playbook and retrieved context."
)


def _validate(**kwargs: object):
    return OutputValidationGuardrail().evaluate(
        GuardrailInput(
            message="analyze btc",
            final_answer=ANSWER,
            metadata={"intent": "market_analysis"},
            **kwargs,
        )
    )


def test_word_citation_without_ids_fails() -> None:
    result = _validate(retrieved_chunk_ids=[RETRIEVED], rag_outcome="retrieved")
    assert result.blocked
    assert "citations_or_context" in result.triggered_rules


def test_no_reported_outcome_and_no_ids_fails() -> None:
    result = _validate()
    assert result.blocked and "citations_or_context" in result.triggered_rules


def test_ids_outside_retrieved_set_fail() -> None:
    result = _validate(
        cited_chunk_ids=[RETRIEVED, FOREIGN],
        retrieved_chunk_ids=[RETRIEVED],
        rag_outcome="retrieved",
    )
    assert result.blocked
    assert CITATION_NOT_IN_RETRIEVED_SET in result.triggered_rules


def test_valid_ids_pass() -> None:
    result = _validate(
        cited_chunk_ids=[RETRIEVED], retrieved_chunk_ids=[RETRIEVED], rag_outcome="retrieved"
    )
    assert result.allowed
    assert result.reason == "Trading output validated (grounded:cited_retrieved_chunks)."


def test_no_relevant_source_outcome_passes_with_reason() -> None:
    result = _validate(rag_outcome="no_relevant_source")
    assert result.allowed
    assert result.reason == "Trading output validated (grounded:no_relevant_source)."


def test_retrieval_unavailable_outcome_passes_with_reason() -> None:
    result = _validate(rag_outcome="retrieval_failed")
    assert result.allowed and "retrieval_unavailable" in (result.reason or "")


def _citation(chunk_id: str) -> Citation:
    return Citation(
        document_id=uuid.uuid4(),
        chunk_id=uuid.UUID(chunk_id),
        title="Risk Policy",
        source_type=DocumentSourceType.RISK_POLICY,
        snippet="Every trade requires a stop loss.",
    )


def test_has_citations_is_not_true_merely_because_something_was_retrieved() -> None:
    state = AgentState(request_id="r", retrieved_context=[_citation(RETRIEVED)])
    data = GuardrailInput.from_agent_state(state)
    assert data.has_citations is False
    assert data.retrieved_chunk_ids == [RETRIEVED]
    cited = GuardrailInput.from_agent_state(
        state.model_copy(update={"cited_chunk_ids": [uuid.UUID(RETRIEVED)]})
    )
    assert cited.has_citations is True


def test_verify_cited_chunk_ids_normalizes_and_dedupes() -> None:
    check = verify_cited_chunk_ids([RETRIEVED.upper(), RETRIEVED, FOREIGN], [RETRIEVED])
    assert check.valid == (RETRIEVED,) and check.unknown == (FOREIGN,)
    assert not check.grounded
    assert verify_cited_chunk_ids([], [RETRIEVED]).grounded is False


def test_memory_update_no_longer_fabricates_a_citation() -> None:
    state = AgentState(request_id="r", message="hello").model_dump(mode="json")
    patched = memory_update(state, runtime=None)  # type: ignore[arg-type]
    assert AgentState.model_validate(patched).citations == []


class _CitingLLM(MockLLMProvider):
    def __init__(self, cited: list[str]) -> None:
        super().__init__()
        self.cited = cited

    def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
        result = super().complete(request)
        payload = dict(result.parsed_json or {})
        payload["cited_chunk_ids"] = self.cited
        content = json.dumps(payload)
        return LLMCompletionResult(
            content=content,
            model=result.model,
            provider=result.provider,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            fallback_used=result.fallback_used,
            parsed_json=payload,
        )


def _enhance(cited: list[str]):
    analysis = TradingAnalysisDetail.model_validate(
        {
            "summary": "No trade.",
            "risk_level": RiskSeverity.MEDIUM,
            "stop_loss_or_no_trade_reason": "No trade.",
            "approval_status": "not_required",
            "market_data_quality": "mock",
        }
    )
    agent = AgentState(
        request_id="r",
        message="analyze btc",
        retrieved_context=[_citation(RETRIEVED)],
        citations=[_citation(RETRIEVED)],
    )
    return NarrativeService(llm_provider=_CitingLLM(cited), llm_model="m").enhance(agent, analysis)


def test_llm_narrative_citing_retrieved_ids_is_kept() -> None:
    result = _enhance([RETRIEVED])
    assert result.metadata.source == "llm"
    assert result.narrative.cited_chunk_ids == [RETRIEVED]
    assert result.citation_violations == ()


def test_llm_narrative_citing_foreign_id_is_rejected_and_stripped() -> None:
    result = _enhance([RETRIEVED, FOREIGN])
    assert result.metadata.source == "deterministic_fallback"
    assert result.metadata.validation_passed is False
    assert result.citation_violations == (FOREIGN,)
    # Deterministic narrative cites retrieved chunks by construction.
    assert result.narrative.cited_chunk_ids == [RETRIEVED]


def test_llm_narrative_ignoring_retrieved_sources_falls_back() -> None:
    result = _enhance([])
    assert result.metadata.source == "deterministic_fallback"
    assert result.narrative.cited_chunk_ids == [RETRIEVED]
