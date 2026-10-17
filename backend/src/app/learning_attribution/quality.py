"""Pure quality axes: setup vs execution vs risk adherence vs trader behavior vs outcome.

Outcome PnL never rewrites SetupAssessment state. REJECT/SKIP never become
executed trade outcomes. Risk adherence is independent of fill slippage and PnL.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from app.db.models import JournalTrade
from app.learning_attribution.contracts import (
    EARLY_EXIT_CAPTURE_PCT,
    ENTRY_MATCH_BPS,
    SIZE_MATCH_BPS,
    DecisionActor,
    ExecutionQuality,
    ExecutionQualityFacts,
    LearningVenueMode,
    PlannedSetupQuality,
    PlannedSetupQualityFacts,
    RiskAdherence,
    RiskAdherenceFacts,
    StrategyPatternStatFacts,
    TradeOutcomeFacts,
    TraderBehavior,
    TraderBehaviorFacts,
)
from app.schemas.common import JournalLifecycleEventType, JournalTradeStatus, TradeResult
from app.schemas.trade_plan import AuthorizationChannel
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.enums import SetupAssessmentState

_EXECUTING = frozenset(
    {
        JournalLifecycleEventType.APPROVED_PLAN,
        JournalLifecycleEventType.FILL,
        JournalLifecycleEventType.CLOSE,
        JournalLifecycleEventType.RECONCILE,
    }
)


def planned_setup_quality(assessment: SetupAssessment) -> PlannedSetupQualityFacts:
    return planned_setup_quality_from_identity(
        assessment_id=assessment.assessment_id,
        assessment_state=assessment.state,
        assessment_content_hash=assessment.content_hash,
        evidence_window_hash=assessment.evidence_window_hash,
        reason_codes=tuple(code.value for code in assessment.reason_codes),
    )


def planned_setup_quality_from_identity(
    *,
    assessment_id: UUID,
    assessment_state: SetupAssessmentState,
    assessment_content_hash: str,
    evidence_window_hash: str,
    reason_codes: tuple[str, ...] = (),
) -> PlannedSetupQualityFacts:
    """Copy stored SetupAssessment identity. Does not re-evaluate setup truth."""

    axis = {
        SetupAssessmentState.CONFIRMED_SETUP: PlannedSetupQuality.CONFIRMED,
        SetupAssessmentState.INVALIDATED: PlannedSetupQuality.INVALIDATED,
        SetupAssessmentState.EXPIRED: PlannedSetupQuality.EXPIRED,
    }.get(assessment_state, PlannedSetupQuality.NOT_CONFIRMED)
    return PlannedSetupQualityFacts(
        axis=axis,
        assessment_id=assessment_id,
        assessment_state=assessment_state,
        assessment_content_hash=assessment_content_hash,
        evidence_window_hash=evidence_window_hash,
        reason_codes=reason_codes,
        confirmed=assessment_state is SetupAssessmentState.CONFIRMED_SETUP,
    )


def approval_channel_from_payload(payload: dict[str, object] | None) -> str | None:
    """Read the authorization channel carried on a journal payload.

    Missing or non-string values stay unset. Legacy events have no channel and
    remain human approvals.
    """

    if not payload:
        return None
    value = payload.get("approval_channel")
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def decision_actor_for(
    event_type: JournalLifecycleEventType,
    *,
    approval_channel: str | None = None,
) -> DecisionActor:
    if event_type is JournalLifecycleEventType.CANDIDATE_CONFIRMED:
        return DecisionActor.SYSTEM_SETUP
    if event_type is JournalLifecycleEventType.REJECT:
        return DecisionActor.HUMAN_DECISION
    if event_type is JournalLifecycleEventType.SKIP:
        return DecisionActor.HUMAN_DECISION
    if event_type is JournalLifecycleEventType.APPROVED_PLAN:
        if approval_channel == AuthorizationChannel.AUTONOMOUS.value:
            return DecisionActor.AUTONOMOUS_APPROVAL
        return DecisionActor.HUMAN_APPROVAL
    return DecisionActor.PAPER_SYSTEM_EXECUTION


def human_plan_approved(
    event_type: JournalLifecycleEventType,
    trade: JournalTrade | None,
    payload: dict[str, object] | None = None,
) -> bool:
    """Human approval flag. An AUTONOMOUS channel is never a human approval.

    FILL and CLOSE still carry an execution lifecycle id. That shortcut must
    not turn an autonomous trade into a human approval when facts are folded.
    """

    if approval_channel_from_payload(payload) == AuthorizationChannel.AUTONOMOUS.value:
        return False
    return event_type is JournalLifecycleEventType.APPROVED_PLAN or (
        trade is not None and trade.execution_lifecycle_id is not None
    )


def trader_behavior_for(
    event_type: JournalLifecycleEventType,
    *,
    approval_channel: str | None = None,
) -> TraderBehavior:
    if (
        event_type is JournalLifecycleEventType.APPROVED_PLAN
        and approval_channel == AuthorizationChannel.AUTONOMOUS.value
    ):
        return TraderBehavior.AUTONOMOUS_APPROVAL
    mapping = {
        JournalLifecycleEventType.CANDIDATE_CONFIRMED: TraderBehavior.AWAITING_DECISION,
        JournalLifecycleEventType.REJECT: TraderBehavior.REJECTED,
        JournalLifecycleEventType.SKIP: TraderBehavior.SKIPPED,
        JournalLifecycleEventType.APPROVED_PLAN: TraderBehavior.APPROVED_PLAN,
        JournalLifecycleEventType.FILL: TraderBehavior.EXECUTED,
        JournalLifecycleEventType.CLOSE: TraderBehavior.EXECUTED,
        JournalLifecycleEventType.RECONCILE: TraderBehavior.RECONCILED,
    }
    return mapping[event_type]


def executed_trade_outcome(
    event_type: JournalLifecycleEventType,
    trade: JournalTrade | None,
) -> bool:
    """REJECT/SKIP/candidate confirmation never count as executed outcomes."""
    if event_type not in _EXECUTING:
        return False
    if trade is None:
        return False
    if trade.status is JournalTradeStatus.PLANNED:
        return False
    if trade.status is JournalTradeStatus.CANCELLED:
        return False
    return trade.status in {JournalTradeStatus.OPEN, JournalTradeStatus.CLOSED}


def outcome_facts(
    *,
    event_type: JournalLifecycleEventType,
    trade: JournalTrade | None,
) -> TradeOutcomeFacts:
    eligible = executed_trade_outcome(event_type, trade)
    if not eligible or trade is None:
        return TradeOutcomeFacts(
            eligible=False,
            journal_trade_id=None if trade is None or event_type not in _EXECUTING else trade.id,
            status=None if trade is None else trade.status,
            result=None,
            net_pnl=None,
            gross_pnl=None,
        )
    return TradeOutcomeFacts(
        eligible=True,
        journal_trade_id=trade.id,
        status=trade.status,
        result=trade.result,
        net_pnl=_decimal_or_none(trade.net_pnl),
        gross_pnl=_decimal_or_none(trade.gross_pnl),
    )


def behavior_facts(
    *,
    event_type: JournalLifecycleEventType,
    candidate: Candidate,
    trade: JournalTrade | None,
    payload: dict[str, object] | None = None,
) -> TraderBehaviorFacts:
    channel = approval_channel_from_payload(payload)
    return TraderBehaviorFacts(
        axis=trader_behavior_for(event_type, approval_channel=channel),
        actor=decision_actor_for(
            event_type,
            approval_channel=channel,
        ),
        event_type=event_type,
        candidate_state=candidate.state,
        executed_trade_outcome=executed_trade_outcome(event_type, trade),
    )


def risk_adherence_facts(
    *,
    event_type: JournalLifecycleEventType,
    trade: JournalTrade | None,
    payload: dict[str, object] | None = None,
) -> RiskAdherenceFacts:
    """Stop/size plan adherence. Early-exit capture is execution quality, not this axis."""
    body = payload or {}
    if event_type not in _EXECUTING or trade is None:
        return RiskAdherenceFacts(axis=RiskAdherence.NOT_APPLICABLE)
    planned_stop = _decimal_or_none(trade.planned_stop_price)
    actual_exit = _decimal_or_none(trade.exit_price)
    planned_risk = _decimal_or_none(trade.planned_risk_amount)
    planned_size = _decimal_from_payload(body.get("planned_size"))
    actual_size = _decimal_or_none(trade.size)
    size_deviation = _entry_deviation_bps(planned_size, actual_size)
    if trade.status is JournalTradeStatus.PLANNED:
        return RiskAdherenceFacts(
            axis=RiskAdherence.NOT_YET_ASSESSED,
            journal_trade_id=trade.id,
            planned_risk_amount=planned_risk,
            planned_stop_price=planned_stop,
            planned_size=planned_size,
            actual_size=actual_size,
            size_deviation_bps=size_deviation,
        )
    if trade.entry_price is None:
        axis = RiskAdherence.INCOMPLETE_FACTS
    elif _is_stop_exit(trade, body) and _stop_deviated(planned_stop, actual_exit):
        axis = RiskAdherence.STOP_VIOLATION
    elif size_deviation is not None and abs(size_deviation) > SIZE_MATCH_BPS:
        axis = RiskAdherence.SIZE_OR_RISK_VIOLATION
    elif trade.status in {JournalTradeStatus.OPEN, JournalTradeStatus.CLOSED}:
        axis = RiskAdherence.ADHERED
    else:
        axis = RiskAdherence.INCOMPLETE_FACTS
    return RiskAdherenceFacts(
        axis=axis,
        journal_trade_id=trade.id,
        planned_risk_amount=planned_risk,
        planned_stop_price=planned_stop,
        actual_exit_price=actual_exit,
        planned_size=planned_size,
        actual_size=actual_size,
        size_deviation_bps=size_deviation,
    )


def execution_quality_facts(
    *,
    event_type: JournalLifecycleEventType,
    trade: JournalTrade | None,
    payload: dict[str, object] | None = None,
) -> ExecutionQualityFacts:
    if event_type not in _EXECUTING or trade is None:
        return ExecutionQualityFacts(axis=ExecutionQuality.NOT_APPLICABLE)
    planned_entry = _decimal_or_none(trade.planned_entry_price)
    actual_entry = _decimal_or_none(trade.entry_price)
    planned_stop = _decimal_or_none(trade.planned_stop_price)
    actual_exit = _decimal_or_none(trade.exit_price)
    slippage = _decimal_or_none(trade.slippage)
    capture = _capture_pct(trade, payload)
    deviation = _entry_deviation_bps(planned_entry, actual_entry)
    axis = _execution_axis(
        trade=trade,
        deviation=deviation,
        capture=capture,
        planned_stop=planned_stop,
        actual_exit=actual_exit,
        payload=payload or {},
    )
    return ExecutionQualityFacts(
        axis=axis,
        journal_trade_id=trade.id,
        journal_status=trade.status,
        planned_entry_price=planned_entry,
        actual_entry_price=actual_entry,
        entry_deviation_bps=deviation,
        planned_stop_price=planned_stop,
        actual_exit_price=actual_exit,
        recorded_slippage=slippage,
        capture_pct=capture,
    )


def strategy_pattern_facts(
    *,
    candidate: Candidate,
    event_type: JournalLifecycleEventType,
    trade: JournalTrade | None,
    outcome: TradeOutcomeFacts,
    learning_venue_mode: LearningVenueMode = LearningVenueMode.PAPER_INTERNAL,
    payload: dict[str, object] | None = None,
) -> StrategyPatternStatFacts:
    filled = trade is not None and trade.status in {
        JournalTradeStatus.OPEN,
        JournalTradeStatus.CLOSED,
    }
    closed = trade is not None and trade.status is JournalTradeStatus.CLOSED
    result = outcome.result if outcome.eligible else None
    autonomous = approval_channel_from_payload(payload) == AuthorizationChannel.AUTONOMOUS.value
    return StrategyPatternStatFacts(
        strategy_version_id=candidate.strategy_version_id,
        setup_definition_id=candidate.setup_definition_id,
        fusion_policy_version=candidate.fusion_policy_version,
        uniqueness_tuple_hash=candidate.uniqueness_tuple().canonical_hash(),
        learning_venue_mode=learning_venue_mode,
        candidate_confirmed=True,
        rejected=event_type is JournalLifecycleEventType.REJECT,
        skipped=event_type is JournalLifecycleEventType.SKIP,
        plan_approved=human_plan_approved(event_type, trade, payload),
        autonomous_approval=autonomous,
        filled=filled and outcome.eligible,
        closed=closed and outcome.eligible,
        executed_outcome=outcome.eligible,
        win=result is TradeResult.WIN,
        loss=result is TradeResult.LOSS,
        breakeven=result is TradeResult.BREAKEVEN,
    )


def _execution_axis(
    *,
    trade: JournalTrade,
    deviation: Decimal | None,
    capture: Decimal | None,
    planned_stop: Decimal | None,
    actual_exit: Decimal | None,
    payload: dict[str, object],
) -> ExecutionQuality:
    if trade.status is JournalTradeStatus.PLANNED:
        return ExecutionQuality.NOT_YET_EXECUTED
    if trade.entry_price is None:
        return ExecutionQuality.INCOMPLETE_VENUE_FACTS
    if capture is not None and capture < EARLY_EXIT_CAPTURE_PCT:
        return ExecutionQuality.EARLY_EXIT
    if _is_stop_exit(trade, payload) and _stop_deviated(planned_stop, actual_exit):
        return ExecutionQuality.STOP_DEVIATION
    if deviation is not None and abs(deviation) > ENTRY_MATCH_BPS:
        return ExecutionQuality.SLIPPAGE_DEVIATION
    if trade.status in {JournalTradeStatus.OPEN, JournalTradeStatus.CLOSED}:
        return ExecutionQuality.MATCHED_PLAN
    return ExecutionQuality.INCOMPLETE_VENUE_FACTS


_STOP_REASONS = frozenset({"stop", "stop_loss", "stopped", "invalidation", "sl"})


def _is_stop_exit(trade: JournalTrade, payload: dict[str, object]) -> bool:
    raw = trade.exit_reason if trade.exit_reason is not None else payload.get("exit_reason")
    if raw is None:
        return False
    return str(raw).strip().lower() in _STOP_REASONS


def _stop_deviated(planned_stop: Decimal | None, actual_exit: Decimal | None) -> bool:
    if planned_stop is None or actual_exit is None:
        return False
    bps = _relative_bps(actual_exit, planned_stop)
    return bps is not None and abs(bps) > ENTRY_MATCH_BPS


def _entry_deviation_bps(planned: Decimal | None, actual: Decimal | None) -> Decimal | None:
    if planned is None or actual is None:
        return None
    return _relative_bps(actual, planned)


def _relative_bps(actual: Decimal, planned: Decimal) -> Decimal | None:
    if planned == 0:
        return None
    return ((actual - planned) / planned * Decimal("10000")).quantize(Decimal("0.01"))


def _capture_pct(trade: JournalTrade, payload: dict[str, object] | None) -> Decimal | None:
    raw: Any = trade.realized_vs_available_pct
    if raw is None and payload is not None:
        raw = payload.get("realized_vs_available_pct")
    if raw is None:
        return None
    return Decimal(str(raw))


def _decimal_or_none(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _decimal_from_payload(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
