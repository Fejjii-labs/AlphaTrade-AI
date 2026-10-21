"""Deterministic structured Candidate alert content and Telegram outbox text.

Facts are copied from canonical Candidate / SetupAssessment / CanonicalEvidenceWindowV1.
No profitability claim. No LLM-authored setup truth.
"""

from __future__ import annotations

from decimal import Decimal

from app.candidate_alerts.contracts import (
    MAX_ALERT_TEXT_BYTES_GUARD,
    CandidateAlertContent,
    EvidenceFreshnessSummary,
    EvidenceProvenanceSummary,
    InvalidationSummary,
    RuleResultSummary,
    TriggerContext,
)
from app.market_contracts.enums import Finality
from app.market_contracts.first_slice import FIRST_SLICE_PATTERN_NAME
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.enums import CandidateState, SetupAssessmentState
from app.signal_fusion.evidence_window import CanonicalEvidenceWindowV1
from app.signal_fusion.types import RuleResult

_PROFIT_CLAIMS = (
    "profit",
    "profitable",
    "guaranteed",
    "will win",
    "expected return",
)


def _price_text(value: object) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def canonical_setup_name(assessment: SetupAssessment) -> str:
    """Prefer the first-slice canonical pattern name when the assessment already carries it."""
    explanation = assessment.explanation.strip()
    if explanation.startswith(FIRST_SLICE_PATTERN_NAME):
        return FIRST_SLICE_PATTERN_NAME
    return f"compiled-setup:{assessment.executable_setup.setup_definition_id}"


def _rule_summaries(results: tuple[RuleResult, ...]) -> tuple[RuleResultSummary, ...]:
    return tuple(
        RuleResultSummary(rule_id=item.rule_id, passed=item.passed, reason_code=item.reason_code)
        for item in results
    )


def _freshness_rule_passed(results: tuple[RuleResult, ...]) -> bool | None:
    for item in results:
        if item.rule_id == "freshness":
            return item.passed
    return None


def build_candidate_alert_content(
    *,
    candidate: Candidate,
    assessment: SetupAssessment,
    window: CanonicalEvidenceWindowV1,
) -> CandidateAlertContent:
    """Project canonical records into structured alert facts."""
    invalidated = candidate.state is CandidateState.INVALIDATED or (
        assessment.state is SetupAssessmentState.INVALIDATED
    )
    reason_codes = tuple(code.value for code in assessment.reason_codes)
    selected_final = all(
        item.finality is Finality.FINAL for item in window.selected_public_observations
    )
    source_families = tuple(sorted({item.source_family.value for item in window.source_set}))
    observation_hashes = tuple(item.content_hash for item in window.selected_public_observations)
    return CandidateAlertContent(
        instrument=candidate.evidence_instrument,
        direction=candidate.direction,
        setup_name=canonical_setup_name(assessment),
        setup_state=assessment.state,
        candidate_state=candidate.state,
        trigger_context=TriggerContext(
            natural_event_id=window.trigger.natural_event_id,
            revision=window.trigger.revision,
            timeframe=window.timeframe,
            interval_start=window.interval.start,
            interval_end=window.interval.end,
        ),
        invalidation=InvalidationSummary(
            candidate_state=candidate.state,
            assessment_state=assessment.state,
            invalidated=invalidated,
            reason_codes=reason_codes,
        ),
        expiry=candidate.valid_until,
        rule_results=_rule_summaries(assessment.rule_results),
        evidence_freshness=EvidenceFreshnessSummary(
            freshness_policy_version=window.freshness_policy_version,
            finality_policy_version=window.finality_policy_version,
            all_selected_observations_final=selected_final,
            freshness_rule_passed=_freshness_rule_passed(assessment.rule_results),
        ),
        evidence_provenance=EvidenceProvenanceSummary(
            venue=window.evidence_venue,
            market_type=window.evidence_market,
            instrument=window.evidence_instrument,
            source_families=source_families,
            selected_observation_hashes=observation_hashes,
            tenant_assertion_count=len(window.tenant_assertions),
        ),
        candidate_id=candidate.candidate_id,
        candidate_revision=candidate.transition_version,
    )


