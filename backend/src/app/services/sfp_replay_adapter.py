"""SFP research projection of the canonical detector, never a trade planner.

Historical rows do not establish when a candle was received. Explicit canonical
proofs are required. Missing/stale required evidence breaks the causal history;
later data cannot fill the gap retrospectively or erase earlier observations.
"""

from collections.abc import Iterator
from datetime import datetime
from typing import Any
from uuid import UUID

from app.market_contracts.derivatives import (
    DerivativeMetric,
    derivative_identity,
    hash_derivative_observation,
    require_derivative_observations,
)
from app.market_contracts.enums import Finality, FreshnessState
from app.market_contracts.errors import MarketContractError, StaleEvidenceError
from app.market_contracts.hashing import semantic_content_hash
from app.market_contracts.identity import EvidenceMarketIdentity
from app.market_contracts.ohlcv import OhlcvBar
from app.market_contracts.order_flow import hash_order_flow, order_flow_identity, require_order_flow
from app.schemas.common import BacktestSplitLabel, TradeDirection
from app.schemas.nested_continuation import BrainSetupState, EvidenceAvailability
from app.schemas.strategy_replay import (
    ReplayCandidate,
    ReplayCandleEvidence,
    ReplayEvidenceFrame,
    ReplayEvidenceGap,
    ReplayLevelConsideration,
    SfpReplayEvidence,
)
from app.strategy_brain.sfp.contracts import (
    SfpSpec,
    StructuralLevel,
    available_at,
    validate_binding,
)
from app.strategy_brain.sfp.detector import _known, detect_sfp
from app.strategy_brain.sfp.levels import derive_levels, level_evidence_fresh


def validate_replay_evidence(evidence: SfpReplayEvidence) -> None:
    """Validate content before freezing, including hashes that omit receipt clocks.

    The enclosing replay config hash additionally binds all clocks and freshness.
    """
    seen = set()
    for item in evidence.candles:
        validate_binding(item.bar, item.observation)
        key = (item.bar.instrument.instrument_id, item.bar.timeframe, item.bar.interval_start)
        if key in seen:
            raise ValueError("SFP replay requires one explicit revision per candle.")
        seen.add(key)
    for level in evidence.context_levels:
        # Revalidate even when called with model_copy bypassing normal validation.
        type(level).model_validate(level.model_dump())
    for flow in evidence.order_flow:
        if hash_order_flow(flow).content_hash != flow.content_hash:
            raise ValueError("SFP order-flow content hash mismatch.")
        require_hash = flow.freshness
        if (
            require_hash
            and semantic_content_hash(require_hash, extra_exclude=frozenset({"content_hash"}))
            != require_hash.content_hash
        ):
            raise ValueError("SFP order-flow freshness hash mismatch.")
    for derivative in evidence.derivatives:
        if hash_derivative_observation(derivative).content_hash != derivative.content_hash:
            raise ValueError("SFP derivative content hash mismatch.")
        if (
            derivative.freshness
            and semantic_content_hash(
                derivative.freshness, extra_exclude=frozenset({"content_hash"})
            )
            != derivative.freshness.content_hash
        ):
            raise ValueError("SFP derivative freshness hash mismatch.")


