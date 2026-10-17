"""Bounded reads of existing notification receipts; no detection or delivery effects."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import UserStrategy, UserStrategyVersion
from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.db.telegram_security import TelegramOutboxRow
from app.db.watcher_orchestration import (
    WatcherPolicyVersionRow,
    WatcherScanAttemptRow,
    WatcherScanLineageRow,
    WatcherSourceFetchAttemptRow,
    WatcherSubscriptionEvalAttemptRow,
)
from app.market_contracts.evidence_diagnostics import DiagnosticReason, EvidenceComponent
from app.schemas.nested_continuation import BrainSetupState
from app.schemas.telegram_policy import TelegramNotificationEvent
from app.signal_fusion.enums import EligibilityReasonCode, SetupAssessmentState
from app.strategy_brain.records import aware
from app.telegram_security.contracts import OutboxState
from app.telegram_security.errors import TelegramSecurityReason
from app.watcher.contracts import ScanAttemptStatus, UnitAttemptStatus

ROW_LIMIT = 100
WINDOW = timedelta(days=7)
_REASONS = frozenset(
    {
        "accepted",
        "canonical_contract_invalid_contract",
        "canonical_contract_unexpected_error",
        "canonical_market_monitor_invalid_contract",
        "candidate_creation_failed",
        "candidate_creation_forbidden",
        "confirmed_setup",
        "evaluation_exception",
        "failure_not_propagated",
        "missing_canonical_evidence",
        "missing_monitor",
        "notify_disabled",
        "organization_mismatch",
        "provider_outage",
        "risk_block",
        "kill_switch_active",
        "stale_evidence",
        "stale_fence",
        "strategy_evidence_mismatch",
        "watcher_disabled",
        "POLICY_STRATEGY_SUBSCRIPTIONS",
        "POLICY_SYMBOL_SUBSCRIPTIONS",
        "POLICY_SETUP_STAGES",
        "POLICY_EVENT_TYPES",
        "POLICY_SEVERITIES",
        "POLICY_MINIMUM_SEVERITY",
        "POLICY_EVENT_DISABLED",
        "POLICY_PHASE_MISSING",
        "POLICY_FORMING_DISABLED",
        "POLICY_CONFIRMED_DISABLED",
        "POLICY_QUALITY_MISSING",
        "POLICY_MINIMUM_QUALITY",
        "POLICY_QUIET_HOURS",
        "POLICY_DUPLICATE",
        "POLICY_COOLDOWN",
        "POLICY_FACTS_MISSING",
        "awaiting_controlled_pullback",
        "structural_failure",
        "formation_expired_new_episode_required",
        "pullback_duration_exceeded",
        "awaiting_closed_structural_break",
        "pullback_pivot_not_yet_confirmed",
        "required_volume_or_relative_threshold_not_met",
        "maximum_sweep_depth_exceeded",
        "setup_expired",
        "required_ohlcv_missing",
        "no_knowable_ohlcv",
        "required_causal_series_incomplete",
        "required_observation_stale",
        "required_ohlcv_tail_stale",
        "provisional_candle_cannot_confirm_or_invalidate",
        "sustained_closed_breakout_research_definition",
        "closed_structural_extreme_breached",
        "closed_reclaim_lost",
        "confirmed_structure_intact",
        "confirmation_window_elapsed",
        "closed_break_of_reclaim_extreme",
        "awaiting_closed_break_of_reclaim_extreme",
        "reclaim_window_elapsed",
        "closed_reclaim",
        "awaiting_later_closed_confirmation",
        "closed_structural_break",
        "controlled_pullback_confirmed",
    }
    | {reason.value for reason in DiagnosticReason}
    | {
        f"{'canonical_' if component is not EvidenceComponent.CONTRACT else ''}"
        f"{component.value}_{reason.value}"
        for component in EvidenceComponent
        for reason in DiagnosticReason
    }
    | {reason.value for reason in TelegramSecurityReason}
    | {reason.value for reason in SetupAssessmentState}
    | {reason.value for reason in EligibilityReasonCode}
)
_STATES = frozenset(
    state.value
    for group in (BrainSetupState, OutboxState, ScanAttemptStatus, UnitAttemptStatus)
    for state in group
)


def _reason(value: str | None) -> str:
    # Stored errors can contain provider payloads. Only stable known codes leave this read.
    return value if value in _REASONS else "unrecorded" if value is None else "unrecognized_reason"


def _counts(values: list[str]) -> dict[str, int]:
    return dict(
        sorted(Counter(value if value in _STATES else "unrecorded" for value in values).items())
    )


def _reason_counts(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def read_notification_diagnostics(
    session: Session,
    *,
    organization_id: UUID,
    user_id: UUID,
    strategies: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project exact persisted stages for already owner-filtered selected versions.

    Counts are samples of at most 100 receipts per stage/version in seven days,
    never claims of continuous scan coverage or of notifications read by a human.
    """
    now = aware(now or datetime.now(UTC))
    since = now - WINDOW
    scopes = []
    with session.no_autoflush:
        owned_versions = set(
            session.scalars(
                select(UserStrategyVersion.id)
                .join(UserStrategy, UserStrategy.id == UserStrategyVersion.strategy_id)
                .where(
                    UserStrategy.organization_id == organization_id,
                    UserStrategy.user_id == user_id,
                    UserStrategyVersion.id.in_(
                        [UUID(item["version_id"]) for item in strategies[:8]]
                    ),
                )
            )
        )
        for strategy in strategies[:8]:
            version_id = UUID(strategy["version_id"])
            if version_id not in owned_versions:
                continue
            scans_all = session.scalars(
                select(WatcherScanAttemptRow)
                .join(
                    WatcherScanLineageRow,
                    WatcherScanLineageRow.lineage_id == WatcherScanAttemptRow.lineage_id,
                )
                .join(
                    WatcherPolicyVersionRow,
                    (WatcherPolicyVersionRow.policy_id == WatcherScanLineageRow.policy_id)
                    & (WatcherPolicyVersionRow.version == WatcherScanLineageRow.policy_version),
                )
                .where(
                    WatcherScanAttemptRow.organization_id == organization_id,
                    WatcherScanLineageRow.organization_id == organization_id,
                    WatcherPolicyVersionRow.organization_id == organization_id,
                    WatcherPolicyVersionRow.user_id == user_id,
                    WatcherPolicyVersionRow.strategy_version_id == version_id,
                    WatcherScanAttemptRow.started_at >= since,
                    WatcherScanAttemptRow.started_at <= now,
                )
                .order_by(WatcherScanAttemptRow.started_at.desc(), WatcherScanAttemptRow.attempt_id)
                .limit(ROW_LIMIT + 1)
            ).all()
            scans = scans_all[:ROW_LIMIT]
            scan_ids = [row.attempt_id for row in scans]
            acquisition: list[WatcherSourceFetchAttemptRow | WatcherSubscriptionEvalAttemptRow] = []
            evaluations: list[WatcherSourceFetchAttemptRow | WatcherSubscriptionEvalAttemptRow] = []
            truncated = {"scans": len(scans_all) > ROW_LIMIT}
            for name, model, rows in (
                ("acquisition", WatcherSourceFetchAttemptRow, acquisition),
                ("evaluation", WatcherSubscriptionEvalAttemptRow, evaluations),
            ):
                found = session.scalars(
                    select(model)
                    .where(
                        model.organization_id == organization_id,
                        model.scan_attempt_id.in_(scan_ids),
                        model.created_at >= since,
                        model.created_at <= now,
                    )
                    .order_by(model.created_at.desc())
                    .limit(ROW_LIMIT + 1)
                ).all()
                rows.extend(
                    cast(
                        list[WatcherSourceFetchAttemptRow | WatcherSubscriptionEvalAttemptRow],
                        found[:ROW_LIMIT],
                    )
                )
                truncated[name] = len(found) > ROW_LIMIT
            events_all = session.execute(
                select(BrainSetupEventRow, BrainSetupRow)
                .join(BrainSetupRow, BrainSetupRow.id == BrainSetupEventRow.setup_id)
                .where(
                    BrainSetupEventRow.organization_id == organization_id,
                    BrainSetupRow.organization_id == organization_id,
                    BrainSetupRow.strategy_version_id == version_id,
                    BrainSetupEventRow.occurred_at >= since,
                    BrainSetupEventRow.occurred_at <= now,
                )
                .order_by(BrainSetupEventRow.occurred_at.desc(), BrainSetupEventRow.id)
                .limit(ROW_LIMIT + 1)
            ).all()
            events = events_all[:ROW_LIMIT]
            truncated["detections"] = len(events_all) > ROW_LIMIT
            outbox_all = session.scalars(
                select(TelegramOutboxRow)
                .where(
                    TelegramOutboxRow.organization_id == organization_id,
                    TelegramOutboxRow.user_id == user_id,
                    TelegramOutboxRow.notification_event["strategy_id"].as_string()
                    == str(version_id),
                    TelegramOutboxRow.created_at >= since,
                    TelegramOutboxRow.created_at <= now,
                )
                .order_by(TelegramOutboxRow.created_at.desc(), TelegramOutboxRow.outbox_id)
                .limit(ROW_LIMIT + 1)
            ).all()
            outbox = outbox_all[:ROW_LIMIT]
            truncated["delivery"] = len(outbox_all) > ROW_LIMIT
            deliveries = []
            invalid_facts = 0
            for row in outbox:
                try:
                    event = TelegramNotificationEvent.model_validate(row.notification_event)
                except ValidationError:
                    invalid_facts += 1
                    continue
                deliveries.append(
                    {
                        "outbox_id": str(row.outbox_id),
                        "event_type": event.event_type.value,
                        "state": row.state if row.state in _STATES else "unrecorded",
                        "reason": _reason(row.last_error),
                        "attempt": row.attempt,
                        "created_at": aware(row.created_at).isoformat(),
                        "sent_at": aware(row.sent_at).isoformat() if row.sent_at else None,
                        "provider_receipt_present": bool(row.transport_message_id),
                    }
                )
            gaps = []
            if not scans:
                gaps.append("scan_receipts_missing")
            if not acquisition:
                gaps.append("acquisition_receipts_missing")
            if not evaluations:
                gaps.append("evaluation_receipts_missing")
            if not events:
                gaps.append("detection_receipts_missing")
            if not outbox:
                gaps.append("delivery_receipts_missing")
            if invalid_facts:
                gaps.append("invalid_notification_facts")
            scopes.append(
                {
                    "family": "sfp"
                    if strategy["spec"]["kind"] == "swing_failure_pattern/v1"
                    else "nested",
                    "strategy_version_id": str(version_id),
                    "symbol": strategy["spec"]["symbol"],
                    "timeframe": strategy["spec"]["trigger_timeframe"],
                    "scan_status_counts": _counts([row.status for row in scans]),
                    "scan_reasons": _reason_counts(
                        [_reason(row.outcome_reason_code) for row in scans]
                    ),
                    "acquisition_status_counts": _counts([row.status for row in acquisition]),
                    "acquisition_reasons": _reason_counts(
                        [_reason(row.reason_code) for row in acquisition]
                    ),
                    "evaluation_status_counts": _counts([row.status for row in evaluations]),
                    "evaluation_reasons": _reason_counts(
                        [_reason(row.reason_code) for row in evaluations]
                    ),
                    "detection_states": _counts(
                        [
                            event.payload.get("state", "unrecorded")
                            for event, _ in events
                            if event.kind == "system_observation"
                        ]
                    ),
                    "detection_reasons": _reason_counts(
                        [
                            _reason(reason)
                            for event, _ in events
                            if event.kind == "system_observation"
                            for reason in event.payload.get("reason_codes", [])
                            if isinstance(reason, str)
                        ]
                    ),
                    "decision_reasons": _reason_counts(
                        [
                            _reason(reason)
                            for event, _ in events
                            if event.kind in {"paper_decision", "paper_trade_opened"}
                            for reason in event.payload.get("reason_codes", [])
                            if isinstance(reason, str)
                        ]
                    ),
                    "candidate_links": len(
                        {row.candidate_id for _, row in events if row.candidate_id}
                    ),
                    "delivery_states": _counts([row.state for row in outbox]),
                    "deliveries": deliveries[:8],
                    "truncated": truncated,
                    "missing_evidence": gaps,
                }
            )
    return {
        "window_start": since.isoformat(),
        "window_end": now.isoformat(),
        "per_stage_row_limit": ROW_LIMIT,
        "scopes": scopes,
        "limitations": [
            "Persisted samples only; missing rows do not prove no detection or no delivery.",
            "Scan success alone does not establish continuous coverage "
            "or component coverage proofs.",
            "Outbox SENT/ACKNOWLEDGED state is not proof a human read the message.",
            "Legacy outbox rows without typed strategy facts "
            "cannot be attributed to these versions.",
        ],
    }