def format_candidate_alert_text(content: CandidateAlertContent) -> str:
    """Deterministic private-chat text. Not a trade order and not a performance claim."""
    if content.nested is not None:
        nested = content.nested
        return "\n".join(
            (
                "AlphaTrade PAPER informational Nested Continuation setup.",
                f"Symbol: {nested.symbol}",
                f"Venue: {content.evidence_provenance.venue.value}",
                f"Timeframe: {content.trigger_context.timeframe.value}",
                f"Nested stage: {nested.stage}",
                f"Direction: {content.direction.value}",
                f"Entry: {_price_text(nested.entry)}",
                f"Stop: {_price_text(nested.stop)}",
                f"Target: {_price_text(nested.target)}",
                f"Strategy version: {nested.strategy_version_id}",
                f"Setup ID: {nested.setup_id}",
                f"Evidence timestamp: {nested.evidence_at.isoformat()}",
                f"Freshness: {nested.freshness}",
                f"Decision: {nested.decision}",
                f"Risk state: {nested.risk_state}",
                f"Reason summary: {', '.join(nested.reasons)[:500]}",
            )
        )
    rules = ", ".join(
        f"{item.rule_id}={'pass' if item.passed else 'fail'}" for item in content.rule_results
    )
    if not rules:
        rules = "none"
    freshness_rule = content.evidence_freshness.freshness_rule_passed
    freshness_bit = "unknown" if freshness_rule is None else ("pass" if freshness_rule else "fail")
    invalidation = (
        "invalidated"
        if content.invalidation.invalidated
        else "not invalidated; bound to candidate expiry"
    )
    lines = (
        "AlphaTrade paper candidate alert. Not a trade. Not a performance claim.",
        f"Instrument: {content.instrument}",
        f"Direction: {content.direction.value}",
        f"Setup: {content.setup_name}",
        f"Setup state: {content.setup_state.value}",
        f"Candidate state: {content.candidate_state.value}",
        (
            "Trigger: "
            f"{content.trigger_context.natural_event_id} "
            f"rev {content.trigger_context.revision} "
            f"{content.trigger_context.timeframe.value} "
            f"[{content.trigger_context.interval_start.isoformat()} , "
            f"{content.trigger_context.interval_end.isoformat()})"
        ),
        f"Expiry: {content.expiry.isoformat()}",
        f"Invalidation: {invalidation}",
        f"Rules: {rules}",
        (
            "Evidence freshness: "
            f"policy {content.evidence_freshness.freshness_policy_version}; "
            f"finality {content.evidence_freshness.finality_policy_version}; "
            f"selected_final={content.evidence_freshness.all_selected_observations_final}; "
            f"freshness_rule={freshness_bit}"
        ),
        (
            "Provenance: "
            f"{content.evidence_provenance.venue.value}/"
            f"{content.evidence_provenance.market_type.value}/"
            f"{content.evidence_provenance.instrument}; "
            f"sources={','.join(content.evidence_provenance.source_families)}"
        ),
        f"Candidate: {content.candidate_id} revision {content.candidate_revision}",
        (
            "APPROVE records authorization intent only and never executes. "
            "EXECUTE_PAPER_PLAN remains outside Telegram. CLOSE is unavailable."
        ),
    )
    text = "\n".join(lines)
    lowered = text.lower()
    for claim in _PROFIT_CLAIMS:
        if claim in lowered:
            raise ValueError("Candidate alert text must not contain profitability claims.")
    if len(text) > MAX_ALERT_TEXT_BYTES_GUARD:
        raise ValueError("Candidate alert text exceeds Telegram outbox bounds.")
    return text