class SfpReplayAdapter:
    def __init__(self, evidence: SfpReplayEvidence | None) -> None:
        self.evidence = evidence or SfpReplayEvidence()
        validate_replay_evidence(self.evidence)
        self.levels: list[ReplayLevelConsideration] = []
        self.gaps: list[ReplayEvidenceGap] = []
        self.frames: list[ReplayEvidenceFrame] = []

    def events(
        self, bars: tuple[OhlcvBar, ...], spec: dict[str, Any], label: BacktestSplitLabel
    ) -> Iterator[tuple[int, ReplayCandidate]]:
        authored = SfpSpec.model_validate(spec)
        by_start = {item.bar.interval_start: item for item in self.evidence.candles}
        # Actual receipt times are preserved. Proofs received beyond the split
        # boundary remain unavailable; their clocks are never backdated.
        segment: list[tuple[int, ReplayCandleEvidence]] = []
        emitted: list[tuple[int, ReplayCandidate]] = []
        for index, bar in enumerate(bars):
            item = by_start.get(bar.interval_start)
            clock = (
                max(bar.interval_end, available_at(item.observation)) if item else bar.interval_end
            )
            if clock <= bars[-1].interval_end:
                identity = item.observation.identity if item else None
                optional = (
                    self._optional_evidence(identity, clock)
                    if identity
                    else {
                        key: {"availability": "MISSING", "reason": "canonical_identity_unavailable"}
                        for key in ("cvd", "order_flow", "open_interest", "funding")
                    }
                )
                self.frames.append(
                    ReplayEvidenceFrame(split_label=label, decision_at=clock, evidence=optional)
                )
            reason, availability = None, EvidenceAvailability.MISSING
            if item is None:
                reason = "canonical_candle_proof_missing"
            else:
                proof = item.bar
                if (
                    proof.instrument != bar.instrument
                    or proof.timeframe != bar.timeframe
                    or proof.interval_end != bar.interval_end
                    or (proof.open, proof.high, proof.low, proof.close, proof.base_volume)
                    != (bar.open, bar.high, bar.low, bar.close, bar.base_volume)
                ):
                    raise ValueError("SFP candle proof differs from the frozen dataset.")
                if proof.finality is not Finality.FINAL or not proof.provider_complete:
                    reason, availability = "candle_not_final", EvidenceAvailability.INCOMPLETE
                elif available_at(item.observation) > bars[-1].interval_end:
                    reason = "candle_proof_not_known_in_window"
                elif item.observation.freshness_state is not FreshnessState.FRESH:
                    reason, availability = (
                        "canonical_candle_proof_stale",
                        EvidenceAvailability.STALE,
                    )
            if reason:
                emitted.extend(self._segment(segment, authored, label))
                segment = []
                self.gaps.append(
                    ReplayEvidenceGap(
                        split_label=label,
                        decision_at=bar.interval_end,
                        availability=availability.value,
                        reasons=[reason],
                    )
                )
            else:
                assert item is not None
                segment.append((index, item))
        emitted.extend(self._segment(segment, authored, label))
        yield from sorted(
            emitted,
            key=lambda pair: (
                pair[1].detected_at,
                pair[1].sfp_detection.event_time if pair[1].sfp_detection else pair[1].detected_at,
                str(pair[1].sfp_detection.event_id) if pair[1].sfp_detection else "",
            ),
        )

    def _segment(
        self,
        segment: list[tuple[int, ReplayCandleEvidence]],
        spec: SfpSpec,
        label: BacktestSplitLabel,
    ) -> Iterator[tuple[int, ReplayCandidate]]:
        if not segment:
            return
        bars = tuple(item.bar for _, item in segment)
        observations = tuple(item.observation for _, item in segment)
        end = max(bars[-1].interval_end, *(available_at(o) for o in observations))
        # Context levels retain their closed proof and actual known_at. The real
        # detector filters them at the sweep open, including target-space facts.
        context = tuple(self.evidence.context_levels)
        scan = detect_sfp(bars, observations, spec, evaluated_at=end, context_levels=context)
        if scan.required_evidence is not EvidenceAvailability.AVAILABLE:
            self.gaps.append(
                ReplayEvidenceGap(
                    split_label=label,
                    decision_at=end,
                    availability=scan.required_evidence.value,
                    reasons=list(scan.reason_codes),
                )
            )
            return
        levels = (*derive_levels(bars, observations, spec.parameters, evaluated_at=end), *context)
        unique: dict[UUID, StructuralLevel] = {}
        for level in sorted(levels, key=lambda level: (level.known_at, str(level.level_id))):
            unique.setdefault(level.level_id, level)
        for bar, observation in zip(bars, observations, strict=True):
            for level in unique.values():
                if _known(level, bar, spec):
                    self.levels.append(
                        ReplayLevelConsideration(
                            split_label=label,
                            considered_at=available_at(observation),
                            level=level,
                            direction_matches=level.kind.support
                            == (spec.direction is TradeDirection.LONG),
                            significance_passes=level.significance.points
                            >= spec.parameters.minimum_level_significance,
                            evidence_fresh=level_evidence_fresh(level),
                        )
                    )
        indices = {bar.interval_end: index for index, item in segment for bar in (item.bar,)}
        for event in scan.events:
            # Lifecycle and quality come directly from the canonical detector.
            # Replay candidates stay non-executable. A confirmed detection may
            # carry a paper structure plan; this adapter does not copy it into
            # entry, stop, or targets and does not score a trade.
            if event.observed_at < event.event_time or event.observed_at > end:
                raise ValueError("SFP replay event must be a knowable closed-candle decision.")
            assert event.evidence.interval_end is not None
            quality = event.quality.model_dump(mode="json")
            optional = self._optional_evidence(event.evidence.identity, event.observed_at)
            availability = {key: value["availability"] for key, value in quality.items()}
            availability.update({key: value["availability"] for key, value in optional.items()})
            yield (
                indices[event.evidence.interval_end],
                ReplayCandidate(
                    setup_id=event.setup_id,
                    detected_at=event.observed_at,
                    decision_at=event.observed_at,
                    split_label=label,
                    direction=event.direction,
                    entry=None,
                    stop=None,
                    targets=[],
                    state=event.state.value,
                    reasons=list(event.reason_codes),
                    evidence=availability,
                    missing_evidence=sorted(
                        key
                        for key, value in availability.items()
                        if value != EvidenceAvailability.AVAILABLE
                    ),
                    stale_evidence=sorted(
                        key
                        for key, value in availability.items()
                        if value == EvidenceAvailability.STALE
                    ),
                    sfp_detection=event,
                    research_evidence=optional,
                    candidate_creation_eligibility="structural_confirmation_only"
                    if event.state is BrainSetupState.CONFIRMED
                    else "not_eligible",
                    risk_applicability="not_evaluated_no_authorized_execution_plan",
                ),
            )

    def _optional_evidence(
        self, identity: EvidenceMarketIdentity, clock: datetime
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        matching = [
            item
            for item in self.evidence.order_flow
            if item.identity == order_flow_identity(identity)
            and item.observed_at <= clock
            and item.window_end <= clock
        ]
        flow = max(
            matching,
            key=lambda item: (item.observed_at, item.window_end, item.content_hash),
            default=None,
        )
        reason: str | None
        availability, reason = (
            EvidenceAvailability.MISSING,
            "no_knowable_five_minute_print_evidence",
        )
        if flow:
            availability, reason = flow.availability, flow.reason
            if availability is EvidenceAvailability.AVAILABLE:
                try:
                    require_order_flow(flow, identity=identity, evaluated_at=clock)
                except StaleEvidenceError as exc:
                    availability, reason = EvidenceAvailability.STALE, str(exc)
                except MarketContractError as exc:
                    availability, reason = EvidenceAvailability.INCOMPLETE, str(exc)
        for name in ("cvd", "order_flow"):
            result[name] = {
                "availability": availability.value,
                "reason": reason,
                "content_hash": flow.content_hash if flow else None,
                "observation": flow.model_dump(mode="json")
                if flow and availability is EvidenceAvailability.AVAILABLE
                else None,
            }
        for metric in DerivativeMetric:
            matching_derivatives = [
                item
                for item in self.evidence.derivatives
                if item.metric is metric
                and item.identity == derivative_identity(identity, metric)
                and item.observed_at <= clock
                and (item.event_time is None or item.event_time <= clock)
            ]
            item = max(
                matching_derivatives,
                key=lambda item: (item.observed_at, item.content_hash),
                default=None,
            )
            availability, reason = EvidenceAvailability.MISSING, "no_knowable_native_evidence"
            if item:
                availability, reason = item.availability, item.reason
                if availability is EvidenceAvailability.AVAILABLE:
                    try:
                        require_derivative_observations(
                            [item], required_metrics=[metric], identity=identity, evaluated_at=clock
                        )
                    except StaleEvidenceError as exc:
                        availability, reason = EvidenceAvailability.STALE, str(exc)
                    except MarketContractError as exc:
                        availability, reason = EvidenceAvailability.INCOMPLETE, str(exc)
            result[metric.value] = {
                "availability": availability.value,
                "reason": reason,
                "content_hash": item.content_hash if item else None,
                "observation": item.model_dump(mode="json")
                if item and availability is EvidenceAvailability.AVAILABLE
                else None,
            }
        return result
