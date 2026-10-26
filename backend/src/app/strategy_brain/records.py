"""Idempotent stored projections. These records never grant execution permission."""

from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.canonical_eligibility import ActionEligibilityEvaluationRow
from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.schemas.nested_continuation import BrainSetupState
from app.strategy_brain.detector import NAMESPACE, NestedDetection, detection_hash
from app.strategy_brain.sfp.contracts import SfpDetection

TERMINAL = {"INVALIDATED", "EXPIRED", "COMPLETED"}
# Only Nested and SFP write setups; rows stored before Nested carried a family are Nested.
NESTED_FAMILY = "nested_continuation"
ALLOWED = {
    "NO_SETUP": {"WATCH", "FORMING"},
    "WATCH": {"FORMING", "INVALIDATED", "EXPIRED"},
    "FORMING": {"CONFIRMED", "INVALIDATED", "EXPIRED"},
    "CONFIRMED": {"TRADE_CANDIDATE", "BLOCKED_BY_RISK", "INVALIDATED", "EXPIRED"},
    "TRADE_CANDIDATE": {"BLOCKED_BY_RISK", "COMPLETED", "INVALIDATED", "EXPIRED"},
    "BLOCKED_BY_RISK": {"TRADE_CANDIDATE", "COMPLETED", "INVALIDATED", "EXPIRED"},
}


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def scoped_setup_id(organization_id: UUID, version_id: UUID, setup_id: UUID) -> UUID:
    return uuid5(NAMESPACE, f"{organization_id}:{version_id}:{setup_id}")


def transition(row: BrainSetupRow, new: str) -> None:
    if row.state == new:
        return
    if new not in ALLOWED.get(row.state, set()):
        raise ValueError(f"Illegal setup transition {row.state} -> {new}")
    row.state = new


def _insert_once(session: Session, row: object) -> bool:
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
        return True
    except IntegrityError:
        if session.get(type(row), row.id) is None:
            raise
        return False


def record_detections(
    session: Session,
    *,
    organization_id: UUID,
    strategy_id: UUID,
    version_id: UUID,
    spec: object,
    bars: tuple,
    events: tuple[NestedDetection, ...],
    evidence_hash: str,
    evaluated_at: datetime,
) -> UUID | None:
    latest_id = None
    for event in events:
        identity = scoped_setup_id(organization_id, version_id, event.setup_id)
        latest_id = identity
        row = session.scalar(
            select(BrainSetupRow)
            .where(BrainSetupRow.id == identity, BrainSetupRow.organization_id == organization_id)
            .with_for_update()
        )
        if row is None:
            row = BrainSetupRow(
                id=identity,
                organization_id=organization_id,
                strategy_id=strategy_id,
                strategy_version_id=version_id,
                symbol=spec.symbol,
                state="NO_SETUP",
                observed_at=event.detected_at,
                expires_at=event.expires_at,
                payload={},
            )
            if not _insert_once(session, row):
                row = session.scalar(
                    select(BrainSetupRow)
                    .where(
                        BrainSetupRow.id == identity,
                        BrainSetupRow.organization_id == organization_id,
                    )
                    .with_for_update()
                )
            assert row is not None
        event_id = uuid5(identity, detection_hash(event, bars))
        if session.get(BrainSetupEventRow, event_id) is not None:
            continue
        if aware(event.detected_at) < aware(row.observed_at) or row.state in TERMINAL:
            continue
        # Replay may first see a confirmed candle: retain its observed forming path.
        if row.state == "NO_SETUP":
            transition(row, "WATCH")
        if (
            event.state in {BrainSetupState.FORMING, BrainSetupState.CONFIRMED}
            and row.state == "WATCH"
        ):
            transition(row, "FORMING")
        # Account decisions remain authoritative; replay cannot revert them to pattern state.
        if row.state not in {"TRADE_CANDIDATE", "BLOCKED_BY_RISK"} or event.state.value in TERMINAL:
            transition(row, event.state.value)
        row.observed_at = event.detected_at
        row.expires_at = event.expires_at
        row.payload = {
            **event.model_dump(mode="json"),
            "family": NESTED_FAMILY,
            "setup_id": str(identity),
            "strategy_version_id": str(version_id),
            "strategy_id": str(strategy_id),
            "evidence_reference": detection_hash(event, bars),
            "canonical_scan_reference": evidence_hash
            if event.event_index == len(bars) - 1
            else None,
            "evidence_at": event.detected_at.isoformat(),
            "venue": bars[0].instrument.venue.value,
            "instrument": bars[0].instrument.instrument_id,
            "market_type": bars[0].instrument.market_type.value,
            "timeframe": spec.trigger_timeframe.value,
            "bar_references": [
                bar.content_hash for bar in bars[event.anchor_index : event.event_index + 1]
            ],
            "rule_results": event.reason_codes,
            "data_quality": "AVAILABLE",
            "risk_state": (row.payload or {}).get("risk_state", "not_evaluated"),
        }
        _insert_once(
            session,
            BrainSetupEventRow(
                id=event_id,
                organization_id=organization_id,
                setup_id=identity,
                kind="system_observation",
                occurred_at=event.detected_at,
                payload=row.payload,
            ),
        )
    return latest_id


