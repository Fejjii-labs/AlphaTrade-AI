"""OHLCV-only family assembly using Watcher's existing contracted source.

No CVD, flow, funding or open interest acquisition is required for detection.
Quote acquisition is optional here; existing paper execution still requires
fresh contracted live evidence and all existing account/risk gates.
"""

from contextlib import suppress
from datetime import timedelta
from uuid import uuid5

import structlog
from sqlalchemy.orm import Session

from app.candidate_alerts.nested import NestedAlertSummary, required_evidence_fresh
from app.evidence_pipeline.canonical import semantic_source_from_identity
from app.evidence_pipeline.current_price import quote_current_price
from app.evidence_pipeline.types import (
    AssembledCanonicalEvidence,
    CompletenessReport,
    EvidenceClockReport,
)
from app.market_contracts.catalog import instrument_for_source
from app.market_contracts.enums import DataCompleteness, FreshnessState
from app.market_contracts.errors import DuplicateDataError, MarketContractError, StaleEvidenceError
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import interval_timedelta
from app.market_contracts.observation import observation_from_ohlcv
from app.services.canonical_serialization import canonical_sha256
from app.signal_fusion.adapters import AssessmentCommand, evidence_window_from_assessment_command
from app.signal_fusion.enums import EvidenceAdapterKind, EvidenceRole
from app.signal_fusion.first_slice_types import FIRST_SLICE_TICK_SIZE, FirstSliceEvidenceBundle
from app.signal_fusion.types import HalfOpenInterval, TriggerIdentity
from app.strategy_brain.detector import NAMESPACE, NestedDetection, detect_nested
from app.strategy_brain.records import attach_paper_result, record_detections, scoped_setup_id

logger = structlog.get_logger(__name__)


def assemble_nested(
    assembler: object,
    *,
    executable: object,
    organization_id: object,
    session: Session | None,
) -> AssembledCanonicalEvidence:
    return assemble_ohlcv_family(
        assembler,
        executable=executable,
        organization_id=organization_id,
        session=session,
    )


