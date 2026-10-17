"""Read-only integration example. Not registered as an experiment/runtime worker."""

from __future__ import annotations

from app.evidence_pipeline.types import AssembledCanonicalEvidence
from app.market_contracts.context import MarketEvidenceContext
from app.market_contracts.derivatives import DerivativeMetric, require_derivative_observations
from app.market_contracts.errors import MarketContractError, WrongSourceError
from app.market_contracts.order_flow import require_order_flow
from app.signal_fusion.enums import EvidenceRole
from app.signal_fusion.evaluator import evaluate_setup
from app.signal_fusion.policy import FusionPolicy


def experiment_evidence_record(
    assembled: AssembledCanonicalEvidence,
    policy: FusionPolicy,
    *,
    optional_context: MarketEvidenceContext | None = None,
) -> dict[str, object]:
    """Persist canonical qualification and optional context as distinct inputs.

    Use an assembly produced for the experiment's configured policy/timeframe.
    Context alone cannot satisfy required roles or replace canonical hashes.
    No collection occurs here; reuse the collector's already acquired evidence.
    """
    cutoff = assembled.clocks.evidence_cutoff_at or assembled.evaluated_at
    required_metrics = tuple(
        DerivativeMetric(role.value)
        for role in policy.required_roles
        if role in {EvidenceRole.OPEN_INTEREST, EvidenceRole.FUNDING}
    )
    require_derivative_observations(
        assembled.bundle.market_intelligence,
        required_metrics=required_metrics,
        identity=assembled.identity,
        evaluated_at=assembled.evaluated_at,
        event_cutoff_at=cutoff,
    )
    if EvidenceRole.ORDER_BOOK in policy.required_roles:
        raise MarketContractError("required_order_book:historical_snapshot_coverage_unavailable")
    if any(
        role in policy.required_roles for role in (EvidenceRole.ORDER_FLOW_5M, EvidenceRole.CVD_5M)
    ):
        flow = assembled.bundle.order_flow
        require_order_flow(flow, identity=assembled.identity, evaluated_at=assembled.evaluated_at)
        if flow is None or flow.window_end != assembled.trigger_bar.interval_end:
            raise WrongSourceError("Required flow must align to the configured detection trigger.")
    context = optional_context or MarketEvidenceContext(
        evaluated_at=assembled.evaluated_at,
        evidence_cutoff_at=cutoff,
        anchor_venue=assembled.identity.venue.value,
        anchor_symbol=assembled.identity.instrument.provider_symbol,
        derivatives=assembled.bundle.market_intelligence,
        order_flow=assembled.bundle.order_flow,
    )
    if (
        context.anchor_venue != assembled.identity.venue.value
        or context.anchor_symbol != assembled.identity.instrument.provider_symbol
    ):
        raise WrongSourceError("Experiment context anchor must match its canonical assembly.")
    # Existing evaluator owns qualification and command/hash binding. The JSON
    # context retains per-component availability and explicit cross-venue labels.
    assessment = evaluate_setup(
        policy=policy,
        command=assembled.assessment_command,
        evidence=assembled.bundle,
        evaluated_at=assembled.evaluated_at,
    )
    return {
        "evidence_window_hash": assembled.evidence_window_hash,
        "evidence_cutoff_at": cutoff.isoformat(),
        "evaluated_at": assembled.evaluated_at.isoformat(),
        "detection_timeframe": assembled.identity.timeframe.value
        if assembled.identity.timeframe is not None
        else None,
        "trigger_interval_end": assembled.trigger_bar.interval_end.isoformat(),
        "assessment_state": assessment.state.value,
        "market_context": context.model_dump(mode="json"),
    }
