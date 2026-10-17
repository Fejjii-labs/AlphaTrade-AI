"""TrendPulse receipts enter the existing evaluate_setup and Candidate authorities."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid5

from app.evidence_pipeline.canonical import semantic_source_from_identity
from app.signal_fusion.adapters import AssessmentCommand, evidence_window_from_assessment_command
from app.signal_fusion.assessment import SetupAssessment, build_setup_assessment
from app.signal_fusion.enums import (
    AssessmentReasonCode,
    EvidenceAdapterKind,
    EvidenceRole,
    SetupAssessmentState,
)
from app.signal_fusion.first_slice_types import FirstSliceEvidenceBundle
from app.signal_fusion.policy import FusionPolicy
from app.signal_fusion.types import HalfOpenInterval, RuleResult, TriggerIdentity
from app.strategy_brain.trendpulse_1r.adapter import evaluate_trendpulse
from app.strategy_brain.trendpulse_1r.contracts import (
    TRENDPULSE_NAMESPACE,
    TrendPulseRequest,
    TrendPulseSpec,
    TrendPulseStatus,
    exact_hash,
)


def command_from_receipts(policy: FusionPolicy, request: TrendPulseRequest) -> AssessmentCommand:
    """Bind original receipt hashes, both histories and exact instrument rules.

    No arrival clock is rewritten. The rule hash occupies the derivation trigger
    identity, so changed precision cannot alias the same canonical Candidate.
    """
    trigger = next(b for b in request.entry_bars if b.interval_end == request.trigger_end)
    identity = next(
        o.identity
        for o in request.entry_observations
        if o.source_event_id == trigger.source_event_id
    )
    return AssessmentCommand(
        organization_id=policy.organization_id,
        strategy_version_id=policy.strategy_version_id,
        executable_setup=policy.executable_setup,
        fusion_policy_version=policy.policy_version,
        freshness_policy_version=policy.freshness_policy_version,
        finality_policy_version=policy.finality_policy_version,
        direction=request.spec.direction,
        evidence_identity=identity,
        interval=HalfOpenInterval(start=trigger.interval_start, end=trigger.interval_end),
        trigger=TriggerIdentity(
            natural_event_id="trendpulse:"
            + exact_hash(
                {
                    "event": trigger.source_event_id,
                    "rules": request.instrument_rules.model_dump(),
                }
            ),
            revision=trigger.revision,
        ),
        mandatory_evidence_roles=policy.required_roles,
        public_observations=request.entry_observations + request.trend_observations,
        selected_roles=(EvidenceRole.TRIGGER_OHLCV,) * len(request.entry_observations)
        + (EvidenceRole.CONTEXT_OHLCV,) * len(request.trend_observations),
        role_timeframes=policy.role_timeframes,
        source_set=(semantic_source_from_identity(identity),),
        adapter_kind=EvidenceAdapterKind.DETECTOR,
        correction_selection_policy=policy.correction_selection_policy,
    )


def evaluate_trendpulse_setup(
    *,
    policy: FusionPolicy,
    command: AssessmentCommand,
    evidence: FirstSliceEvidenceBundle,
    evaluated_at: datetime,
    spec: object,
) -> SetupAssessment:
    """Re-evaluate fixed rules; a stored research signal never supplies authority."""
    authored = TrendPulseSpec.model_validate(spec)
    request = evidence.trendpulse
    if request is None or request.spec != authored:
        raise ValueError("trendpulse_receipts_missing_or_spec_mismatch")
    if command != command_from_receipts(policy, request):
        raise ValueError("trendpulse_canonical_command_mismatch")
    window = evidence_window_from_assessment_command(command)
    result = evaluate_trendpulse(
        request.spec,
        trend_bars=request.trend_bars,
        trend_observations=request.trend_observations,
        entry_bars=request.entry_bars,
        entry_observations=request.entry_observations,
        instrument_rules=request.instrument_rules,
        trigger_end=request.trigger_end,
        evaluated_at=evaluated_at,
        seen_signal_ids=request.seen_signal_ids,
    )
    qualified = result.status is TrendPulseStatus.QUALIFIED
    state = SetupAssessmentState.CONFIRMED_SETUP if qualified else SetupAssessmentState.NO_SETUP
    if result.reason == "trigger_expired":
        state = SetupAssessmentState.EXPIRED
    expires = request.trigger_end + timedelta(seconds=60)
    return build_setup_assessment(
        assessment_id=uuid5(TRENDPULSE_NAMESPACE, f"assessment:{window.content_hash}:{state}"),
        organization_id=command.organization_id,
        strategy_version_id=command.strategy_version_id,
        executable_setup=command.executable_setup,
        fusion_policy_version=command.fusion_policy_version,
        observation_ids=tuple(o.observation_id for o in command.public_observations),
        assessment_window=window.interval,
        state=state,
        rule_results=(
            RuleResult(
                rule_id="trendpulse_fixed_rules",
                passed=qualified,
                reason_code=result.reason,
                weight=Decimal(1),
            ),
        ),
        threshold=Decimal(1),
        reason_codes=(
            AssessmentReasonCode.ALL_MANDATORY_EVIDENCE_CONFIRMED
            if qualified
            else AssessmentReasonCode.MARKET_DISQUALIFIER,
        ),
        explanation=result.reason,
        evidence_window_hash=window.content_hash,
        assessed_at=evaluated_at,
        valid_until=expires if expires > evaluated_at else evaluated_at + timedelta(microseconds=1),
        correlation_id=command.correlation_id or uuid5(TRENDPULSE_NAMESPACE, window.content_hash),
    )