def assemble_ohlcv_family(
    assembler: object,
    *,
    executable: object,
    organization_id: object,
    session: Session | None,
) -> AssembledCanonicalEvidence:
    from app.strategy_brain.sfp.contracts import SfpSpec
    from app.strategy_brain.sfp.detector import detect_sfp
    from app.strategy_brain.sfp_runtime.assembly import sfp_observations
    from app.strategy_brain.sfp_runtime.assessment import select_detection
    from app.strategy_brain.sfp_runtime.records import record_sfp_scan

    spec = executable.authored_spec
    sfp = isinstance(spec, SfpSpec)
    source, catalog = assembler._source, assembler._catalog
    replay = assembler._replay
    clock = assembler._default_clock
    now = clock()
    instrument = instrument_for_source(source, catalog, spec.symbol)
    identity = first_slice_identity(
        timeframe=spec.trigger_timeframe, replay=replay, is_live=not replay, instrument=instrument
    )
    assembler._assert_live_contract(identity)
    series = source.fetch_closed_ohlcv(
        identity=identity,
        instrument=instrument,
        timeframe=spec.trigger_timeframe,
        min_final_bars=max(
            256,
            max(spec.parameters.level_lookback, spec.parameters.quality_lookback)
            + 2 * spec.parameters.pivot_width,
        )
        if sfp
        else 256,
        evaluated_at=now,
    )
    bars = tuple(series.bars)
    # Receipt time belongs after acquisition/confirmation, never before network IO.
    now = clock()
    if not bars:
        raise MarketContractError("Required OHLCV is missing.")
    if not sfp and not timedelta(0) <= now - bars[-1].interval_end < interval_timedelta(
        spec.trigger_timeframe
    ):
        raise StaleEvidenceError("Required Nested OHLCV is stale.")
    if sfp:
        observations = sfp_observations(session, bars=bars, identity=identity, now=now)
        if session is not None:
            from app.persistence.public_market_observations import observed_ohlcv_history

            horizon = (
                spec.parameters.level_lookback
                + spec.parameters.expiry_bars
                + spec.parameters.quality_lookback
                + 2 * spec.parameters.pivot_width
            )
            retained = observed_ohlcv_history(
                session,
                identity=identity,
                since=now - interval_timedelta(spec.trigger_timeframe) * horizon,
                evaluated_at=now,
                limit=horizon * 2,
            )
            # Original prices and original receipt clocks survive a rolling provider window.
            # The fetched revision explicitly replaces the corresponding historical revision.
            pairs = {b.source_event_id: (b, o) for b, o in retained}
            for b, o in zip(bars, observations, strict=True):
                previous = pairs.get(b.source_event_id)
                if previous is not None and previous[0].revision > b.revision:
                    logger.warning(
                        "canonical_ohlcv_revision_regressed",
                        observation_id=str(previous[1].observation_id),
                        retained_revision=previous[0].revision,
                        incoming_revision=b.revision,
                    )
                    raise DuplicateDataError(
                        "Provider returned an older canonical candle revision."
                    )
                pairs[b.source_event_id] = (b, o)
            ordered = sorted(pairs.values(), key=lambda pair: pair[0].interval_start)
            bars = tuple(b for b, _ in ordered)
            observations = tuple(o for _, o in ordered)
        scan = detect_sfp(
            bars, observations, spec, evaluated_at=now, tick_size=FIRST_SLICE_TICK_SIZE
        )
        latest = select_detection(scan, bars)
        events = scan.events
    else:
        events = detect_nested(bars, spec, evaluated_at=now)
        latest = events[-1] if events else None
    trigger = bars[-1]
    policy = executable.fusion_policy
    if sfp:
        trigger_obs = observations[-1]
        public_observations = (trigger_obs, *observations)
        selected_roles = (
            EvidenceRole.TRIGGER_OHLCV,
            *(EvidenceRole.STRUCTURE for _ in observations),
        )
    else:
        trigger_obs = observation_from_ohlcv(
            trigger,
            identity=identity,
            observed_at=now,
            receive_time=now,
            freshness_state=FreshnessState.FRESH,
        )
        # A typed OHLCV-series observation binds every causal bar to canonical evidence.
        history_hash = canonical_sha256([b.content_hash for b in bars])
        history_obs = with_content_hash(
            trigger_obs.model_copy(
                update={
                    "observation_id": uuid5(NAMESPACE, history_hash),
                    "source_event_id": f"history:{history_hash}",
                    "interval_start": bars[0].interval_start,
                    "payload_content_hash": history_hash,
                    "content_hash": "0" * 64,
                }
            )
        )
        public_observations = (trigger_obs, history_obs)
        selected_roles = (EvidenceRole.TRIGGER_OHLCV, EvidenceRole.STRUCTURE)
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
        public_observations=public_observations,
        selected_roles=selected_roles,
        source_set=(semantic_source_from_identity(identity),),
        adapter_kind=EvidenceAdapterKind.WATCHER,
        role_timeframes=policy.role_timeframes,
    )
    window = evidence_window_from_assessment_command(command)
    if session is not None:
        if sfp:
            record_sfp_scan(
                session,
                executable=executable,
                scan=scan,
                evidence_hash=window.content_hash,
                evaluated_at=now,
            )
        else:
            record_detections(
                session,
                organization_id=organization_id,
                strategy_id=executable.strategy_id,
                version_id=executable.strategy_version_id,
                spec=spec,
                bars=bars,
                events=events,
                evidence_hash=window.content_hash,
                evaluated_at=now,
            )
    quote = None
    with suppress(MarketContractError):
        quote = quote_current_price(
            source,
            identity=identity,
            instrument=instrument,
            evaluated_at=now,
            connection_id=uuid5(NAMESPACE, window.content_hash),
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
        series_4h=None,
        cvd=None,
        signed_flow=None,
        current_price=quote,
        completeness=CompletenessReport(
            ohlcv_15m=DataCompleteness.COMPLETE,
            ohlcv_4h=DataCompleteness.UNKNOWN,
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
            setup_expired=latest is not None and latest.state.value in {"EXPIRED", "INVALIDATED"},
            setup_lifetime_remaining_bars=spec.parameters.expiry_bars
            if sfp
            else spec.parameters.confirmation_window,
            setup_trigger_bar_hash=trigger.content_hash,
        ),
        bundle=FirstSliceEvidenceBundle(bars_15m=bars, tick_size=FIRST_SLICE_TICK_SIZE),
        assessment_command=command,
        evidence_window=window,
        evidence_window_hash=window.content_hash,
        connection_id=uuid5(NAMESPACE, window.content_hash),
        evaluation_mark=quote.price if quote else trigger.close,
    )


