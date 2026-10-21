"""SFP family adapter inside evaluate_setup; no Candidate or account authority."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid5

from app.market_contracts.enums import Finality, MarketType
from app.market_contracts.errors import MarketContractError
from app.market_contracts.ohlcv import OhlcvBar
from app.schemas.nested_continuation import BrainSetupState, EvidenceAvailability
from app.signal_fusion.adapters import AssessmentCommand, evidence_window_from_assessment_command
from app.signal_fusion.assessment import SetupAssessment, build_setup_assessment
from app.signal_fusion.enums import AssessmentReasonCode, SetupAssessmentState
from app.signal_fusion.first_slice_types import FirstSliceEvidenceBundle
from app.signal_fusion.order_flow_inputs import ORDER_FLOW_ROLES, require_bound_order_flow
from app.signal_fusion.policy import FusionPolicy
from app.signal_fusion.types import RuleResult
from app.strategy_brain.sfp.contracts import SFP_NAMESPACE, SfpDetection, SfpScan, SfpSpec
from app.strategy_brain.sfp.detector import detect_sfp


def select_detection(scan: SfpScan, bars: tuple[OhlcvBar, ...]) -> SfpDetection | None:
    # One canonical scan currently admits one episode. Current confirmations take
    # precedence; order is stable when distinct structural levels confirm together.
    latest = {}
    for event in scan.events:
        latest[event.setup_id] = event
    confirmed = [
        e
        for e in latest.values()
        if e.state is BrainSetupState.CONFIRMED
        and not e.provisional
        and bars
        and e.confirmation_observation_id == e.evidence.observation_id
        and e.evidence.source_event_id == bars[-1].source_event_id
    ]
    if confirmed:
        return min(confirmed, key=lambda e: str(e.setup_id))
    return max(
        latest.values(), key=lambda e: (e.observed_at, e.event_time, str(e.event_id)), default=None
    )


def evaluate_sfp_setup(
    *,
    policy: FusionPolicy,
    command: AssessmentCommand,
    evidence: FirstSliceEvidenceBundle,
    evaluated_at: datetime,
    spec: object,
) -> SetupAssessment:
    spec = SfpSpec.model_validate(spec)
    window = evidence_window_from_assessment_command(command)
    bars = evidence.bars_15m
    # The entire per-candle proof is selected into the canonical evidence hash.
    observations = tuple(
        o
        for o, role in zip(command.public_observations, command.selected_roles, strict=True)
        if role.value == "structure"
    )
    scan = detect_sfp(
        bars,
        observations,
        spec,
        evaluated_at=evaluated_at,
        tick_size=evidence.tick_size,
    )
    latest = select_detection(scan, bars)
    trigger_proofs = tuple(
        o
        for o, role in zip(command.public_observations, command.selected_roles, strict=True)
        if role.value == "trigger_ohlcv"
    )
    identity_ok = (
        command.evidence_identity.instrument.provider_symbol == spec.symbol
        and command.evidence_identity.market_type is MarketType.PERPETUAL
        and command.direction == spec.direction
        and command.evidence_identity.timeframe == spec.trigger_timeframe
    )
    bound = bool(bars) and (
        command.trigger.natural_event_id == bars[-1].source_event_id
        and command.trigger.revision == bars[-1].revision
        and len(trigger_proofs) == 1
        and trigger_proofs[0] in observations
        and trigger_proofs[0].payload_content_hash == bars[-1].content_hash
        and trigger_proofs[0].source_event_id == bars[-1].source_event_id
    )
    policy_ok = (
        policy.required_roles == command.mandatory_evidence_roles
        and policy.strategy_version_id == command.strategy_version_id
        and policy.executable_setup == command.executable_setup
        and policy.organization_id == command.organization_id
        and policy.role_timeframes == command.role_timeframes
    )
    available = scan.required_evidence is EvidenceAvailability.AVAILABLE
    order_flow_required = any(role in ORDER_FLOW_ROLES for role in policy.required_roles)
    order_flow_ok = True
    if order_flow_required:
        try:
            require_bound_order_flow(
                evidence.order_flow,
                command=command,
                required_roles=policy.required_roles,
                evaluated_at=evaluated_at,
                trigger_end=bars[-1].interval_end if bars else None,
            )
        except (MarketContractError, ValueError):
            order_flow_ok = False
    final = bool(bars) and bars[-1].finality is Finality.FINAL and bars[-1].provider_complete
    current_confirmation = (
        latest is not None
        and latest.state is BrainSetupState.CONFIRMED
        and (
            not latest.provisional
            and latest.confirmation_observation_id == latest.evidence.observation_id
            and latest.evidence.source_event_id == bars[-1].source_event_id
            and latest.observed_at <= evaluated_at < latest.expires_at
        )
    )
    checks: tuple[tuple[str, bool], ...] = (
        ("market_identity", identity_ok),
        ("trigger_binding", bound),
        ("compiled_policy_binding", policy_ok),
        ("required_sfp_evidence", available),
        ("closed_candle_confirmation", final),
        ("sfp_confirmation", current_confirmation),
    )
    if order_flow_required:
        checks += (("required_order_flow_binding", order_flow_ok),)
    mapping = {
        BrainSetupState.FORMING: SetupAssessmentState.PARTIAL_MATCH,
        BrainSetupState.CONFIRMED: SetupAssessmentState.WATCH,
        BrainSetupState.INVALIDATED: SetupAssessmentState.INVALIDATED,
        BrainSetupState.EXPIRED: SetupAssessmentState.EXPIRED,
    }
    quality = identity_ok and bound and policy_ok and available and order_flow_ok
    state = (
        mapping.get(latest.state, SetupAssessmentState.NO_SETUP)
        if latest and quality
        else SetupAssessmentState.NO_SETUP
    )
    if quality and final and current_confirmation:
        state = SetupAssessmentState.CONFIRMED_SETUP
    reason = AssessmentReasonCode.PRECONDITIONS_PASSED
    if state is SetupAssessmentState.CONFIRMED_SETUP:
        reason = AssessmentReasonCode.ALL_MANDATORY_EVIDENCE_CONFIRMED
    elif not quality:
        reason = (
            AssessmentReasonCode.REQUIRED_SOURCE_STALE
            if scan.required_evidence is EvidenceAvailability.STALE
            else AssessmentReasonCode.MARKET_DISQUALIFIER
        )
    return build_setup_assessment(
        assessment_id=uuid5(SFP_NAMESPACE, f"assessment:{window.content_hash}:{state}"),
        organization_id=command.organization_id,
        strategy_version_id=command.strategy_version_id,
        executable_setup=command.executable_setup,
        fusion_policy_version=command.fusion_policy_version,
        observation_ids=tuple(dict.fromkeys(o.observation_id for o in command.public_observations)),
        assessment_window=window.interval,
        state=state,
        rule_results=tuple(
            RuleResult(
                rule_id=name,
                passed=bool(passed),
                reason_code=None if passed else name,
                weight=Decimal(1),
            )
            for name, passed in checks
        ),
        threshold=Decimal(1),
        reason_codes=(reason,),
        explanation=(
            "SFP: "
            + ", ".join(latest.reason_codes if latest else scan.reason_codes or ("no setup",))
        )[:500],
        evidence_window_hash=window.content_hash,
        assessed_at=evaluated_at,
        valid_until=latest.expires_at
        if latest and latest.expires_at > evaluated_at
        else evaluated_at + timedelta(seconds=1),
        correlation_id=command.correlation_id
        or uuid5(SFP_NAMESPACE, f"correlation:{window.content_hash}"),
    )
