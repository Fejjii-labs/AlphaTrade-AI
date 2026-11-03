"""Deterministic structured Candidate alert content and Telegram outbox text.

Facts are copied from canonical Candidate / SetupAssessment / CanonicalEvidenceWindowV1.
No profitability claim. No LLM-authored setup truth.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
from app.candidate_alerts.projection import projection_note, projection_tag
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


NESTED_STRATEGY_NAME = "Nested Continuation"
SFP_STRATEGY_NAME = "Swing Failure Pattern"
PAPER_MARKER = "PAPER alert. Not an order."

# Display names for known strategy keys. Any key not listed here (including
# strategies added later) still renders through the same layout via
# ``strategy_display_name``'s readable fallback.
STRATEGY_DISPLAY_NAMES: dict[str, str] = {
    "nested": NESTED_STRATEGY_NAME,
    "nested_continuation": NESTED_STRATEGY_NAME,
    "sfp": SFP_STRATEGY_NAME,
    "swing_failure_pattern": SFP_STRATEGY_NAME,
}


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


def strategy_display_name(key: str) -> str:
    """Readable strategy name for any key: registry first, then a humanized fallback."""
    raw = key.strip()
    normalized = raw.lower().replace("-", "_").replace(" ", "_")
    if normalized in STRATEGY_DISPLAY_NAMES:
        return STRATEGY_DISPLAY_NAMES[normalized]
    if normalized.startswith("compiled-setup:".replace("-", "_")) or raw.startswith(
        "compiled-setup:"
    ):
        # The suffix is an internal setup ID and is never rendered.
        return "Compiled setup"
    if any(ch.isupper() for ch in raw) and " " in raw:
        return raw  # already a human display name
    words = [w for w in normalized.replace(".", "_").split("_") if w]
    return " ".join(w.upper() if len(w) <= 3 else w.capitalize() for w in words) or "Strategy"


def render_strategy_alert_text(
    *,
    symbol: str,
    strategy: str,
    direction: str,
    entry: object,
    stop: object,
    target: object,
    timeframe: str,
    at: datetime,
    nested_stage: str | None = None,
    stage: str | None = None,
    projection_source: str | None = None,
) -> str:
    """The one Telegram alert layout shared by every strategy (current and future).

    Order: symbol, strategy, direction, entry, stop, target, timeframe, stage
    (when present), UTC minute timestamp, PAPER marker, then the Source line for
    borrowed-parameter scans. Internal IDs, strategy version, risk state and
    reason text stay in the stored payload/audit data and are never rendered.
    """
    stage_lines: tuple[str, ...] = ()
    if nested_stage:
        stage_lines = (f"Nested stage: {nested_stage}",)
    elif stage:
        stage_lines = (f"Stage: {stage}",)
    projected: tuple[str, ...] = ()
    if projection_source is not None:
        projected = (
            f"Source: {projection_tag(projection_source)}",
            projection_note(projection_source, symbol=symbol, timeframe=timeframe),
        )
    text = "\n".join(
        (
            f"Symbol: {symbol}",
            f"Strategy: {strategy_display_name(strategy)}",
            f"Direction: {direction}",
            f"Entry: {_price_text(entry)}",
            f"Stop: {_price_text(stop)}",
            f"Target: {_price_text(target)}",
            f"Timeframe: {timeframe}",
            *stage_lines,
            f"Time: {at.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
            PAPER_MARKER,
            *projected,
        )
    )
    lowered = text.lower()
    for claim in _PROFIT_CLAIMS:
        if claim in lowered:
            raise ValueError("Candidate alert text must not contain profitability claims.")
    if len(text.encode("utf-8")) > MAX_ALERT_TEXT_BYTES_GUARD:
        raise ValueError("Candidate alert text exceeds Telegram outbox bounds.")
    return text


def format_candidate_alert_text(content: CandidateAlertContent) -> str:
    """Deterministic private-chat text via the shared layout. Not an order, not a claim."""
    timeframe = content.trigger_context.timeframe.value
    if content.nested is not None:
        nested = content.nested
        return render_strategy_alert_text(
            symbol=nested.symbol,
            strategy="nested",
            direction=content.direction.value,
            entry=nested.entry,
            stop=nested.stop,
            target=nested.target,
            timeframe=timeframe,
            nested_stage=nested.stage,
            at=nested.evidence_at,
            projection_source=nested.projection_source,
        )
    # Generic / any other strategy: same layout. Candidate content carries no plan
    # prices, so those render as "unavailable" rather than inventing values.
    return render_strategy_alert_text(
        symbol=content.instrument,
        strategy=content.setup_name,
        direction=content.direction.value,
        entry=None,
        stop=None,
        target=None,
        timeframe=timeframe,
        at=content.trigger_context.interval_end,
    )