def record_paper_link(
    session: Session | None,
    *,
    target: object,
    report: object,
    evidence: object,
    now: object,
) -> NestedAlertSummary | None:
    if report.discussion is None:
        return
    last = getattr(evidence, "last_assembly", lambda: None)()
    if last is None:
        return
    assembled, policy = last
    from app.schemas.nested_continuation import NestedContinuationSpec
    from app.signal_fusion.enums import EvidenceRole
    from app.strategy_brain.sfp.contracts import SfpDetection, SfpSpec
    from app.strategy_brain.sfp.detector import detect_sfp
    from app.strategy_brain.sfp_runtime.assessment import select_detection

    if not isinstance(policy.authored_spec, (NestedContinuationSpec, SfpSpec)):
        return
    events: tuple[SfpDetection, ...] | tuple[NestedDetection, ...]
    nested_latest: NestedDetection | None = None
    if isinstance(policy.authored_spec, SfpSpec):
        observations = tuple(
            o
            for o, role in zip(
                assembled.assessment_command.public_observations,
                assembled.assessment_command.selected_roles,
                strict=True,
            )
            if role is EvidenceRole.STRUCTURE
        )
        scan = detect_sfp(
            assembled.bundle.bars_15m,
            observations,
            policy.authored_spec,
            evaluated_at=assembled.evaluated_at,
            tick_size=assembled.bundle.tick_size,
        )
        detection = select_detection(scan, assembled.bundle.bars_15m)
        events = (detection,) if detection is not None else ()
    else:
        nested_events = detect_nested(
            assembled.bundle.bars_15m, policy.authored_spec, evaluated_at=assembled.evaluated_at
        )
        events = nested_events
        nested_latest = nested_events[-1] if nested_events else None
    if not events:
        return
    identity = scoped_setup_id(
        target.organization_id, target.strategy_version_id, events[-1].setup_id
    )
    if session is not None:
        attach_paper_result(
            session,
            organization_id=target.organization_id,
            setup_id=identity,
            candidate_id=report.discussion.candidate.candidate_id,
            assessment_id=report.discussion.assessment.assessment_id,
            proof=report,
            now=now,
        )

    # SFP persists its canonical paper link, but has no accepted Telegram producer.
    # The Nested-only alert adapter cannot interpret SFP detection fields.
    if isinstance(policy.authored_spec, SfpSpec):
        return None
    if nested_latest is None:
        return None

    latest = nested_latest
    if (
        latest.state.value != "CONFIRMED"
        or latest.confirmed_index != len(assembled.bundle.bars_15m) - 1
        or not required_evidence_fresh(report.discussion.assessment, assembled.evidence_window, now)
    ):
        return None
    return NestedAlertSummary(
        organization_id=target.organization_id,
        strategy_version_id=target.strategy_version_id,
        setup_id=identity,
        symbol=policy.authored_spec.symbol,
        stage=latest.stage,
        evidence_at=latest.detected_at,
        decision=report.paper_loop_reason or "not_evaluated",
        risk_state=report.eligibility_state or "not_evaluated",
        reasons=tuple(dict.fromkeys([*latest.reason_codes, report.paper_loop_reason])),
        entry=latest.entry,
        stop=latest.stop,
        target=latest.targets[0] if latest.targets else None,
    )
