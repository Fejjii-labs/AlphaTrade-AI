"""Typed LLM narrative output — explanation only, never decision authority."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import StrictModel


class TradingNarrativeDetail(StrictModel):
    """Schema-validated LLM narrative polish. Extra fields forbidden."""

    summary: str
    setup_interpretation: str
    evidence_explanation: str
    risk_explanation: str
    invalidation_explanation: str
    next_decision_point: str
    caution_notes: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    paper_mode_disclaimer: str
    citations_used: list[str] = Field(default_factory=list)
    cited_chunk_ids: list[str] = Field(
        default_factory=list,
        description=(
            "chunk_id values, copied from the supplied citations, that support this "
            "narrative. Ids outside the retrieved set are rejected."
        ),
    )


class NarrativeMetadata(StrictModel):
    """How the narrative layer was produced (for UI transparency)."""

    source: str = Field(description="llm | deterministic_fallback")
    provider: str
    model: str
    fallback_used: bool = False
    validation_passed: bool = True
    latency_ms: float | None = None
