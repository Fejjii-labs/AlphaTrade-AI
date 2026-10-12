"""Deterministic Phase 6 fusion evaluator (setup truth only).

Consumes frozen ``FusionPolicy`` + ``AssessmentCommand`` + Phase 5 payloads and
emits an immutable ``SetupAssessment``. Optional ``evaluation_params`` are the
first-slice compatibility adapter bound from an approved compiled spec. This
module does not create candidates, evaluate eligibility, persist rows, call
exchanges, send Telegram, or invoke an LLM.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid5

from app.analysis.wilder_atr_v1 import (
    FinalOhlcvBar,
    OhlcvFinality,
    WilderAtrFeatureV1,
    WilderAtrStatus,
    compute_wilder_atr_v1,
)
from app.market_contracts.cvd import (
    FIRST_SLICE_CVD_LOOKBACK_BARS,
    first_slice_baseline_open,
    first_slice_cvd_window,
    signed_quote_delta_until,
    snapshot_terminal_event_time,
)
from app.market_contracts.derivatives import DerivativeMetric, require_derivative_observations
from app.market_contracts.enums import (
    Finality,
    FreshnessState,
    MarketType,
    ObservationType,
    VenueId,
)
from app.market_contracts.errors import (
    FallbackForbiddenError,
    FormingCandleError,
    GapDetectedError,
    IncompleteTradeWindowError,
    IncompleteWarmUpError,
    MarketContractError,
    SpotFallbackRejectedError,
    StaleEvidenceError,
    WrongInstrumentError,
    WrongMarketError,
    WrongSourceError,
)
from app.market_contracts.first_slice import (
    FIRST_SLICE_CONTEXT_TIMEFRAME,
    FIRST_SLICE_MIN_FINAL_4H,
    FIRST_SLICE_MIN_FINAL_15M,
    FIRST_SLICE_PATTERN_NAME,
    FIRST_SLICE_SYMBOL,
    FIRST_SLICE_TRIGGER_TIMEFRAME,
)
from app.market_contracts.flow import bar_signed_quote_flow
from app.market_contracts.freshness import (
    evaluate_freshness,
    first_slice_freshness_policy,
    live_confirmation_window_open,
)
from app.market_contracts.identity import (
    instrument_matches_requested_symbol,
    interval_timedelta,
)
from app.market_contracts.observation import (
    PublicMarketObservation,
    observation_from_derivative,
)
from app.market_contracts.ohlcv import OhlcvBar, observation_id_for, require_closed_series
from app.schemas.common import Timeframe, TradeDirection
from app.services.canonical_serialization import canonical_sha256
from app.signal_fusion.adapters import AssessmentCommand, evidence_window_from_assessment_command
from app.signal_fusion.assessment import SetupAssessment, build_setup_assessment
from app.signal_fusion.enums import AssessmentReasonCode, EvidenceRole, SetupAssessmentState
from app.signal_fusion.errors import (
    EvidenceIdentityMismatchError,
    EvidenceWindowContractError,
    FormingObservationNotExecutableError,
    SignalFusionContractError,
    TenantAssertionSelectionError,
)
from app.signal_fusion.first_slice_adapter import (
    FirstSliceEvaluationParams,
    resolve_evaluation_params,
)
from app.signal_fusion.first_slice_types import (
    FIRST_SLICE_RULE_IDS,
    FIRST_SLICE_RULE_WEIGHT,
    QUALITY_RULE_IDS,
    FirstSliceEvidenceBundle,
    ManualResistanceEvidence,
)
from app.signal_fusion.order_flow_inputs import ORDER_FLOW_ROLES, require_bound_order_flow
from app.signal_fusion.policy import DEFAULT_FUSION_POLICY_VERSION, FusionPolicy
from app.signal_fusion.swings import most_recent_confirmed_swing_high
from app.signal_fusion.types import HalfOpenInterval, RuleResult

DERIVATIVE_ROLES = {
    EvidenceRole.OPEN_INTEREST: DerivativeMetric.OPEN_INTEREST,
    EvidenceRole.FUNDING: DerivativeMetric.FUNDING,
}

_ASSESSMENT_NAMESPACE = UUID("9c4e1d70-2b8a-4f11-9d55-6a1f0c3e8b27")
_LIVE_STATES = frozenset(
    {
        SetupAssessmentState.WATCH,
        SetupAssessmentState.PARTIAL_MATCH,
        SetupAssessmentState.CONFIRMED_SETUP,
    }
)


def evaluate_setup(
    *,
    policy: FusionPolicy,
    command: AssessmentCommand,
    evidence: FirstSliceEvidenceBundle,
    evaluated_at: datetime,
    previous_assessment: SetupAssessment | None = None,
    account_context: object | None = None,
    evaluation_params: FirstSliceEvaluationParams | None = None,
    market_symbol: str | None = None,
    nested_spec: object | None = None,
    sfp_spec: object | None = None,
    trendpulse_spec: object | None = None,
) -> SetupAssessment:
    """Evaluate first-slice setup truth. Account/risk context is ignored.

    ``evaluation_params`` are the first-slice compatibility adapter. Product
    callers must pass params bound from an approved compiled spec; omitting
    them keeps adapter-level tests on the canonical first-slice constants.
    ``market_symbol`` is the market under evaluation. Omitting it keeps the
    canonical first-slice symbol so existing BTC evaluations stay unchanged.
    """
    del account_context
    if trendpulse_spec is not None:
        if nested_spec is not None or sfp_spec is not None:
            raise ValueError("A setup assessment must bind exactly one family")
        from app.strategy_brain.trendpulse_1r.canonical import evaluate_trendpulse_setup

        return evaluate_trendpulse_setup(
            policy=policy,
            command=command,
            evidence=evidence,
            evaluated_at=evaluated_at,
            spec=trendpulse_spec,
        )
    if sfp_spec is not None:
        if nested_spec is not None:
            raise ValueError("Exactly one strategy family may occupy evaluate_setup")
        from app.strategy_brain.sfp_runtime.assessment import evaluate_sfp_setup

        return evaluate_sfp_setup(
            policy=policy,
            command=command,
            evidence=evidence,
            evaluated_at=evaluated_at,
            spec=sfp_spec,
        )
    if nested_spec is not None:
        from app.strategy_brain.assessment import evaluate_nested_setup

        return evaluate_nested_setup(
            policy=policy,
            command=command,
            evidence=evidence,
            evaluated_at=evaluated_at,
            spec=nested_spec,
        )
    params = resolve_evaluation_params(evaluation_params)
    resolved_market = FIRST_SLICE_SYMBOL if market_symbol is None else market_symbol.strip().upper()
    evaluated = evaluated_at.astimezone(UTC)
    rules = {rule_id: _pending_rule(rule_id) for rule_id in FIRST_SLICE_RULE_IDS}
    window_hash: str | None = None
    assessment_window = _command_interval(command, evaluated)
    identity_reason: str | None = None

    compatible, policy_reason = _policy_compatible(policy, command)
    rules["policy_compatible"] = _rule(
        "policy_compatible",
        compatible,
        None if compatible else policy_reason,
    )

    try:
        window = evidence_window_from_assessment_command(command)
        window_hash = window.content_hash
        assessment_window = window.interval
    except (
        EvidenceIdentityMismatchError,
        FormingObservationNotExecutableError,
        EvidenceWindowContractError,
        TenantAssertionSelectionError,
        SignalFusionContractError,
        ValueError,
    ) as exc:
        identity_reason = _classify_contract_error(exc)
        window_hash = _fail_closed_hash(command, reason=identity_reason)

    _evaluate_market_identity(command, rules, identity_reason, market_symbol=resolved_market)
    series_15m, series_4h = _evaluate_series(command, evidence, evaluated, rules)
    atr_15m = _evaluate_atr(
        series_15m,
        timeframe=Timeframe.M15,
        rule_id="wilder_atr_15m",
        rules=rules,
        period=params.atr_period,
    )
    atr_4h = _evaluate_atr(
        series_4h,
        timeframe=Timeframe.H4,
        rule_id="wilder_atr_4h",
        rules=rules,
        period=params.atr_period,
    )
    trigger = series_15m[-1] if series_15m else None
    _evaluate_trigger_binding(command, trigger, rules)
    _evaluate_freshness_and_flow(
        command,
        evidence,
        series_15m,
        trigger,
        evaluated,
        rules,
        atr_15m=atr_15m,
        atr_4h=atr_4h,
        params=params,
    )

    try:
        require_derivative_observations(
            evidence.market_intelligence,
            required_metrics=tuple(
                DERIVATIVE_ROLES[r] for r in policy.required_roles if r in DERIVATIVE_ROLES
            ),
            identity=command.evidence_identity,
            evaluated_at=evaluated,
        )
        for role in policy.required_roles:
            if role not in DERIVATIVE_ROLES:
                continue
            item = next(
                item
                for item in evidence.market_intelligence
                if item.metric is DERIVATIVE_ROLES[role]
            )
            selected = [
                obs
                for obs, selected_role in zip(
                    command.public_observations, command.selected_roles, strict=True
                )
                if selected_role is role
            ]
            if selected != [observation_from_derivative(item)]:
                raise WrongSourceError("Required OI/funding payload is not bound to the command.")
    except (MarketContractError, ValueError) as exc:
        rules["complete_warmup"] = _rule(
            "complete_warmup",
            False,
            "required_source_stale"
            if isinstance(exc, StaleEvidenceError)
            else "missing_required_evidence",
        )
    if any(role in ORDER_FLOW_ROLES for role in policy.required_roles):
        try:
            require_bound_order_flow(
                evidence.order_flow,
                command=command,
                required_roles=policy.required_roles,
                evaluated_at=evaluated,
                trigger_end=trigger.interval_end if trigger else None,
            )
        except (MarketContractError, ValueError) as exc:
            rules["complete_warmup"] = _rule(
                "complete_warmup",
                False,
                "required_source_stale"
                if isinstance(exc, StaleEvidenceError)
                else "missing_required_evidence",
            )
    if EvidenceRole.ORDER_BOOK in policy.required_roles:
        # A current depth snapshot cannot prove liquidity at a historical trigger.
        rules["complete_warmup"] = _rule("complete_warmup", False, "missing_required_evidence")
    quality_ok = all(rules[rule_id].passed for rule_id in QUALITY_RULE_IDS)
    all_passed = all(rules[rule_id].passed for rule_id in FIRST_SLICE_RULE_IDS)
    state, reason_codes = _resolve_state(
        rules=rules,
        quality_ok=quality_ok,
        all_passed=all_passed,
        previous=previous_assessment,
    )
    assert window_hash is not None
    return _build_assessment(
        policy=policy,
        command=command,
        state=state,
        reason_codes=reason_codes,
        rules=rules,
        window_hash=window_hash,
        assessment_window=assessment_window,
        evaluated_at=evaluated,
        trigger=trigger,
        previous=previous_assessment,
        subsequent=evidence.subsequent_final_15m,
        params=params,
    )


def _pending_rule(rule_id: str) -> RuleResult:
    return _rule(rule_id, passed=False, reason_code="not_evaluated")


def _rule(
    rule_id: str,
    passed: bool,
    reason_code: str | None,
    *,
    evidence_role: EvidenceRole | None = None,
) -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        passed=passed,
        weight=FIRST_SLICE_RULE_WEIGHT,
        reason_code=None if passed else reason_code,
        evidence_role=evidence_role,
    )


def _policy_compatible(policy: FusionPolicy, command: AssessmentCommand) -> tuple[bool, str | None]:
    if policy.policy_version != DEFAULT_FUSION_POLICY_VERSION:
        return False, "incompatible_policy_version"
    if command.fusion_policy_version != policy.policy_version:
        return False, "incompatible_policy_version"
    if command.organization_id != policy.organization_id:
        return False, "incompatible_policy_version"
    if command.strategy_version_id != policy.strategy_version_id:
        return False, "incompatible_policy_version"
    if command.executable_setup != policy.executable_setup:
        return False, "incompatible_policy_version"
    if command.finality_policy_version != policy.finality_policy_version:
        return False, "incompatible_policy_version"
    if command.freshness_policy_version != policy.freshness_policy_version:
        return False, "incompatible_policy_version"
    if command.correction_selection_policy != policy.correction_selection_policy:
        return False, "incompatible_policy_version"
    if command.direction is not TradeDirection.SHORT:
        return False, "incompatible_policy_version"
    if set(command.mandatory_evidence_roles) < set(policy.required_roles):
        return False, "missing_required_evidence"
    if command.role_timeframes != policy.role_timeframes:
        return False, "incompatible_policy_version"
    if policy.thresholds.confirmation_score != Decimal("1.0"):
        return False, "incompatible_policy_version"
    return True, None


def _classify_contract_error(exc: Exception) -> str:
    if isinstance(exc, FormingObservationNotExecutableError):
        return "forming_evidence"
    if isinstance(exc, EvidenceIdentityMismatchError):
        return "wrong_venue_or_market"
    if isinstance(exc, TenantAssertionSelectionError):
        return "missing_required_evidence"
    if isinstance(exc, EvidenceWindowContractError):
        return "missing_required_evidence"
    return "market_disqualifier"


def _fail_closed_hash(command: AssessmentCommand, *, reason: str) -> str:
    return canonical_sha256(
        {
            "direction": command.direction,
            "fail_closed": True,
            "fusion_policy_version": command.fusion_policy_version,
            "organization_id": command.organization_id,
            "reason": reason,
            "setup_definition_id": command.executable_setup.setup_definition_id,
            "strategy_version_id": command.strategy_version_id,
        }
    )


def _command_interval(command: AssessmentCommand, evaluated: datetime) -> HalfOpenInterval:
    del evaluated
    return command.interval


def _evaluate_market_identity(
    command: AssessmentCommand,
    rules: dict[str, RuleResult],
    identity_reason: str | None,
    *,
    market_symbol: str,
) -> None:
    identity = command.evidence_identity
    venue_ok = identity.venue in {VenueId.BINANCE, VenueId.BYBIT}
    market_ok = identity.market_type is MarketType.PERPETUAL
    instrument_ok = instrument_matches_requested_symbol(identity, market_symbol)
    timeframe_ok = identity.timeframe is FIRST_SLICE_TRIGGER_TIMEFRAME
    fallback_used = identity.provenance.fallback_used
    if identity_reason == "wrong_venue_or_market" or not (venue_ok and market_ok and timeframe_ok):
        rules["market_identity"] = _rule(
            "market_identity",
            False,
            "wrong_venue_or_market",
            evidence_role=EvidenceRole.TRIGGER_OHLCV,
        )
    elif not instrument_ok:
        rules["market_identity"] = _rule(
            "market_identity",
            False,
            "wrong_instrument",
            evidence_role=EvidenceRole.TRIGGER_OHLCV,
        )
    else:
        rules["market_identity"] = _rule(
            "market_identity", True, None, evidence_role=EvidenceRole.TRIGGER_OHLCV
        )

    perpetual_ok = (
        market_ok and identity.instrument.market_type is MarketType.PERPETUAL and not fallback_used
    )
    if fallback_used:
        rules["perpetual_identity"] = _rule("perpetual_identity", False, "required_source_fallback")
    elif not perpetual_ok:
        rules["perpetual_identity"] = _rule("perpetual_identity", False, "wrong_perpetual_identity")
    else:
        rules["perpetual_identity"] = _rule("perpetual_identity", True, None)

    if identity_reason == "forming_evidence":
        rules["final_evidence"] = _rule(
            "final_evidence", False, "forming_evidence", evidence_role=EvidenceRole.TRIGGER_OHLCV
        )
    if identity_reason == "missing_required_evidence":
        rules["complete_warmup"] = _rule("complete_warmup", False, "missing_required_evidence")


def _ordered_bars(bars: Sequence[OhlcvBar], timeframe: Timeframe) -> list[OhlcvBar]:
    matching = [bar for bar in bars if bar.timeframe is timeframe]
    return sorted(matching, key=lambda bar: (bar.interval_start, bar.source_event_id, bar.revision))


def _context_bar_for_trigger(trigger: OhlcvBar, bars_4h: Sequence[OhlcvBar]) -> OhlcvBar | None:
    eligible = [
        bar
        for bar in _ordered_bars(bars_4h, Timeframe.H4)
        if bar.interval_end <= trigger.interval_end
    ]
    if not eligible:
        return None
    return eligible[-1]


def _evaluate_series(
    command: AssessmentCommand,
    evidence: FirstSliceEvidenceBundle,
    evaluated: datetime,
    rules: dict[str, RuleResult],
) -> tuple[list[OhlcvBar], list[OhlcvBar]]:
    bars_15m = _ordered_bars(evidence.bars_15m, Timeframe.M15)
    bars_4h = _ordered_bars(evidence.bars_4h, Timeframe.H4)
    forming = [
        bar.source_event_id
        for bar in (*bars_15m, *bars_4h)
        if bar.finality is not Finality.FINAL or not bar.provider_complete
    ]
    if forming:
        rules["final_evidence"] = _rule(
            "final_evidence",
            False,
            "forming_evidence",
            evidence_role=EvidenceRole.TRIGGER_OHLCV,
        )
        return bars_15m, bars_4h
    if rules["final_evidence"].reason_code != "forming_evidence":
        rules["final_evidence"] = _rule(
            "final_evidence", True, None, evidence_role=EvidenceRole.TRIGGER_OHLCV
        )

    identity_15m = command.evidence_identity
    identity_4h = command.evidence_identity.model_copy(
        update={"timeframe": FIRST_SLICE_CONTEXT_TIMEFRAME}
    )
    try:
        series_15m = require_closed_series(
            bars_15m,
            identity=identity_15m,
            timeframe=Timeframe.M15,
            evaluated_at=evaluated,
            min_bars=FIRST_SLICE_MIN_FINAL_15M,
        )
        series_4h = require_closed_series(
            bars_4h,
            identity=identity_4h,
            timeframe=Timeframe.H4,
            evaluated_at=evaluated,
            min_bars=FIRST_SLICE_MIN_FINAL_4H,
        )
    except FormingCandleError:
        rules["final_evidence"] = _rule(
            "final_evidence", False, "forming_evidence", evidence_role=EvidenceRole.TRIGGER_OHLCV
        )
        rules["complete_warmup"] = _rule("complete_warmup", False, "incomplete_warmup")
        return bars_15m, bars_4h
    except (WrongMarketError, WrongInstrumentError):
        rules["market_identity"] = _rule(
            "market_identity", False, "wrong_venue_or_market", evidence_role=EvidenceRole.TRIGGER
        )
        rules["complete_warmup"] = _rule("complete_warmup", False, "wrong_venue_or_market")
        return bars_15m, bars_4h
    except GapDetectedError:
        rules["no_unresolved_gap"] = _rule(
            "no_unresolved_gap", False, "required_source_gapped", evidence_role=EvidenceRole.TRIGGER
        )
        return bars_15m, bars_4h
    except MarketContractError:
        rules["complete_warmup"] = _rule("complete_warmup", False, "incomplete_warmup")
        return bars_15m, bars_4h

    if rules["complete_warmup"].reason_code == "not_evaluated":
        rules["complete_warmup"] = _rule("complete_warmup", True, None)
    return list(series_15m.bars), list(series_4h.bars)


def _atr_bars(bars: Sequence[OhlcvBar]) -> list[FinalOhlcvBar]:
    converted: list[FinalOhlcvBar] = []
    for bar in bars:
        converted.append(
            FinalOhlcvBar(
                revision_id=observation_id_for(
                    bar.source_event_id, finality=bar.finality, revision=bar.revision
                ),
                content_hash=bar.content_hash,
                venue=bar.instrument.venue.value,
                market=bar.instrument.market_type.value,
                instrument=bar.instrument.provider_symbol,
                timeframe=bar.timeframe.value,
                open_time=bar.interval_start,
                close_time=bar.interval_end,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.base_volume,
                finality=OhlcvFinality.FINAL,
            )
        )
    return converted


def _evaluate_atr(
    bars: Sequence[OhlcvBar],
    *,
    timeframe: Timeframe,
    rule_id: str,
    rules: dict[str, RuleResult],
    period: int,
) -> WilderAtrFeatureV1 | None:
    if not bars or any(bar.finality is not Finality.FINAL for bar in bars):
        rules[rule_id] = _rule(rule_id, False, "incomplete_warmup")
        return None
    feature = compute_wilder_atr_v1(
        _atr_bars(bars),
        period=period,
        timeframe=timeframe.value,
    )
    if feature.status is not WilderAtrStatus.VALUE or feature.value is None:
        reason = feature.missing_reason or "incomplete_warmup"
        rules[rule_id] = _rule(rule_id, False, reason)
        return None
    rules[rule_id] = _rule(rule_id, True, None)
    return feature


def _evaluate_trigger_binding(
    command: AssessmentCommand,
    trigger: OhlcvBar | None,
    rules: dict[str, RuleResult],
) -> None:
    if trigger is None:
        rules["complete_warmup"] = _rule("complete_warmup", False, "missing_required_evidence")
        return
    if (
        trigger.source_event_id != command.trigger.natural_event_id
        or trigger.revision != command.trigger.revision
    ):
        rules["final_evidence"] = _rule(
            "final_evidence",
            False,
            "missing_required_evidence",
            evidence_role=EvidenceRole.TRIGGER,
        )


def _contiguous_subsequent(trigger: OhlcvBar, bars: Sequence[OhlcvBar]) -> list[OhlcvBar]:
    ordered = _ordered_bars(bars, Timeframe.M15)
    expected = trigger.interval_end
    out: list[OhlcvBar] = []
    for bar in ordered:
        if bar.finality is not Finality.FINAL or not bar.provider_complete:
            break
        if bar.interval_start != expected:
            break
        out.append(bar)
        expected = bar.interval_end
    return out


def _select_resistance(
    evidence: FirstSliceEvidenceBundle,
    command: AssessmentCommand,
    *,
    swing_price: Decimal,
    trigger_start: datetime,
) -> tuple[ManualResistanceEvidence | None, str | None]:
    ref = command.manual_level_revision
    if ref is None:
        return None, "missing_manual_resistance"
    identity = command.evidence_identity
    eligible: list[ManualResistanceEvidence] = []
    matching_ref: ManualResistanceEvidence | None = None
    for item in evidence.resistances:
        if item.ref == ref:
            matching_ref = item
        if not item.valid:
            continue
        if item.timeframe is not Timeframe.H4:
            continue
        if item.effective_at >= trigger_start:
            continue
        if item.venue is not identity.venue:
            continue
        if item.market_type is not identity.market_type:
            continue
        if item.instrument_id != identity.instrument.instrument_id:
            continue
        eligible.append(item)
    if matching_ref is None:
        return None, "invalid_manual_level_revision"
    if not matching_ref.valid:
        return None, "invalid_manual_level_revision"
    if matching_ref.effective_at >= trigger_start:
        return None, "invalid_manual_level_revision"
    if matching_ref.timeframe is not Timeframe.H4:
        return None, "invalid_manual_level_revision"
    if not eligible:
        return None, "missing_manual_resistance"
    eligible.sort(
        key=lambda item: (
            abs(item.price - swing_price),
            str(item.ref.level_id),
            item.ref.revision_number,
        )
    )
    nearest = eligible[0]
    if nearest.ref != ref:
        return None, "invalid_manual_level_revision"
    return nearest, None


def _observation_for_role(
    command: AssessmentCommand, role: EvidenceRole
) -> PublicMarketObservation | None:
    for observation, selected in zip(
        command.public_observations, command.selected_roles, strict=True
    ):
        if selected is role:
            return observation
    return None


def _payload_matches(
    observation: PublicMarketObservation | None,
    *,
    expected_type: ObservationType,
    expected_payload_hash: str,
) -> bool:
    return (
        observation is not None
        and observation.observation_type is expected_type
        and observation.payload_content_hash == expected_payload_hash
    )


def _bind_consumed_observations(
    command: AssessmentCommand,
    *,
    trigger: OhlcvBar,
    context: OhlcvBar | None,
    cvd_hash: str,
    flow_hash: str,
    coverage_hash: str,
    rules: dict[str, RuleResult],
) -> bool:
    """Require command observations to be the payloads evaluation consumes."""

    trigger_obs = _observation_for_role(command, EvidenceRole.TRIGGER_OHLCV)
    context_obs = _observation_for_role(command, EvidenceRole.CONTEXT_OHLCV)
    cvd_obs = _observation_for_role(command, EvidenceRole.CVD_WINDOW)
    flow_obs = _observation_for_role(command, EvidenceRole.SIGNED_FLOW)
    coverage_obs = _observation_for_role(command, EvidenceRole.TRADE_EVENT)
    bindings: list[tuple[PublicMarketObservation | None, ObservationType, str, EvidenceRole]] = [
        (
            trigger_obs,
            ObservationType.OHLCV,
            trigger.content_hash,
            EvidenceRole.TRIGGER_OHLCV,
        ),
        (cvd_obs, ObservationType.CVD, cvd_hash, EvidenceRole.CVD_WINDOW),
        (flow_obs, ObservationType.VOLUME, flow_hash, EvidenceRole.SIGNED_FLOW),
        (coverage_obs, ObservationType.TRADE, coverage_hash, EvidenceRole.TRADE_EVENT),
    ]
    if context is not None:
        bindings.insert(
            1,
            (
                context_obs,
                ObservationType.OHLCV,
                context.content_hash,
                EvidenceRole.CONTEXT_OHLCV,
            ),
        )
    for observation, expected_type, expected_hash, role in bindings:
        if _payload_matches(
            observation,
            expected_type=expected_type,
            expected_payload_hash=expected_hash,
        ):
            continue
        rules["market_identity"] = _rule(
            "market_identity",
            False,
            "wrong_venue_or_market",
            evidence_role=role,
        )
        return False
    return True


def _evaluate_freshness_and_flow(
    command: AssessmentCommand,
    evidence: FirstSliceEvidenceBundle,
    series_15m: Sequence[OhlcvBar],
    trigger: OhlcvBar | None,
    evaluated: datetime,
    rules: dict[str, RuleResult],
    *,
    atr_15m: WilderAtrFeatureV1 | None,
    atr_4h: WilderAtrFeatureV1 | None,
    params: FirstSliceEvaluationParams,
) -> None:
    stale_obs = any(
        observation.freshness_state
        in {FreshnessState.STALE, FreshnessState.UNKNOWN, FreshnessState.GAP}
        for observation in command.public_observations
    )
    snapshot = evidence.snapshot
    if snapshot is None:
        rules["freshness"] = _rule(
            "freshness", False, "missing_required_evidence", evidence_role=EvidenceRole.CVD_WINDOW
        )
        rules["no_unresolved_gap"] = _rule(
            "no_unresolved_gap",
            False,
            "missing_required_evidence",
            evidence_role=EvidenceRole.CVD_WINDOW,
        )
        if rules["complete_warmup"].passed:
            rules["complete_warmup"] = _rule("complete_warmup", False, "missing_required_evidence")
        _fail_pattern_rules(rules)
        return

    if stale_obs:
        rules["freshness"] = _rule(
            "freshness", False, "required_source_stale", evidence_role=EvidenceRole.TRADE_EVENT
        )

    try:
        if trigger is None:
            raise IncompleteWarmUpError("Trigger bar missing.")
        identity_15m = command.evidence_identity
        closed = require_closed_series(
            list(series_15m),
            identity=identity_15m,
            timeframe=Timeframe.M15,
            evaluated_at=evaluated,
            min_bars=FIRST_SLICE_MIN_FINAL_15M,
        )
        live_window = live_confirmation_window_open(
            closed_interval_end=trigger.interval_end,
            evaluated_at=evaluated,
        )
        cvd = first_slice_cvd_window(
            identity=identity_15m,
            series_15m=closed,
            snapshot=snapshot,
            created_at=evaluated,
            require_live_freshness=live_window,
        )
        flow = bar_signed_quote_flow(
            identity=identity_15m,
            bar=trigger,
            snapshot=snapshot,
            evaluated_at=evaluated,
            require_live_freshness=live_window,
        )
        if live_window:
            evaluate_freshness(
                source_time=snapshot_terminal_event_time(snapshot),
                evaluated_at=evaluated,
                policy=first_slice_freshness_policy(),
                require_fresh=True,
            )
    except StaleEvidenceError:
        rules["freshness"] = _rule(
            "freshness", False, "required_source_stale", evidence_role=EvidenceRole.TRADE_EVENT
        )
        _fail_pattern_rules(rules, keep_existing=True)
        return
    except GapDetectedError:
        rules["no_unresolved_gap"] = _rule(
            "no_unresolved_gap",
            False,
            "required_source_gapped",
            evidence_role=EvidenceRole.CVD_WINDOW,
        )
        _fail_pattern_rules(rules, keep_existing=True)
        return
    except IncompleteWarmUpError:
        rules["complete_warmup"] = _rule("complete_warmup", False, "incomplete_warmup")
        _fail_pattern_rules(rules, keep_existing=True)
        return
    except IncompleteTradeWindowError:
        rules["complete_warmup"] = _rule("complete_warmup", False, "missing_required_evidence")
        _fail_pattern_rules(rules, keep_existing=True)
        return
    except (
        WrongMarketError,
        WrongInstrumentError,
        SpotFallbackRejectedError,
        FallbackForbiddenError,
    ):
        rules["market_identity"] = _rule(
            "market_identity", False, "wrong_venue_or_market", evidence_role=EvidenceRole.CVD_WINDOW
        )
        _fail_pattern_rules(rules, keep_existing=True)
        return
    except MarketContractError:
        rules["complete_warmup"] = _rule("complete_warmup", False, "incomplete_warmup")
        _fail_pattern_rules(rules, keep_existing=True)
        return

    if not _bind_consumed_observations(
        command,
        trigger=trigger,
        context=_context_bar_for_trigger(trigger, evidence.bars_4h),
        cvd_hash=cvd.content_hash,
        flow_hash=flow.content_hash,
        coverage_hash=snapshot.coverage.content_hash,
        rules=rules,
    ):
        _fail_pattern_rules(rules, keep_existing=True)
        return

    if rules["freshness"].reason_code == "not_evaluated":
        rules["freshness"] = _rule("freshness", True, None, evidence_role=EvidenceRole.TRADE_EVENT)
    if rules["no_unresolved_gap"].reason_code == "not_evaluated":
        rules["no_unresolved_gap"] = _rule(
            "no_unresolved_gap", True, None, evidence_role=EvidenceRole.CVD_WINDOW
        )

    if atr_15m is None or atr_4h is None or atr_15m.value is None or atr_4h.value is None:
        _fail_pattern_rules(rules, keep_existing=True)
        return

    window_start = first_slice_baseline_open(trigger, FIRST_SLICE_CVD_LOOKBACK_BARS)
    swing = most_recent_confirmed_swing_high(
        series_15m, window_start=window_start, before_bar=trigger
    )
    if swing is None:
        rules["confirmed_swing_high"] = _rule(
            "confirmed_swing_high",
            False,
            "missing_confirmed_swing",
            evidence_role=EvidenceRole.SWING,
        )
        _fail_remaining_pattern(rules)
        _evaluate_expiry_invalidation(trigger, evidence, atr_15m.value, rules, params=params)
        return
    rules["confirmed_swing_high"] = _rule(
        "confirmed_swing_high", True, None, evidence_role=EvidenceRole.SWING
    )

    resistance, resistance_reason = _select_resistance(
        evidence, command, swing_price=swing.price, trigger_start=trigger.interval_start
    )
    if resistance is None:
        rules["manual_4h_resistance"] = _rule(
            "manual_4h_resistance",
            False,
            resistance_reason or "missing_manual_resistance",
            evidence_role=EvidenceRole.STRUCTURE,
        )
        _fail_remaining_pattern(rules)
        _evaluate_expiry_invalidation(trigger, evidence, atr_15m.value, rules, params=params)
        return
    rules["manual_4h_resistance"] = _rule(
        "manual_4h_resistance", True, None, evidence_role=EvidenceRole.STRUCTURE
    )

    htf_ok = abs(swing.price - resistance.price) <= params.swing_atr_distance * atr_4h.value
    rules["htf_resistance_context"] = _rule(
        "htf_resistance_context",
        htf_ok,
        None if htf_ok else "resistance_tolerance_failure",
        evidence_role=EvidenceRole.STRUCTURE,
    )

    sweep_ok = trigger.high >= swing.price + params.sweep_atr * atr_15m.value
    close_ok = trigger.close < swing.price and trigger.close < trigger.open
    ltf_ok = sweep_ok and close_ok
    ltf_reason = None if ltf_ok else ("sweep_failed" if not sweep_ok else "close_failed")
    rules["ltf_liquidity_sweep"] = _rule(
        "ltf_liquidity_sweep",
        ltf_ok,
        ltf_reason,
        evidence_role=EvidenceRole.TRIGGER,
    )

    prior = list(series_15m[-(params.volume_lookback + 1) : -1])
    if len(prior) != params.volume_lookback:
        rules["volume_spike"] = _rule(
            "volume_spike", False, "missing_required_evidence", evidence_role=EvidenceRole.VOLUME
        )
    else:
        mean_volume = sum((bar.base_volume for bar in prior), start=Decimal("0")) / Decimal(
            params.volume_lookback
        )
        if mean_volume == 0:
            rules["volume_spike"] = _rule(
                "volume_spike", False, "volume_failure", evidence_role=EvidenceRole.VOLUME
            )
        else:
            volume_ok = trigger.base_volume / mean_volume >= params.volume_ratio
            rules["volume_spike"] = _rule(
                "volume_spike",
                volume_ok,
                None if volume_ok else "volume_failure",
                evidence_role=EvidenceRole.VOLUME,
            )

    cvd_t = signed_quote_delta_until(
        snapshot, window_start=window_start, bar_end=trigger.interval_end, baseline=Decimal("0")
    )
    cvd_s = signed_quote_delta_until(
        snapshot,
        window_start=window_start,
        bar_end=swing.bar.interval_end,
        baseline=Decimal("0"),
    )
    cvd_ok = trigger.high > swing.price and cvd_t < cvd_s
    rules["bearish_cvd_divergence"] = _rule(
        "bearish_cvd_divergence",
        cvd_ok,
        None if cvd_ok else "cvd_divergence_failure",
        evidence_role=EvidenceRole.CVD_WINDOW,
    )

    imbalance_ok = flow.signed_flow_ratio <= params.sell_imbalance
    rules["aggressive_sell_imbalance"] = _rule(
        "aggressive_sell_imbalance",
        imbalance_ok,
        None if imbalance_ok else "aggressive_sell_imbalance_failure",
        evidence_role=EvidenceRole.SIGNED_FLOW,
    )
    _evaluate_expiry_invalidation(trigger, evidence, atr_15m.value, rules, params=params)


def _fail_pattern_rules(rules: dict[str, RuleResult], *, keep_existing: bool = False) -> None:
    pattern_ids = (
        "confirmed_swing_high",
        "manual_4h_resistance",
        "htf_resistance_context",
        "ltf_liquidity_sweep",
        "volume_spike",
        "bearish_cvd_divergence",
        "aggressive_sell_imbalance",
        "not_invalidated",
        "not_expired",
    )
    for rule_id in pattern_ids:
        current = rules[rule_id]
        if keep_existing and current.reason_code != "not_evaluated":
            continue
        rules[rule_id] = _rule(rule_id, False, "fail_closed_prerequisite")


def _fail_remaining_pattern(rules: dict[str, RuleResult]) -> None:
    for rule_id in (
        "htf_resistance_context",
        "ltf_liquidity_sweep",
        "volume_spike",
        "bearish_cvd_divergence",
        "aggressive_sell_imbalance",
    ):
        if rules[rule_id].reason_code == "not_evaluated":
            rules[rule_id] = _rule(rule_id, False, "fail_closed_prerequisite")


def first_slice_short_invalidation_price(
    *,
    trigger: OhlcvBar,
    bars_15m: Sequence[OhlcvBar],
    tick_size: Decimal,
    params: FirstSliceEvaluationParams,
) -> Decimal | None:
    """Stop used by the first-slice short invalidation rule. None when ATR is missing."""

    rules: dict[str, RuleResult] = {}
    feature = _evaluate_atr(
        bars_15m,
        timeframe=Timeframe.M15,
        rule_id="wilder_atr_15m",
        rules=rules,
        period=params.atr_period,
    )
    if feature is None or feature.value is None:
        return None
    buffer = max(params.invalidation_atr * feature.value, params.invalidation_ticks * tick_size)
    return trigger.high + buffer


def _evaluate_expiry_invalidation(
    trigger: OhlcvBar,
    evidence: FirstSliceEvidenceBundle,
    atr_15m: Decimal,
    rules: dict[str, RuleResult],
    *,
    params: FirstSliceEvaluationParams,
) -> None:
    subsequent = _contiguous_subsequent(trigger, evidence.subsequent_final_15m)
    buffer = max(
        params.invalidation_atr * atr_15m,
        params.invalidation_ticks * evidence.tick_size,
    )
    invalidation_price = trigger.high + buffer
    invalidated = any(bar.high > invalidation_price for bar in subsequent)
    expired = len(subsequent) >= params.expiry_bars
    rules["not_invalidated"] = _rule(
        "not_invalidated",
        not invalidated,
        None if not invalidated else "pattern_invalidated",
    )
    rules["not_expired"] = _rule(
        "not_expired",
        not expired,
        None if not expired else "validity_interval_elapsed",
    )


def _resolve_state(
    *,
    rules: dict[str, RuleResult],
    quality_ok: bool,
    all_passed: bool,
    previous: SetupAssessment | None,
) -> tuple[SetupAssessmentState, tuple[AssessmentReasonCode, ...]]:
    previous_live = previous is not None and previous.state in _LIVE_STATES
    invalidated = (
        not rules["not_invalidated"].passed
        and rules["not_invalidated"].reason_code == "pattern_invalidated"
    )
    expired = (
        not rules["not_expired"].passed
        and rules["not_expired"].reason_code == "validity_interval_elapsed"
    )
    if not quality_ok:
        codes = _quality_reason_codes(rules)
        if previous_live:
            return SetupAssessmentState.INVALIDATED, codes
        return SetupAssessmentState.NO_SETUP, codes
    if invalidated:
        return SetupAssessmentState.INVALIDATED, (AssessmentReasonCode.PATTERN_INVALIDATED,)
    if expired:
        return SetupAssessmentState.EXPIRED, (AssessmentReasonCode.VALIDITY_INTERVAL_ELAPSED,)
    if all_passed:
        return SetupAssessmentState.CONFIRMED_SETUP, (
            AssessmentReasonCode.ALL_MANDATORY_EVIDENCE_CONFIRMED,
        )
    if rules["htf_resistance_context"].passed:
        return SetupAssessmentState.PARTIAL_MATCH, (AssessmentReasonCode.SEQUENCE_STEP_PASSED,)
    return SetupAssessmentState.WATCH, (AssessmentReasonCode.PRECONDITIONS_PASSED,)


def _quality_reason_codes(rules: dict[str, RuleResult]) -> tuple[AssessmentReasonCode, ...]:
    mapping: list[AssessmentReasonCode] = []
    by_rule = {
        "policy_compatible": AssessmentReasonCode.POLICY_REPLACED,
        "market_identity": AssessmentReasonCode.WRONG_VENUE_OR_MARKET,
        "perpetual_identity": AssessmentReasonCode.WRONG_VENUE_OR_MARKET,
        "final_evidence": AssessmentReasonCode.MARKET_DISQUALIFIER,
        "freshness": AssessmentReasonCode.REQUIRED_SOURCE_STALE,
        "no_unresolved_gap": AssessmentReasonCode.REQUIRED_SOURCE_GAPPED,
        "complete_warmup": AssessmentReasonCode.MARKET_DISQUALIFIER,
        "wilder_atr_15m": AssessmentReasonCode.MARKET_DISQUALIFIER,
        "wilder_atr_4h": AssessmentReasonCode.MARKET_DISQUALIFIER,
    }
    for rule_id, code in by_rule.items():
        result = rules[rule_id]
        if not result.passed and code not in mapping:
            if result.reason_code == "required_source_fallback":
                mapping.append(AssessmentReasonCode.REQUIRED_SOURCE_FALLBACK)
            else:
                mapping.append(code)
    if not mapping:
        mapping.append(AssessmentReasonCode.MARKET_DISQUALIFIER)
    return tuple(mapping)


def _explanation(state: SetupAssessmentState, rules: dict[str, RuleResult]) -> str:
    failed = [rule_id for rule_id in FIRST_SLICE_RULE_IDS if not rules[rule_id].passed]
    if state is SetupAssessmentState.CONFIRMED_SETUP:
        text = (
            f"{FIRST_SLICE_PATTERN_NAME}: CONFIRMED_SETUP; "
            "all mandatory first-slice predicates passed."
        )
    elif failed:
        text = f"{FIRST_SLICE_PATTERN_NAME}: {state.value}; failed {', '.join(failed)}."
    else:
        text = f"{FIRST_SLICE_PATTERN_NAME}: {state.value}."
    return text[:500]


def _valid_until(
    evaluated: datetime, trigger: OhlcvBar | None, *, params: FirstSliceEvaluationParams
) -> datetime:
    expiry_delta = interval_timedelta(Timeframe.M15) * params.expiry_bars
    natural = (trigger.interval_end if trigger is not None else evaluated) + expiry_delta
    minimum = evaluated + timedelta(seconds=1)
    return max(natural, minimum)


def _build_assessment(
    *,
    policy: FusionPolicy,
    command: AssessmentCommand,
    state: SetupAssessmentState,
    reason_codes: tuple[AssessmentReasonCode, ...],
    rules: dict[str, RuleResult],
    window_hash: str,
    assessment_window: HalfOpenInterval,
    evaluated_at: datetime,
    trigger: OhlcvBar | None,
    previous: SetupAssessment | None,
    subsequent: tuple[OhlcvBar, ...],
    params: FirstSliceEvaluationParams,
) -> SetupAssessment:
    ordered_rules = tuple(rules[rule_id] for rule_id in FIRST_SLICE_RULE_IDS)
    subsequent_digest = ",".join(
        bar.content_hash for bar in _ordered_bars(subsequent, Timeframe.M15)
    )
    material = f"{window_hash}|{state.value}|{evaluated_at.isoformat()}|{subsequent_digest}"
    if previous is not None:
        material = f"{material}|{previous.assessment_id}"
    assessment_id = uuid5(_ASSESSMENT_NAMESPACE, f"assessment|{material}")
    correlation_id = uuid5(_ASSESSMENT_NAMESPACE, f"correlation|{window_hash}")
    observation_ids = tuple(sorted(item.observation_id for item in command.public_observations))
    return build_setup_assessment(
        assessment_id=assessment_id,
        organization_id=command.organization_id,
        strategy_version_id=command.strategy_version_id,
        executable_setup=command.executable_setup,
        fusion_policy_version=policy.policy_version,
        observation_ids=observation_ids,
        assessment_window=assessment_window,
        state=state,
        previous_assessment_id=None if previous is None else previous.assessment_id,
        previous_state=None if previous is None else previous.state,
        rule_results=ordered_rules,
        threshold=policy.thresholds.confirmation_score,
        reason_codes=reason_codes,
        explanation=_explanation(state, rules),
        evidence_window_hash=window_hash,
        assessed_at=evaluated_at,
        valid_until=_valid_until(evaluated_at, trigger, params=params),
        correlation_id=correlation_id,
        presentation_evidence=(),
    )


__all__ = ["evaluate_setup", "first_slice_short_invalidation_price"]
