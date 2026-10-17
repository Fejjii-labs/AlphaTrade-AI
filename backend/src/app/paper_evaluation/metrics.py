"""Deterministic paper-evaluation rollups. Not a trading or strategy authority."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.learning_attribution.contracts import (
    AttributionRecord,
    RiskAdherence,
)
from app.market_contracts.evidence_diagnostics import (
    PROVIDER_FAILURE_REASONS,
    DiagnosticReason,
    canonical_diagnostic_reason,
)
from app.paper_evaluation.contracts import (
    BlockedTradeFacts,
    DataQualityClass,
    DataQualityFacts,
    FalseSignalFacts,
    HumanVsSystemEvaluationFacts,
    MissedOpportunityFacts,
    PaperEvaluationFacts,
    PaperEvaluationObservation,
    PaperEvaluationStage,
    PaperEvaluationWarning,
    RuleAdherenceFacts,
    SetupConversionFacts,
    StrategyPerformanceFacts,
    WatcherPerformanceFacts,
)
from app.paper_evaluation.hashing import hashed_facts
from app.paper_evaluation.ports import JournalTradeMeasurement
from app.schemas.journal_statistics import SampleConfidence, TradeRuleCompliance
from app.signal_fusion.action_eligibility import ActionEligibilityEvaluation
from app.signal_fusion.enums import ActionEligibilityState, SetupAssessmentState

_ZERO = Decimal("0")
_RATE_Q = Decimal("0.0001")
_MONEY_Q = Decimal("0.01")
_CONFIDENCE_LOW = 5
_CONFIDENCE_MODERATE = 20
_CONFIDENCE_HIGH = 50

_STALE_REASONS = frozenset({"stale_evidence", "stale"})
_OUTAGE_REASONS = frozenset({"provider_outage", "canonical_evidence_unavailable"})


@dataclass(frozen=True, slots=True)
class PaperEvaluationInput:
    organization_id: UUID
    generated_at: datetime
    observations: tuple[PaperEvaluationObservation, ...] = ()
    attributions: tuple[AttributionRecord, ...] = ()
    eligibility: tuple[ActionEligibilityEvaluation, ...] = ()
    journal: dict[UUID, JournalTradeMeasurement] | None = None


def rollup_facts(payload: PaperEvaluationInput) -> PaperEvaluationFacts:
    """Compute deterministic measurement facts. Narrative is not an input."""

    generated_at = payload.generated_at
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    observations = tuple(
        item for item in payload.observations if item.organization_id == payload.organization_id
    )
    attributions = tuple(
        item for item in payload.attributions if item.organization_id == payload.organization_id
    )
    eligibility = tuple(
        item
        for item in payload.eligibility
        if item.eligibility.organization_id == payload.organization_id
    )
    journal = payload.journal or {}

    watcher = _watcher_facts(observations)
    conversion = _conversion_facts(observations, attributions, eligibility)
    false_signals = _false_signal_facts(observations, attributions)
    overall, versions = _strategy_facts(attributions, journal)
    rule = _rule_facts(attributions, journal)
    blocked = _blocked_facts(eligibility, observations)
    human = _human_facts(attributions, watcher)
    missed = _missed_facts(attributions, eligibility)
    quality = _data_quality_facts(observations)
    warnings = _warnings(
        watcher=watcher,
        conversion=conversion,
        overall=overall,
        quality=quality,
        observation_count=len(observations),
        attribution_count=len(attributions),
    )
    facts = PaperEvaluationFacts(
        organization_id=payload.organization_id,
        generated_at=generated_at,
        watcher=watcher,
        conversion=conversion,
        false_signals=false_signals,
        strategy_overall=overall,
        strategy_versions=versions,
        rule_adherence=rule,
        blocked=blocked,
        human_vs_system=human,
        missed_opportunities=missed,
        data_quality=quality,
        warnings=warnings,
        content_hash="0" * 64,
    )
    return hashed_facts(facts)


def _rate(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(_RATE_Q)


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values, _ZERO) / Decimal(len(values))).quantize(_MONEY_Q)


def _confidence(sample: int) -> SampleConfidence:
    if sample < _CONFIDENCE_LOW:
        return SampleConfidence.INSUFFICIENT
    if sample < _CONFIDENCE_MODERATE:
        return SampleConfidence.LOW
    if sample < _CONFIDENCE_HIGH:
        return SampleConfidence.MODERATE
    return SampleConfidence.HIGH


def _watcher_facts(observations: tuple[PaperEvaluationObservation, ...]) -> WatcherPerformanceFacts:
    scans = [item for item in observations if item.stage is PaperEvaluationStage.WATCHER_SCAN]
    assessments = [
        item for item in observations if item.stage is PaperEvaluationStage.SETUP_ASSESSMENT
    ]
    reasons = [item.reason_code or "" for item in scans]
    assessment_states = [item.assessment_state for item in assessments]
    candidates = {
        item.candidate_id
        for item in observations
        if item.candidate_id is not None and item.stage is PaperEvaluationStage.CANDIDATE
    }
    return WatcherPerformanceFacts(
        scan_count=len(scans),
        succeeded_count=sum(1 for item in scans if (item.scan_status or "") == "succeeded"),
        failed_count=sum(1 for item in scans if (item.scan_status or "") == "failed"),
        blocked_count=sum(1 for item in scans if (item.scan_status or "") == "blocked"),
        skipped_count=sum(1 for item in scans if (item.scan_status or "") == "skipped"),
        replay_count=sum(1 for item in scans if item.replayed),
        stale_evidence_count=sum(
            1
            for reason in reasons
            if reason in _STALE_REASONS
            or canonical_diagnostic_reason(reason) is DiagnosticReason.STALE
        ),
        provider_outage_count=sum(
            1
            for reason in reasons
            if reason in _OUTAGE_REASONS
            or canonical_diagnostic_reason(reason) in PROVIDER_FAILURE_REASONS
        ),
        confirmed_setup_count=sum(
            1 for state in assessment_states if state is SetupAssessmentState.CONFIRMED_SETUP
        ),
        watch_count=sum(1 for state in assessment_states if state is SetupAssessmentState.WATCH),
        no_setup_count=sum(
            1 for state in assessment_states if state is SetupAssessmentState.NO_SETUP
        ),
        partial_match_count=sum(
            1 for state in assessment_states if state is SetupAssessmentState.PARTIAL_MATCH
        ),
        invalidated_count=sum(
            1 for state in assessment_states if state is SetupAssessmentState.INVALIDATED
        ),
        expired_count=sum(
            1 for state in assessment_states if state is SetupAssessmentState.EXPIRED
        ),
        candidates_published=len(candidates),
    )


def _conversion_facts(
    observations: tuple[PaperEvaluationObservation, ...],
    attributions: tuple[AttributionRecord, ...],
    eligibility: tuple[ActionEligibilityEvaluation, ...],
) -> SetupConversionFacts:
    scans = sum(1 for item in observations if item.stage is PaperEvaluationStage.WATCHER_SCAN)
    assessment_ids = {
        item.assessment_id for item in observations if item.assessment_id is not None
    } | {record.facts.assessment_id for record in attributions}
    confirmed_ids = {
        item.assessment_id
        for item in observations
        if item.assessment_id is not None
        and item.assessment_state is SetupAssessmentState.CONFIRMED_SETUP
    } | {
        record.facts.assessment_id
        for record in attributions
        if record.facts.setup_quality.confirmed
    }
    candidate_ids = {record.candidate_id for record in attributions} | {
        item.candidate_id for item in observations if item.candidate_id is not None
    }
    latest_eligibility = _latest_eligibility(eligibility)
    eligible_ids = {
        eval_.eligibility.candidate_id
        for eval_ in latest_eligibility.values()
        if eval_.eligibility.state is ActionEligibilityState.ELIGIBLE
    }
    blocked_ids = {
        eval_.eligibility.candidate_id
        for eval_ in latest_eligibility.values()
        if eval_.eligibility.state is ActionEligibilityState.BLOCKED
    }
    approved = sum(1 for record in attributions if record.facts.strategy_pattern.plan_approved)
    rejected = sum(1 for record in attributions if record.facts.strategy_pattern.rejected)
    skipped = sum(1 for record in attributions if record.facts.strategy_pattern.skipped)
    filled = sum(1 for record in attributions if record.facts.strategy_pattern.filled)
    closed = sum(1 for record in attributions if record.facts.strategy_pattern.closed)
    decisions = approved + rejected + skipped
    confirmed = len(confirmed_ids)
    candidates = len(candidate_ids)
    eligible = len(eligible_ids)
    return SetupConversionFacts(
        scans=scans,
        assessments=len(assessment_ids),
        confirmed_setups=confirmed,
        candidates=candidates,
        eligible=eligible,
        blocked=len(blocked_ids),
        paper_decisions=decisions,
        approved=approved,
        rejected=rejected,
        skipped=skipped,
        filled=filled,
        closed=closed,
        scan_to_confirmed_rate=_rate(confirmed, scans),
        confirmed_to_candidate_rate=_rate(candidates, confirmed),
        candidate_to_eligible_rate=_rate(eligible, candidates),
        eligible_to_approved_rate=_rate(approved, eligible),
        approved_to_fill_rate=_rate(filled, approved),
        fill_to_close_rate=_rate(closed, filled),
    )


def _false_signal_facts(
    observations: tuple[PaperEvaluationObservation, ...],
    attributions: tuple[AttributionRecord, ...],
) -> FalseSignalFacts:
    confirmed = sum(1 for record in attributions if record.facts.setup_quality.confirmed)
    if confirmed == 0:
        confirmed = sum(
            1
            for item in observations
            if item.assessment_state is SetupAssessmentState.CONFIRMED_SETUP
        )
    executed = [record for record in attributions if record.facts.outcome.eligible]
    wins = sum(1 for record in executed if record.facts.strategy_pattern.win)
    losses = sum(1 for record in executed if record.facts.strategy_pattern.loss)
    breakeven = sum(1 for record in executed if record.facts.strategy_pattern.breakeven)
    invalidated = sum(
        1
        for item in observations
        if item.stage is PaperEvaluationStage.SETUP_ASSESSMENT
        and item.assessment_state is SetupAssessmentState.INVALIDATED
        and item.setup_quality is not None
    )
    return FalseSignalFacts(
        confirmed_setups=confirmed,
        executed_outcomes=len(executed),
        confirmed_wins=wins,
        confirmed_losses=losses,
        confirmed_breakeven=breakeven,
        confirmed_invalidated_before_fill=invalidated,
        false_signal_rate=_rate(losses, len(executed)),
    )


def _strategy_facts(
    attributions: tuple[AttributionRecord, ...],
    journal: dict[UUID, JournalTradeMeasurement],
) -> tuple[StrategyPerformanceFacts, tuple[StrategyPerformanceFacts, ...]]:
    overall = _scorecard(attributions, journal, strategy_version_id=None, setup_definition_id=None)
    grouped: dict[tuple[UUID, UUID], list[AttributionRecord]] = defaultdict(list)
    for record in attributions:
        pattern = record.facts.strategy_pattern
        grouped[(pattern.strategy_version_id, pattern.setup_definition_id)].append(record)
    versions = tuple(
        _scorecard(
            tuple(group),
            journal,
            strategy_version_id=strategy_version_id,
            setup_definition_id=setup_definition_id,
        )
        for (strategy_version_id, setup_definition_id), group in sorted(
            grouped.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
        )
    )
    return overall, versions


def _scorecard(
    records: tuple[AttributionRecord, ...] | list[AttributionRecord],
    journal: dict[UUID, JournalTradeMeasurement],
    *,
    strategy_version_id: UUID | None,
    setup_definition_id: UUID | None,
) -> StrategyPerformanceFacts:
    executed = [record for record in records if record.facts.outcome.eligible]
    closed = [record for record in records if record.facts.strategy_pattern.closed]
    wins = sum(1 for record in executed if record.facts.strategy_pattern.win)
    losses = sum(1 for record in executed if record.facts.strategy_pattern.loss)
    breakeven = sum(1 for record in executed if record.facts.strategy_pattern.breakeven)
    pnls: list[Decimal] = []
    chronological: list[tuple[datetime, UUID, Decimal]] = []
    mfe: list[Decimal] = []
    mae: list[Decimal] = []
    capture: list[Decimal] = []
    for record in closed:
        trade_id = record.facts.journal_trade_id
        measurement = journal.get(trade_id) if trade_id is not None else None
        pnl = record.facts.outcome.net_pnl
        if pnl is None and measurement is not None:
            pnl = measurement.net_pnl
        if pnl is not None:
            amount = Decimal(str(pnl))
            pnls.append(amount)
            chronological.append((datetime.min.replace(tzinfo=UTC), record.candidate_id, amount))
        if measurement is not None:
            if measurement.mfe_amount is not None:
                mfe.append(Decimal(str(measurement.mfe_amount)))
            if measurement.mae_amount is not None:
                mae.append(Decimal(str(measurement.mae_amount)))
            if measurement.capture_pct is not None:
                capture.append(Decimal(str(measurement.capture_pct)))
            if measurement.closed_at is not None and pnl is not None:
                chronological[-1] = (
                    measurement.closed_at,
                    record.candidate_id,
                    Decimal(str(pnl)),
                )
    expectancy = _mean(pnls)
    net_total = sum(pnls, _ZERO).quantize(_MONEY_Q) if pnls else None
    return StrategyPerformanceFacts(
        strategy_version_id=strategy_version_id,
        setup_definition_id=setup_definition_id,
        sample_candidates=len(records),
        executed_outcome_count=len(executed),
        win_count=wins,
        loss_count=losses,
        breakeven_count=breakeven,
        win_rate=_rate(wins, wins + losses),
        expectancy=expectancy,
        net_pnl_total=net_total,
        max_drawdown=_max_drawdown(chronological),
        mfe_sample_count=len(mfe),
        average_mfe=_mean(mfe),
        mae_sample_count=len(mae),
        average_mae=_mean(mae),
        capture_sample_count=len(capture),
        average_capture_pct=_mean(capture),
        confidence=_confidence(len(executed)),
    )


def _max_drawdown(points: list[tuple[datetime, UUID, Decimal]]) -> Decimal | None:
    if not points:
        return None
    ordered = sorted(points, key=lambda item: (item[0], str(item[1])))
    equity = _ZERO
    peak = _ZERO
    max_dd = _ZERO
    for _when, _candidate, pnl in ordered:
        equity += pnl
        if equity > peak:
            peak = equity
        drawdown = peak - equity
        if drawdown > max_dd:
            max_dd = drawdown
    return max_dd.quantize(_MONEY_Q)


def _rule_facts(
    attributions: tuple[AttributionRecord, ...],
    journal: dict[UUID, JournalTradeMeasurement],
) -> RuleAdherenceFacts:
    assessed = [
        record
        for record in attributions
        if record.facts.risk_adherence.axis
        not in {RiskAdherence.NOT_APPLICABLE, RiskAdherence.NOT_YET_ASSESSED}
    ]
    adhered = sum(
        1 for record in assessed if record.facts.risk_adherence.axis is RiskAdherence.ADHERED
    )
    stop_v = sum(
        1 for record in assessed if record.facts.risk_adherence.axis is RiskAdherence.STOP_VIOLATION
    )
    size_v = sum(
        1
        for record in assessed
        if record.facts.risk_adherence.axis is RiskAdherence.SIZE_OR_RISK_VIOLATION
    )
    compliance = [
        item.rule_compliance for item in journal.values() if item.rule_compliance is not None
    ]
    return RuleAdherenceFacts(
        assessed_count=len(assessed),
        risk_adhered_count=adhered,
        stop_violation_count=stop_v,
        size_or_risk_violation_count=size_v,
        journal_compliant_count=sum(
            1 for item in compliance if item is TradeRuleCompliance.COMPLIANT
        ),
        journal_partial_count=sum(1 for item in compliance if item is TradeRuleCompliance.PARTIAL),
        journal_violated_count=sum(
            1 for item in compliance if item is TradeRuleCompliance.VIOLATED
        ),
        journal_unassessed_count=sum(
            1 for item in compliance if item is TradeRuleCompliance.UNASSESSED
        ),
        adherence_rate=_rate(adhered, len(assessed)),
    )


def _blocked_facts(
    eligibility: tuple[ActionEligibilityEvaluation, ...],
    observations: tuple[PaperEvaluationObservation, ...],
) -> BlockedTradeFacts:
    latest = _latest_eligibility(eligibility)
    counts: dict[str, int] = defaultdict(int)
    blocked = 0
    for evaluation in latest.values():
        if evaluation.eligibility.state is not ActionEligibilityState.BLOCKED:
            continue
        blocked += 1
        codes = evaluation.eligibility.reason_codes
        if not codes:
            counts["blocked"] += 1
            continue
        for code in codes:
            counts[code.value] += 1
    for item in observations:
        if item.stage is PaperEvaluationStage.ELIGIBILITY and (
            item.eligibility_state is ActionEligibilityState.BLOCKED
        ):
            if item.candidate_id is not None and item.candidate_id in latest:
                continue
            blocked += 1
            counts[item.reason_code or "blocked"] += 1
    ordered = tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
    return BlockedTradeFacts(blocked_count=blocked, by_reason=ordered)


def _human_facts(
    attributions: tuple[AttributionRecord, ...],
    watcher: WatcherPerformanceFacts,
) -> HumanVsSystemEvaluationFacts:
    approved_executed = [
        record
        for record in attributions
        if record.facts.strategy_pattern.plan_approved and record.facts.outcome.eligible
    ]
    return HumanVsSystemEvaluationFacts(
        human_reject_or_skip=sum(
            1
            for record in attributions
            if record.facts.strategy_pattern.rejected or record.facts.strategy_pattern.skipped
        ),
        human_approvals=sum(
            1 for record in attributions if record.facts.strategy_pattern.plan_approved
        ),
        paper_system_executions=sum(
            1 for record in attributions if record.facts.strategy_pattern.filled
        ),
        executed_outcomes=sum(1 for record in attributions if record.facts.outcome.eligible),
        setup_confirmed_count=sum(
            1 for record in attributions if record.facts.setup_quality.confirmed
        ),
        human_approved_executed_wins=sum(
            1 for record in approved_executed if record.facts.strategy_pattern.win
        ),
        human_approved_executed_losses=sum(
            1 for record in approved_executed if record.facts.strategy_pattern.loss
        ),
        system_scan_confirmed=watcher.confirmed_setup_count,
    )


def _missed_facts(
    attributions: tuple[AttributionRecord, ...],
    eligibility: tuple[ActionEligibilityEvaluation, ...],
) -> MissedOpportunityFacts:
    rejected = sum(
        1
        for record in attributions
        if record.facts.setup_quality.confirmed and record.facts.strategy_pattern.rejected
    )
    skipped = sum(
        1
        for record in attributions
        if record.facts.setup_quality.confirmed and record.facts.strategy_pattern.skipped
    )
    latest = _latest_eligibility(eligibility)
    approved = {
        record.candidate_id
        for record in attributions
        if record.facts.strategy_pattern.plan_approved
    }
    executed = {
        record.candidate_id
        for record in attributions
        if record.facts.strategy_pattern.filled or record.facts.outcome.eligible
    }
    eligible_not_approved = 0
    blocked_after = 0
    for evaluation in latest.values():
        candidate_id = evaluation.eligibility.candidate_id
        if evaluation.eligibility.state is ActionEligibilityState.ELIGIBLE:
            if candidate_id not in approved and candidate_id not in executed:
                eligible_not_approved += 1
        elif evaluation.eligibility.state is ActionEligibilityState.BLOCKED:
            blocked_after += 1
    return MissedOpportunityFacts(
        rejected_confirmed=rejected,
        skipped_confirmed=skipped,
        eligible_not_approved=eligible_not_approved,
        blocked_after_confirmation=blocked_after,
    )


def _data_quality_facts(observations: tuple[PaperEvaluationObservation, ...]) -> DataQualityFacts:
    scans = [item for item in observations if item.stage is PaperEvaluationStage.WATCHER_SCAN]
    source = scans or list(observations)
    fresh = sum(1 for item in source if item.data_quality is DataQualityClass.FRESH)
    stale = sum(1 for item in source if item.data_quality is DataQualityClass.STALE)
    degraded = sum(1 for item in source if item.data_quality is DataQualityClass.DEGRADED)
    unavailable = sum(1 for item in source if item.data_quality is DataQualityClass.UNAVAILABLE)
    replay = sum(1 for item in source if item.data_quality is DataQualityClass.REPLAY)
    unknown = sum(1 for item in source if item.data_quality is DataQualityClass.UNKNOWN)
    return DataQualityFacts(
        fresh_count=fresh,
        stale_count=stale,
        degraded_count=degraded,
        unavailable_count=unavailable,
        replay_count=replay,
        unknown_count=unknown,
        stale_or_unavailable_rate=_rate(stale + unavailable, len(source)),
    )


def _warnings(
    *,
    watcher: WatcherPerformanceFacts,
    conversion: SetupConversionFacts,
    overall: StrategyPerformanceFacts,
    quality: DataQualityFacts,
    observation_count: int,
    attribution_count: int,
) -> tuple[PaperEvaluationWarning, ...]:
    warnings: list[PaperEvaluationWarning] = [
        PaperEvaluationWarning(
            code="watcher_not_activated",
            message=(
                "Watcher orchestration remains disabled. Scan metrics are recorded only when "
                "a paper Watcher runtime actually ran; configuration flags are not RUNNING."
            ),
        ),
        PaperEvaluationWarning(
            code="refinement_not_activated",
            message="AI refinement suggestions cannot be activated by this measurement layer.",
        ),
    ]
    if observation_count == 0 and attribution_count == 0:
        warnings.append(
            PaperEvaluationWarning(
                code="no_evaluation_sample",
                message=(
                    "No Watcher observations or learning attribution records in this organization."
                ),
            )
        )
    if overall.confidence is SampleConfidence.INSUFFICIENT and overall.executed_outcome_count:
        warnings.append(
            PaperEvaluationWarning(
                code="insufficient_sample",
                message=(
                    "Executed-outcome sample is below 5; win rate and expectancy are provisional."
                ),
            )
        )
    if quality.stale_count or quality.unavailable_count:
        warnings.append(
            PaperEvaluationWarning(
                code="degraded_evidence",
                message=(
                    "Stale or unavailable evidence observations are included; they fail closed."
                ),
            )
        )
    if watcher.scan_count == 0:
        warnings.append(
            PaperEvaluationWarning(
                code="no_watcher_scans",
                message=(
                    "No Watcher scan observations. Conversion rates that start at scans are "
                    "undefined."
                ),
            )
        )
    _ = conversion
    return tuple(warnings)


def _latest_eligibility(
    eligibility: tuple[ActionEligibilityEvaluation, ...],
) -> dict[UUID, ActionEligibilityEvaluation]:
    latest: dict[UUID, ActionEligibilityEvaluation] = {}
    for evaluation in eligibility:
        candidate_id = evaluation.eligibility.candidate_id
        current = latest.get(candidate_id)
        if current is None or evaluation.evaluation_revision >= current.evaluation_revision:
            latest[candidate_id] = evaluation
    return latest