def attach_paper_result(
    session: Session,
    *,
    organization_id: UUID,
    setup_id: UUID,
    candidate_id: UUID,
    assessment_id: UUID,
    proof: object,
    now: datetime,
) -> None:
    row = session.scalar(
        select(BrainSetupRow)
        .where(BrainSetupRow.id == setup_id, BrainSetupRow.organization_id == organization_id)
        .with_for_update()
    )
    if row is None or row.state in TERMINAL:
        return
    row.candidate_id = candidate_id
    row.assessment_id = assessment_id
    row.decision_id = proof.eligibility_id
    transition(row, "TRADE_CANDIDATE")
    if proof.paper_loop_reason in {"risk_block", "kill_switch_active"}:
        transition(row, "BLOCKED_BY_RISK")
    row.journal_trade_id = proof.journal_trade_id
    decision = (
        session.scalar(
            select(ActionEligibilityEvaluationRow).where(
                ActionEligibilityEvaluationRow.eligibility_id == proof.eligibility_id,
                ActionEligibilityEvaluationRow.organization_id == organization_id,
                ActionEligibilityEvaluationRow.candidate_id == candidate_id,
            )
        )
        if proof.eligibility_id
        else None
    )
    decision_reasons = (
        decision.payload.get("eligibility", {}).get("reason_codes", []) if decision else []
    )
    payload = {
        **row.payload,
        "risk_state": proof.eligibility_state or proof.paper_loop_reason,
        "paper_stage": proof.paper_loop_stage,
        "reason_codes": list(
            dict.fromkeys(
                [*row.payload.get("reason_codes", []), proof.paper_loop_reason, *decision_reasons]
            )
        ),
        "risk_reason_codes": decision_reasons,
        "candidate_id": str(candidate_id),
        "assessment_id": str(assessment_id),
        "decision_id": str(proof.eligibility_id) if proof.eligibility_id else None,
        "execution_command_id": str(proof.execution_command_id)
        if proof.execution_command_id
        else None,
        "journal_trade_id": str(proof.journal_trade_id) if proof.journal_trade_id else None,
        "trade_plan_revision_id": str(proof.trade_plan_revision_id)
        if proof.trade_plan_revision_id
        else None,
        "paper_fill_id": str(proof.paper_fill_id) if proof.paper_fill_id else None,
    }
    row.payload = payload
    _insert_once(
        session,
        BrainSetupEventRow(
            id=uuid5(
                setup_id,
                f"decision:{candidate_id}:{proof.paper_loop_stage}:{proof.paper_loop_reason}",
            ),
            organization_id=organization_id,
            setup_id=setup_id,
            kind="paper_trade_opened" if proof.journal_trade_id else "paper_decision",
            occurred_at=now,
            payload=payload,
        ),
    )


def record_journal_close(session: Session, trade: object) -> None:
    """Link an actual canonical journal closure; never impute missed-trade PnL."""
    row = session.scalar(
        select(BrainSetupRow)
        .where(
            BrainSetupRow.journal_trade_id == trade.id,
            BrainSetupRow.organization_id == trade.organization_id,
        )
        .with_for_update()
    )
    if row is None or row.state == "COMPLETED":
        return
    if row.state in {"TRADE_CANDIDATE", "BLOCKED_BY_RISK"}:
        transition(row, "COMPLETED")
    _insert_once(
        session,
        BrainSetupEventRow(
            id=uuid5(row.id, f"closed:{trade.id}"),
            organization_id=row.organization_id,
            setup_id=row.id,
            kind="paper_trade_closed",
            occurred_at=trade.exit_time or datetime.now(UTC),
            payload={
                "journal_trade_id": str(trade.id),
                "provenance": "actual_outcome",
                "outcome_reference": str(trade.id),
            },
        ),
    )


def lock_candidate_episode(
    session: Session,
    *,
    organization_id: UUID,
    version_id: UUID,
    detection: NestedDetection | SfpDetection,
) -> BrainSetupRow:
    """Structural uniqueness supplements canonical evidence-window uniqueness."""
    identity = scoped_setup_id(organization_id, version_id, detection.setup_id)
    row = session.scalar(
        select(BrainSetupRow)
        .where(
            BrainSetupRow.id == identity,
            BrainSetupRow.organization_id == organization_id,
            BrainSetupRow.strategy_version_id == version_id,
        )
        .with_for_update()
    )
    if row is None:
        raise ValueError("Missing persisted Brain structural episode")
    return row
