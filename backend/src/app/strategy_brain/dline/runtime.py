"""D-line live runtime: Watcher assembly, evaluate_setup adapter and durable setups.

The trigger-timeframe and next-timeframe-up closed candles are both bound into
the canonical evidence window (the history observation hashes both series), so
an assessment or a plan cannot read a different bigger trend than the scan did.
Setup rows reuse the shared Brain lifecycle with family ``d_line``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid5

from sqlalchemy.orm import Session

from app.evidence_pipeline.types import AssembledCanonicalEvidence
from app.market_contracts.enums import Finality, FreshnessState, MarketType
from app.market_contracts.identity import interval_timedelta
from app.market_contracts.ohlcv import OhlcvBar
from app.schemas.nested_continuation import BrainSetupState, EvidenceAvailability
from app.services.canonical_serialization import canonical_sha256
from app.signal_fusion.adapters import AssessmentCommand, evidence_window_from_assessment_command
from app.signal_fusion.assessment import SetupAssessment, build_setup_assessment
from app.signal_fusion.enums import AssessmentReasonCode, SetupAssessmentState
from app.signal_fusion.first_slice_types import FirstSliceEvidenceBundle
from app.signal_fusion.policy import FusionPolicy
from app.signal_fusion.types import RuleResult
from app.strategy_brain.detector import NestedDetection
from app.strategy_brain.dline.live import (
    DLINE_LIVE_NAMESPACE,
    DlineLiveScan,
    DlineLiveSignal,
    DlineLiveSpec,
    detect_dline_live,
)

DLINE_FAMILY = "d_line"


def dline_history_hash(bars: tuple[OhlcvBar, ...], htf_bars: tuple[OhlcvBar, ...]) -> str:
    return canonical_sha256(
        {"trigger": [b.content_hash for b in bars], "htf": [b.content_hash for b in htf_bars]}
    )


def scan_and_current(
    bars: tuple[OhlcvBar, ...],
    htf_bars: tuple[OhlcvBar, ...],
    spec: DlineLiveSpec,
    *,
    evaluated_at: datetime,
    tick_size: Decimal,
) -> tuple[DlineLiveScan, DlineLiveSignal | None]:
    """The scan and the signal triggered by the newest closed candle, if any."""

    scan = detect_dline_live(bars, htf_bars, spec, evaluated_at=evaluated_at, tick_size=tick_size)
    current = next(
        (
            s
            for s in reversed(scan.signals)
            if bars
            and s.trigger_index == len(bars) - 1
            and s.trigger_event_id == bars[-1].source_event_id
            and s.detected_at <= evaluated_at < s.expires_at
        ),
        None,
    )
    return scan, current


def to_detection(
    signal: DlineLiveSignal, *, state: BrainSetupState = BrainSetupState.CONFIRMED
) -> NestedDetection:
    return NestedDetection(
        setup_id=signal.setup_id,
        sequence_id=signal.line_id,
        state=state,
        stage=f"dline_attempt_{signal.attempt}",
        completed_continuations=signal.attempt - 1,
        direction=signal.direction,
        event_index=signal.trigger_index,
        anchor_index=signal.anchor_indices[0],
        impulse_index=signal.anchor_indices[1],
        pullback_index=None,
        confirmed_index=signal.trigger_index,
        detected_at=signal.detected_at if state is BrainSetupState.CONFIRMED else signal.expires_at,
        expires_at=signal.expires_at,
        entry=signal.entry,
        stop=signal.stop,
        targets=[t.price for t in signal.targets],
        quality_components={
            "touches": str(signal.touch_count),
            "angle_degrees": str(signal.angle_degrees),
            "confidence": str(signal.confidence),
            "htf_timeframe": signal.htf_timeframe.value,
            "htf_trend": signal.htf_trend,
            "attempt": str(signal.attempt),
            "line_id": str(signal.line_id),
            "breakout_level": str(signal.breakout_level),
            "structure_extreme": str(signal.structure_extreme),
            "structure_stop_anchor": str(signal.structure_stop_anchor),
            "entry_mode": signal.entry_mode,
            "breakout_fraction": str(signal.breakout_fraction),
            "retest_limit": str(signal.retest_limit),
            "retest_expires_at": signal.retest_expires_at.isoformat(),
            "reward_risk_tp2": str(signal.reward_risk),
            "target_fractions": ",".join(f"{t.label}:{t.fraction}" for t in signal.targets),
            "runner_fraction": str(signal.runner_fraction),
        },
        reason_codes=list(signal.reason_codes),
        evidence={
            "ohlcv": EvidenceAvailability.AVAILABLE,
            "htf_ohlcv": EvidenceAvailability.AVAILABLE,
        },
    )


def detections(scan: DlineLiveScan, *, evaluated_at: datetime) -> tuple[NestedDetection, ...]:
    events: list[NestedDetection] = []
    for signal in scan.signals:
        events.append(to_detection(signal))
        if signal.expires_at <= evaluated_at:
            events.append(to_detection(signal, state=BrainSetupState.EXPIRED))
    return tuple(events)


def assemble_dline(
    assembler: object,
    *,
    executable: object,
    organization_id: object,
    session: Session | None,
) -> AssembledCanonicalEvidence:
    from contextlib import suppress

    from app.evidence_pipeline.canonical import semantic_source_from_identity
    from app.evidence_pipeline.current_price import quote_current_price
    from app.evidence_pipeline.instrument_tick import resolve_bundle_tick_size
    from app.evidence_pipeline.types import CompletenessReport, EvidenceClockReport
    from app.market_contracts.catalog import instrument_for_source
    from app.market_contracts.enums import DataCompleteness
    from app.market_contracts.errors import MarketContractError, StaleEvidenceError
    from app.market_contracts.first_slice import first_slice_identity
    from app.market_contracts.hashing import with_content_hash
    from app.market_contracts.observation import observation_from_ohlcv
    from app.market_contracts.provider_contracts import default_contract_book
    from app.signal_fusion.adapters import AssessmentCommand
    from app.signal_fusion.enums import EvidenceAdapterKind, EvidenceRole
    from app.signal_fusion.types import HalfOpenInterval, TriggerIdentity
    from app.strategy_brain.records import record_detections

    spec = executable.authored_spec
    if not isinstance(spec, DlineLiveSpec):
        raise ValueError("D-line assembly requires a registered D-line version")
    if executable.organization_id != organization_id:
        raise ValueError("D-line assembly tenant does not match immutable policy")
    p = spec.parameters
    source, catalog = assembler._source, assembler._catalog
    replay = assembler._replay
    clock = assembler._default_clock
    now = clock()
    instrument = instrument_for_source(source, catalog, spec.symbol)
    contract_book = getattr(assembler, "_contract_book", None)
    if not replay and (contract_book is None or not contract_book.contracts):
        contract_book = default_contract_book()
    tick_size = resolve_bundle_tick_size(
        symbol=spec.symbol, venue=instrument.venue, book=contract_book, replay=replay
    )
    identity = first_slice_identity(
        timeframe=spec.trigger_timeframe, replay=replay, is_live=not replay, instrument=instrument
    )
    htf_identity = first_slice_identity(
        timeframe=spec.htf_timeframe, replay=replay, is_live=not replay, instrument=instrument
    )
    assembler._assert_live_contract(identity)
    assembler._assert_live_contract(htf_identity)
    series = source.fetch_closed_ohlcv(
        identity=identity,
        instrument=instrument,
        timeframe=spec.trigger_timeframe,
        min_final_bars=max(256, p.lookback + p.atr_period + 2 * p.pivot_width + 2),
        evaluated_at=now,
    )
    htf_series = source.fetch_closed_ohlcv(
        identity=htf_identity,
        instrument=instrument,
        timeframe=spec.htf_timeframe,
        min_final_bars=max(2 * p.htf_pivot_width + 4, p.htf_lookback),
        evaluated_at=now,
    )
    bars, htf = tuple(series.bars), tuple(htf_series.bars)
    now = clock()  # receipt time after network IO
    if not bars or not htf:
        raise MarketContractError("Required D-line OHLCV is missing.")
    if not timedelta(0) <= now - bars[-1].interval_end < interval_timedelta(spec.trigger_timeframe):
        raise StaleEvidenceError("Required D-line OHLCV is stale.")
    if not timedelta(0) <= now - htf[-1].interval_end < interval_timedelta(spec.htf_timeframe):
        raise StaleEvidenceError("Required D-line higher-timeframe OHLCV is stale.")
    scan, current = scan_and_current(bars, htf, spec, evaluated_at=now, tick_size=tick_size)
    trigger = bars[-1]
    policy = executable.fusion_policy
    trigger_obs = observation_from_ohlcv(
        trigger,
        identity=identity,
        observed_at=now,
        receive_time=now,
        freshness_state=FreshnessState.FRESH,
    )
    history_hash = dline_history_hash(bars, htf)
    history_obs = with_content_hash(
        trigger_obs.model_copy(
            update={
                "observation_id": uuid5(DLINE_LIVE_NAMESPACE, history_hash),
                "source_event_id": f"history:{history_hash}",
                "interval_start": bars[0].interval_start,
                "payload_content_hash": history_hash,
                "content_hash": "0" * 64,
            }
        )
    )
    command = AssessmentCommand(
        organization_id=organization_id,
        strategy_version_id=policy.strategy_version_id,
        executable_setup=policy.executable_setup,
        fusion_policy_version=policy.policy_version,
        finality_policy_version=policy.finality_policy_version,
        freshness_policy_version=policy.freshness_policy_version,
        direction=spec.direction,
        evidence_identity=identity,
        interval=HalfOpenInterval(start=bars[0].interval_start, end=trigger.interval_end),
        trigger=TriggerIdentity(
            natural_event_id=trigger.source_event_id, revision=trigger.revision
        ),
        mandatory_evidence_roles=policy.required_roles,
        public_observations=(trigger_obs, history_obs),
        selected_roles=(EvidenceRole.TRIGGER_OHLCV, EvidenceRole.STRUCTURE),
        source_set=(semantic_source_from_identity(identity),),
        adapter_kind=EvidenceAdapterKind.WATCHER,
        role_timeframes=policy.role_timeframes,
    )
    window = evidence_window_from_assessment_command(command)
    if session is not None:
        record_detections(
            session,
            organization_id=organization_id,
            strategy_id=executable.strategy_id,
            version_id=executable.strategy_version_id,
            spec=spec,
            bars=bars,
            events=detections(scan, evaluated_at=now),
            evidence_hash=window.content_hash,
            evaluated_at=now,
            family=DLINE_FAMILY,
        )
    quote = None
    with suppress(MarketContractError):
        quote = quote_current_price(
            source,
            identity=identity,
            instrument=instrument,
            evaluated_at=now,
            connection_id=uuid5(DLINE_LIVE_NAMESPACE, window.content_hash),
            replay=replay,
        )
    return AssembledCanonicalEvidence(
        organization_id=organization_id,
        replay=replay,
        evaluated_at=now,
        identity=identity,
        trigger_bar=trigger,
        context_bar=None,
        series_15m=series,
        series_4h=htf_series,
        cvd=None,
        signed_flow=None,
        current_price=quote,
        completeness=CompletenessReport(
            ohlcv_15m=DataCompleteness.COMPLETE,
            ohlcv_4h=DataCompleteness.COMPLETE,
            cvd=DataCompleteness.UNKNOWN,
            signed_flow=DataCompleteness.UNKNOWN,
        ),
        freshness_state=FreshnessState.FRESH,
        clocks=EvidenceClockReport(
            quote_source_time=quote.source_time if quote else None,
            quote_fresh=quote is not None and quote.usable_as_current_market_price,
            live_confirmation_window_open=True,
            trigger_finality=trigger.finality,
            trigger_interval_end=trigger.interval_end,
            historical_closed_evidence=False,
            closed_evidence_valid=True,
            subsequent_final_15m_count=0,
            setup_expired=current is None,
            setup_lifetime_remaining_bars=p.signal_expiry_bars,
            setup_trigger_bar_hash=trigger.content_hash,
        ),
        bundle=FirstSliceEvidenceBundle(bars_15m=bars, bars_4h=htf, tick_size=tick_size),
        assessment_command=command,
        evidence_window=window,
        evidence_window_hash=window.content_hash,
        connection_id=uuid5(DLINE_LIVE_NAMESPACE, window.content_hash),
        evaluation_mark=quote.price if quote else trigger.close,
    )


def evaluate_dline_setup(
    *,
    policy: FusionPolicy,
    command: AssessmentCommand,
    evidence: FirstSliceEvidenceBundle,
    evaluated_at: datetime,
    spec: object,
) -> SetupAssessment:
    spec = DlineLiveSpec.model_validate(spec)
    window = evidence_window_from_assessment_command(command)
    bars, htf = evidence.bars_15m, evidence.bars_4h
    _, current = scan_and_current(
        bars, htf, spec, evaluated_at=evaluated_at, tick_size=evidence.tick_size
    )
    delta = interval_timedelta(spec.trigger_timeframe)
    identity_ok = (
        command.evidence_identity.instrument.provider_symbol == spec.symbol
        and command.evidence_identity.market_type is MarketType.PERPETUAL
        and command.direction == spec.direction
        and command.evidence_identity.timeframe == spec.trigger_timeframe
    )
    fresh = bool(bars) and timedelta(0) <= evaluated_at - bars[-1].interval_end < delta
    final = (
        bool(bars)
        and bool(htf)
        and all(
            b.finality is Finality.FINAL and b.provider_complete and b.interval_end <= evaluated_at
            for b in (*bars, *htf)
        )
        and all(b.timeframe == spec.htf_timeframe for b in htf)
    )
    bound = bool(bars) and command.trigger.natural_event_id == bars[-1].source_event_id
    observations_ok = bool(bars) and len(command.public_observations) == 2
    if observations_ok:
        trigger_obs, history_obs = command.public_observations
        observations_ok = (
            all(
                o.freshness_state is FreshnessState.FRESH
                and o.finality is Finality.FINAL
                and o.identity == command.evidence_identity
                for o in command.public_observations
            )
            and trigger_obs.payload_content_hash == bars[-1].content_hash
            and trigger_obs.source_event_id == bars[-1].source_event_id
            and history_obs.payload_content_hash == dline_history_hash(bars, htf)
        )
    policy_ok = (
        policy.required_roles == command.mandatory_evidence_roles
        and policy.strategy_version_id == command.strategy_version_id
        and policy.executable_setup == command.executable_setup
        and policy.organization_id == command.organization_id
    )
    checks = (
        ("market_identity", identity_ok),
        ("fresh_closed_ohlcv", fresh),
        ("final_causal_series", final),
        ("trigger_binding", bound),
        ("required_observation_binding", observations_ok),
        ("compiled_policy_binding", policy_ok),
        ("dline_body_close_breakout", current is not None),
    )
    quality = identity_ok and fresh and final and bound and observations_ok and policy_ok
    state = SetupAssessmentState.NO_SETUP
    if quality and current is not None:
        state = SetupAssessmentState.CONFIRMED_SETUP
    if state is SetupAssessmentState.CONFIRMED_SETUP:
        reason = AssessmentReasonCode.ALL_MANDATORY_EVIDENCE_CONFIRMED
    elif not quality:
        reason = (
            AssessmentReasonCode.REQUIRED_SOURCE_STALE
            if not fresh
            else AssessmentReasonCode.MARKET_DISQUALIFIER
        )
    else:
        reason = AssessmentReasonCode.PRECONDITIONS_PASSED
    return build_setup_assessment(
        assessment_id=uuid5(DLINE_LIVE_NAMESPACE, f"assessment:{window.content_hash}:{state}"),
        organization_id=command.organization_id,
        strategy_version_id=command.strategy_version_id,
        executable_setup=command.executable_setup,
        fusion_policy_version=command.fusion_policy_version,
        observation_ids=tuple(o.observation_id for o in command.public_observations),
        assessment_window=window.interval,
        state=state,
        rule_results=tuple(
            RuleResult(
                rule_id=name,
                passed=passed,
                reason_code=None if passed else name,
                weight=Decimal(1),
            )
            for name, passed in checks
        ),
        threshold=Decimal(1),
        reason_codes=(reason,),
        explanation=(
            "D-line: " + (", ".join(current.reason_codes) if current else "no body-close breakout")
        )[:500],
        evidence_window_hash=window.content_hash,
        assessed_at=evaluated_at,
        valid_until=current.expires_at if current else evaluated_at + timedelta(seconds=1),
        correlation_id=command.correlation_id
        or uuid5(DLINE_LIVE_NAMESPACE, f"correlation:{window.content_hash}"),
    )


def current_signal_from_assembly(
    assembled: AssembledCanonicalEvidence, spec: DlineLiveSpec
) -> DlineLiveSignal | None:
    """Re-run the pure scan on the bound evidence (plan terms, paper links)."""

    _, current = scan_and_current(
        assembled.bundle.bars_15m,
        assembled.bundle.bars_4h,
        spec,
        evaluated_at=assembled.evaluated_at,
        tick_size=assembled.bundle.tick_size,
    )
    return current
