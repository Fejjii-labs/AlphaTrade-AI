"""Phase 4 journal lifecycle projector.

Candidate, REJECT and SKIP write lifecycle/audit events only and never create
``JournalTrade``. Approved-plan / fill / close / reconcile events resolve the
database-unique ``(organization_id, execution_lifecycle_id)`` aggregate.

This service does not enqueue workers, call exchanges, or auto-journal from
execution. Callers invoke it explicitly. Automatic projection is Phase 10.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import JournalProjectionConflictError, ValidationAppError
from app.core.operation_policy import PersistenceKind, assert_write_allowed
from app.db.models import (
    JournalLifecycleEvent,
    JournalProjectionReceipt,
    JournalTrade,
)
from app.repositories.journal_trades import JournalTradeRepository
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    ActorType,
    AuditEventType,
    JournalEntryMethod,
    JournalLifecycleEventType,
    JournalReconciliationPosition,
    JournalTradeSource,
    JournalTradeStatus,
    TradeDirection,
    TradeResult,
)
from app.schemas.journal_lifecycle import JournalLifecycleEventInput, JournalProjectionResult
from app.schemas.journal_trades import PlannedTarget
from app.services.audit_service import AuditService
from app.services.canonical_execution_journal import journal_planned_targets
from app.services.canonical_serialization import canonical_sha256
from app.services.journal_integrity import (
    is_journal_lifecycle_event_unique_violation,
    is_journal_lifecycle_unique_violation,
    is_journal_receipt_unique_violation,
)
from app.services.journal_lifecycle_lineage import (
    assert_lineage_matches_event_scope,
    assert_sticky_lineage,
    extract_lineage_map,
    merge_lineage_maps,
)
from app.services.journal_target_repair import approved_plan_for_target_repair

_REQUEST_TAG = "journal-lifecycle-projector"
_NON_CREATING = frozenset(
    {
        JournalLifecycleEventType.CANDIDATE_CONFIRMED,
        JournalLifecycleEventType.REJECT,
        JournalLifecycleEventType.SKIP,
    }
)
_UNRESOLVED_RECONCILIATION = frozenset({"RECONCILIATION_REQUIRED", "OPERATOR_HOLD"})
_MONEY_FIELDS = frozenset(
    {
        "planned_entry_price",
        "planned_stop_price",
        "planned_risk_amount",
        "entry_price",
        "exit_price",
        "size",
        "leverage",
        "fees",
        "funding",
        "slippage",
        "gross_pnl",
        "net_pnl",
    }
)
_UUID_FIELDS = frozenset(
    {
        "linked_position_id",
        "linked_paper_trade_id",
        "linked_proposal_id",
        "linked_order_id",
        "setup_id",
        "user_strategy_id",
        "strategy_version_id",
    }
)
_CREATE_FIELDS = frozenset(
    {
        "symbol",
        "timeframe",
        "direction",
        "exchange",
        "thesis",
        "trigger",
        "entry_plan",
        "invalidation",
        "planned_entry_price",
        "planned_stop_price",
        "planned_targets",
        "runner_enabled",
        "runner_plan",
        "planned_risk_amount",
        "strategy_label",
        "setup_id",
        "user_strategy_id",
        "strategy_version_id",
        "linked_proposal_id",
        "linked_position_id",
        "linked_paper_trade_id",
        "linked_order_id",
        "notes",
        "tags",
    }
)
_VENUE_APPLY_FIELDS = frozenset(
    {
        "entry_price",
        "entry_time",
        "exit_price",
        "exit_time",
        "exit_reason",
        "size",
        "leverage",
        "fees",
        "funding",
        "slippage",
        "gross_pnl",
        "net_pnl",
        "result",
        "status",
        "linked_order_id",
        "linked_position_id",
        "linked_paper_trade_id",
    }
)
_EVENT_RANK = {
    JournalLifecycleEventType.APPROVED_PLAN: 10,
    JournalLifecycleEventType.FILL: 20,
    JournalLifecycleEventType.CLOSE: 30,
    JournalLifecycleEventType.RECONCILE: 40,
}
_STATUS_RANK = {
    JournalTradeStatus.PLANNED: 10,
    JournalTradeStatus.OPEN: 20,
    JournalTradeStatus.CLOSED: 30,
    JournalTradeStatus.CANCELLED: 30,
}


class JournalLifecycleProjector:
    """Resolve one canonical JournalTrade per execution lifecycle."""

    def __init__(self, session: Session, audit_service: AuditService) -> None:
        self._session = session
        self._trades = JournalTradeRepository(session)
        self._audit = audit_service

    def lock_execution_lifecycle(
        self,
        organization_id: uuid.UUID,
        execution_lifecycle_id: uuid.UUID | None,
    ) -> None:
        """Hold this lifecycle until the caller's transaction ends.

        The lock order matches projection: the advisory key, then the journal
        row. A second close waits here and then sees the committed status.
        """

        self._lock_lifecycle(organization_id, execution_lifecycle_id)

    def project(
        self,
        event: JournalLifecycleEventInput,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        actor_user_id: uuid.UUID | None = None,
    ) -> JournalProjectionResult:
        self._require_provenance(event)
        content_hash = self._event_hash(organization_id, event)
        self._lock_lifecycle(organization_id, event.execution_lifecycle_id)

        existing_receipt = self._find_receipt(organization_id, event)
        if existing_receipt is not None:
            self._assert_same_content(existing_receipt.content_hash, content_hash, event)
            return JournalProjectionResult(
                event_type=event.event_type,
                journal_trade_id=existing_receipt.journal_trade_id,
                created_journal_trade=False,
                replayed=True,
                skipped_reason=existing_receipt.skipped_reason,
            )
        existing_event = self._find_lifecycle_event(organization_id, event)
        if existing_event is not None:
            self._assert_same_content(existing_event.content_hash, content_hash, event)
            return JournalProjectionResult(
                event_type=event.event_type,
                journal_trade_id=existing_event.journal_trade_id,
                created_journal_trade=False,
                replayed=True,
                skipped_reason=None,
            )

        self._assert_lineage(organization_id, event)

        skipped_reason: str | None = None
        trade: JournalTrade | None = None
        created = False

        if event.event_type in _NON_CREATING:
            skipped_reason = "non_creating_lifecycle_event"
        elif event.execution_lifecycle_id is None:
            skipped_reason = "missing_execution_lifecycle"
        elif self._close_blocked(event):
            skipped_reason = "unresolved_reconciliation"
        else:
            trade, created = self._resolve_lifecycle_trade(
                event,
                organization_id=organization_id,
                user_id=user_id,
            )
            if trade is not None:
                self._assert_account_scope(trade, event)
                self._apply_event(trade, event)

        lifecycle_event = self._insert_lifecycle_event(
            event,
            organization_id=organization_id,
            user_id=user_id,
            actor_user_id=actor_user_id,
            journal_trade_id=trade.id if trade is not None else None,
            content_hash=content_hash,
        )
        receipt = self._insert_receipt(
            event,
            organization_id=organization_id,
            user_id=user_id,
            journal_trade_id=trade.id if trade is not None else None,
            created_journal_trade=created,
            skipped_reason=skipped_reason,
            content_hash=content_hash,
        )
        if receipt is not None and trade is None and receipt.journal_trade_id is not None:
            trade = self._trades.get_scoped(
                receipt.journal_trade_id, organization_id=organization_id
            )
            created = False

        resource_id: str | None = None
        if trade is not None:
            resource_id = str(trade.id)
        elif lifecycle_event is not None:
            resource_id = str(lifecycle_event.id)
        self._audit.record(
            AuditRecordCreate(
                request_id=_REQUEST_TAG,
                trace_id=event.correlation_id or _REQUEST_TAG,
                event_type=AuditEventType.JOURNAL_LIFECYCLE_PROJECTED,
                resource_type="journal_trade" if trade is not None else "journal_lifecycle_event",
                resource_id=resource_id,
                organization_id=organization_id,
                user_id=user_id,
                actor_type=ActorType.SYSTEM,
                metadata={
                    "event_type": event.event_type.value,
                    "created_journal_trade": created,
                    "skipped_reason": skipped_reason,
                    "replayed": False,
                    "account_id": str(event.account_id),
                },
            )
        )
        return JournalProjectionResult(
            event_type=event.event_type,
            journal_trade_id=trade.id if trade is not None else None,
            created_journal_trade=created,
            replayed=False,
            skipped_reason=skipped_reason,
        )

    def repair_planned_targets(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        account_id: uuid.UUID,
        journal_trade_id: uuid.UUID,
        revision_id: uuid.UUID,
        dry_run: bool = True,
    ) -> dict[str, object]:
        """Repair only empty derived targets. Caller owns commit/rollback; default read-only."""
        assert_write_allowed(PersistenceKind.JOURNAL)
        trade = self._session.scalar(
            select(JournalTrade).where(
                JournalTrade.id == journal_trade_id,
                JournalTrade.organization_id == organization_id,
                JournalTrade.user_id == user_id,
                JournalTrade.account_id == account_id,
            )
        )
        if trade is None:
            raise JournalProjectionConflictError("Journal target repair scope does not match.")
        if not dry_run:
            lifecycle_id = trade.execution_lifecycle_id
            self._lock_lifecycle(organization_id, lifecycle_id)
            self._session.refresh(trade, with_for_update=True)
            if (
                trade.organization_id,
                trade.user_id,
                trade.account_id,
                trade.execution_lifecycle_id,
            ) != (organization_id, user_id, account_id, lifecycle_id):
                raise JournalProjectionConflictError("Journal target repair scope changed.")
        envelope = approved_plan_for_target_repair(self._session, trade, revision_id=revision_id)
        targets = journal_planned_targets(envelope.plan)
        status = "already_correct" if trade.planned_targets == targets else "would_repair"
        if trade.planned_targets and trade.planned_targets != targets:
            raise JournalProjectionConflictError(
                "Existing Journal targets conflict; refusing overwrite."
            )
        if status == "would_repair" and not dry_run:
            AuditService(self._session, strict_mode=True).record(
                AuditRecordCreate(
                    request_id="journal-target-repair",
                    trace_id=str(journal_trade_id),
                    event_type=AuditEventType.JOURNAL_LIFECYCLE_PROJECTED,
                    action="repair_planned_targets",
                    resource_type="journal_trade",
                    resource_id=str(journal_trade_id),
                    organization_id=organization_id,
                    user_id=user_id,
                    actor_type=ActorType.SYSTEM,
                    metadata={
                        "action": "repair_planned_targets",
                        "account_id": str(account_id),
                        "execution_lifecycle_id": str(trade.execution_lifecycle_id),
                        "revision_id": str(revision_id),
                        "plan_content_hash": envelope.plan.content_hash,
                        "before": [],
                        "after": targets,
                    },
                )
            )
            trade.planned_targets = targets
            trade.projector_lock_version = int(trade.projector_lock_version or 0) + 1
            self._session.flush()
            status = "repaired"
        return {
            "status": status,
            "dry_run": dry_run,
            "journal_trade_id": str(journal_trade_id),
            "revision_id": str(revision_id),
            "plan_content_hash": envelope.plan.content_hash,
            "planned_targets": targets,
        }

    def _require_provenance(self, event: JournalLifecycleEventInput) -> None:
        if not event.source_system or not event.source_aggregate or not event.source_event_id:
            raise ValidationAppError(
                "Journal projection requires source_system, source_aggregate, and source_event_id.",
                details={"reason": "missing_provenance"},
            )
        if event.account_id is None:
            raise ValidationAppError(
                "Journal projection requires an explicit account scope.",
                details={"reason": "missing_account_scope"},
            )

    def _close_blocked(self, event: JournalLifecycleEventInput) -> bool:
        if event.event_type not in {
            JournalLifecycleEventType.CLOSE,
            JournalLifecycleEventType.RECONCILE,
        }:
            return False
        status = event.payload.get("reconciliation_status")
        if status is None:
            return False
        return str(status) in _UNRESOLVED_RECONCILIATION

    def _find_receipt(
        self,
        organization_id: uuid.UUID,
        event: JournalLifecycleEventInput,
    ) -> JournalProjectionReceipt | None:
        stmt = select(JournalProjectionReceipt).where(
            JournalProjectionReceipt.organization_id == organization_id,
            JournalProjectionReceipt.account_id == event.account_id,
            JournalProjectionReceipt.source_system == event.source_system,
            JournalProjectionReceipt.source_aggregate == event.source_aggregate,
            JournalProjectionReceipt.event_type == event.event_type,
            JournalProjectionReceipt.source_event_id == event.source_event_id,
            JournalProjectionReceipt.source_event_version == event.source_event_version,
            JournalProjectionReceipt.supersession == event.supersession,
        )
        return self._session.scalar(stmt)

    def _find_lifecycle_event(
        self,
        organization_id: uuid.UUID,
        event: JournalLifecycleEventInput,
    ) -> JournalLifecycleEvent | None:
        stmt = select(JournalLifecycleEvent).where(
            JournalLifecycleEvent.organization_id == organization_id,
            JournalLifecycleEvent.account_id == event.account_id,
            JournalLifecycleEvent.source_system == event.source_system,
            JournalLifecycleEvent.source_aggregate == event.source_aggregate,
            JournalLifecycleEvent.event_type == event.event_type,
            JournalLifecycleEvent.source_event_id == event.source_event_id,
            JournalLifecycleEvent.source_event_version == event.source_event_version,
            JournalLifecycleEvent.supersession == event.supersession,
        )
        return self._session.scalar(stmt)

    def _assert_same_content(
        self,
        stored_hash: str,
        incoming_hash: str,
        event: JournalLifecycleEventInput,
    ) -> None:
        if stored_hash == incoming_hash:
            return
        raise JournalProjectionConflictError(
            "Journal source identity replayed with conflicting semantic content.",
            details={
                "source_system": event.source_system,
                "source_aggregate": event.source_aggregate,
                "source_event_id": event.source_event_id,
                "source_event_version": event.source_event_version,
                "supersession": event.supersession,
                "account_id": str(event.account_id),
            },
        )

    def _assert_account_scope(self, trade: JournalTrade, event: JournalLifecycleEventInput) -> None:
        if trade.account_id is not None and trade.account_id != event.account_id:
            raise JournalProjectionConflictError(
                "Journal lifecycle belongs to a different account.",
                details={
                    "account_id": str(event.account_id),
                    "execution_lifecycle_id": str(event.execution_lifecycle_id),
                },
            )

    def _assert_lineage(
        self,
        organization_id: uuid.UUID,
        event: JournalLifecycleEventInput,
    ) -> None:
        assert_lineage_matches_event_scope(event, organization_id=organization_id)
        incoming = extract_lineage_map(event.payload)
        if not incoming:
            return
        stored = self._stored_lineage(organization_id, event.execution_lifecycle_id)
        assert_sticky_lineage(stored, incoming, event)

    def _stored_lineage(
        self,
        organization_id: uuid.UUID,
        execution_lifecycle_id: uuid.UUID | None,
    ) -> dict[str, str]:
        if execution_lifecycle_id is None:
            return {}
        stmt = select(JournalLifecycleEvent).where(
            JournalLifecycleEvent.organization_id == organization_id,
            JournalLifecycleEvent.execution_lifecycle_id == execution_lifecycle_id,
        )
        merged: dict[str, str] = {}
        for row in self._session.scalars(stmt):
            merged = merge_lineage_maps(merged, extract_lineage_map(row.payload))
        return merged

    def _lock_lifecycle(
        self,
        organization_id: uuid.UUID,
        execution_lifecycle_id: uuid.UUID | None,
    ) -> None:
        if execution_lifecycle_id is None:
            return
        bind = self._session.get_bind()
        if bind is None or bind.dialect.name != "postgresql":
            return
        digest = hashlib.sha256(f"{organization_id}:{execution_lifecycle_id}".encode()).digest()
        key1 = int.from_bytes(digest[:4], "big") % 2147483647
        key2 = int.from_bytes(digest[4:8], "big") % 2147483647
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:k1, :k2)"),
            {"k1": key1, "k2": key2},
        )
        self._session.execute(
            select(JournalTrade)
            .where(
                JournalTrade.organization_id == organization_id,
                JournalTrade.execution_lifecycle_id == execution_lifecycle_id,
            )
            .with_for_update()
        )

    def _event_hash(self, organization_id: uuid.UUID, event: JournalLifecycleEventInput) -> str:
        return canonical_sha256(
            {
                "organization_id": str(organization_id),
                "account_id": str(event.account_id),
                "event_type": event.event_type.value,
                "execution_lifecycle_id": (
                    str(event.execution_lifecycle_id) if event.execution_lifecycle_id else None
                ),
                "source_system": event.source_system,
                "source_aggregate": event.source_aggregate,
                "source_event_id": event.source_event_id,
                "source_event_version": event.source_event_version,
                "supersession": event.supersession,
                "payload": event.payload,
            }
        )

    def _insert_lifecycle_event(
        self,
        event: JournalLifecycleEventInput,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        actor_user_id: uuid.UUID | None,
        journal_trade_id: uuid.UUID | None,
        content_hash: str,
    ) -> JournalLifecycleEvent | None:
        row = JournalLifecycleEvent(
            organization_id=organization_id,
            user_id=user_id,
            account_id=event.account_id,
            event_type=event.event_type,
            execution_lifecycle_id=event.execution_lifecycle_id,
            source_system=event.source_system,
            source_aggregate=event.source_aggregate,
            source_event_id=event.source_event_id,
            source_event_version=event.source_event_version,
            supersession=event.supersession,
            payload=dict(event.payload),
            content_hash=content_hash,
            correlation_id=event.correlation_id,
            actor_user_id=actor_user_id,
            journal_trade_id=journal_trade_id,
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
            return row
        except IntegrityError as exc:
            if not is_journal_lifecycle_event_unique_violation(exc):
                raise
            existing = self._find_lifecycle_event(organization_id, event)
            if existing is None:
                raise
            self._assert_same_content(existing.content_hash, content_hash, event)
            return existing

    def _insert_receipt(
        self,
        event: JournalLifecycleEventInput,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        journal_trade_id: uuid.UUID | None,
        created_journal_trade: bool,
        skipped_reason: str | None,
        content_hash: str,
    ) -> JournalProjectionReceipt | None:
        row = JournalProjectionReceipt(
            organization_id=organization_id,
            user_id=user_id,
            account_id=event.account_id,
            event_type=event.event_type,
            execution_lifecycle_id=event.execution_lifecycle_id,
            source_system=event.source_system,
            source_aggregate=event.source_aggregate,
            source_event_id=event.source_event_id,
            source_event_version=event.source_event_version,
            supersession=event.supersession,
            journal_trade_id=journal_trade_id,
            created_journal_trade=created_journal_trade,
            skipped_reason=skipped_reason,
            content_hash=content_hash,
            correlation_id=event.correlation_id,
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
            return row
        except IntegrityError as exc:
            if not is_journal_receipt_unique_violation(exc):
                raise
            existing = self._find_receipt(organization_id, event)
            if existing is None:
                raise
            self._assert_same_content(existing.content_hash, content_hash, event)
            return existing

    def _resolve_lifecycle_trade(
        self,
        event: JournalLifecycleEventInput,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> tuple[JournalTrade | None, bool]:
        assert event.execution_lifecycle_id is not None
        existing = self._trades.find_by_execution_lifecycle(
            organization_id=organization_id,
            execution_lifecycle_id=event.execution_lifecycle_id,
        )
        if existing is not None:
            if existing.organization_id != organization_id:
                raise ValidationAppError(
                    "Execution lifecycle belongs to another organization.",
                    details={"reason": "cross_tenant_lifecycle"},
                )
            self._assert_account_scope(existing, event)
            return existing, False

        symbol = event.payload.get("symbol")
        timeframe = event.payload.get("timeframe")
        direction_raw = event.payload.get("direction")
        if not isinstance(symbol, str) or not isinstance(timeframe, str) or direction_raw is None:
            raise ValidationAppError(
                "Approved-plan/fill projection requires symbol, timeframe, and direction.",
                details={"reason": "missing_instrument_identity"},
            )
        direction = (
            direction_raw
            if isinstance(direction_raw, TradeDirection)
            else TradeDirection(str(direction_raw))
        )
        row = JournalTrade(
            organization_id=organization_id,
            user_id=user_id,
            source=JournalTradeSource.PAPER_EXECUTION,
            entry_method=JournalEntryMethod.AUTO,
            status=JournalTradeStatus.PLANNED,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            execution_lifecycle_id=event.execution_lifecycle_id,
            account_id=event.account_id,
            projector_watermark_rank=0,
            projector_lock_version=0,
            tags=[],
            planned_targets=[],
        )
        self._apply_create_fields(row, event.payload)
        self._stamp_lineage_columns(row, event.payload)
        try:
            with self._session.begin_nested():
                self._trades.add(row)
            return row, True
        except IntegrityError as exc:
            if not is_journal_lifecycle_unique_violation(exc):
                raise
            winner = self._trades.find_by_execution_lifecycle(
                organization_id=organization_id,
                execution_lifecycle_id=event.execution_lifecycle_id,
            )
            if winner is None:
                raise
            return winner, False

    def _apply_create_fields(self, row: JournalTrade, payload: dict[str, object]) -> None:
        for key in _CREATE_FIELDS:
            if key not in payload:
                continue
            value = payload[key]
            if key == "planned_targets" and isinstance(value, list):
                value = [
                    PlannedTarget.model_validate(target).model_dump(mode="json") for target in value
                ]
            setattr(row, key, _coerce_field(key, value))

    def _apply_event(self, row: JournalTrade, event: JournalLifecycleEventInput) -> None:
        incoming_rank = _EVENT_RANK.get(event.event_type, 0)
        stale = incoming_rank < int(row.projector_watermark_rank or 0)
        if event.event_type is JournalLifecycleEventType.APPROVED_PLAN:
            self._apply_create_fields(row, event.payload)
        elif event.event_type is JournalLifecycleEventType.FILL:
            self._apply_venue_fields(row, event.payload, fill_nulls_only=stale)
            self._promote_status(row, JournalTradeStatus.OPEN, stale=stale)
            if row.result is TradeResult.OPEN:
                row.result = TradeResult.OPEN
        elif event.event_type is JournalLifecycleEventType.CLOSE:
            # Stale CLOSE after a higher-rank RECONCILE must not regress status.
            self._apply_venue_fields(row, event.payload, fill_nulls_only=stale)
            self._promote_status(row, JournalTradeStatus.CLOSED, stale=stale)
        elif event.event_type is JournalLifecycleEventType.RECONCILE:
            self._apply_venue_fields(row, event.payload, fill_nulls_only=stale)
            self._apply_reconcile_status(row, event.payload, stale=stale)
        if incoming_rank >= int(row.projector_watermark_rank or 0):
            row.projector_watermark_rank = incoming_rank
        row.projector_lock_version = int(row.projector_lock_version or 0) + 1
        self._stamp_lineage_columns(row, event.payload)

    def _stamp_lineage_columns(self, row: JournalTrade, payload: dict[str, object]) -> None:
        """Copy sticky payload.lineage onto query-helper columns. First-seen values win."""
        lineage = extract_lineage_map(payload)
        if not lineage:
            return
        uuid_fields = (
            ("candidate_id", "candidate_id"),
            ("assessment_id", "assessment_id"),
            ("trade_plan_revision_id", "trade_plan_revision_id"),
        )
        for lineage_key, attr in uuid_fields:
            raw = lineage.get(lineage_key)
            if raw is None:
                continue
            incoming = uuid.UUID(raw)
            current = getattr(row, attr)
            if current is not None and current != incoming:
                raise JournalProjectionConflictError(
                    "Journal trade lineage column conflict.",
                    details={"reason": "conflicting_lineage_identity", "key": lineage_key},
                )
            if current is None:
                setattr(row, attr, incoming)
        window = lineage.get("evidence_window_hash")
        if window is None:
            return
        if len(window) != 64:
            raise ValidationAppError(
                "Journal lineage evidence window hash is not 64 hex characters.",
                details={"reason": "invalid_evidence_window_hash"},
            )
        current_hash = row.evidence_window_hash
        if current_hash is not None and current_hash != window:
            raise JournalProjectionConflictError(
                "Journal trade lineage column conflict.",
                details={"reason": "conflicting_lineage_identity", "key": "evidence_window_hash"},
            )
        if current_hash is None:
            row.evidence_window_hash = window

    def _promote_status(
        self, row: JournalTrade, desired: JournalTradeStatus, *, stale: bool
    ) -> None:
        if stale:
            return
        current = _STATUS_RANK.get(row.status, 0)
        target = _STATUS_RANK.get(desired, 0)
        if target >= current:
            row.status = desired
            if desired is JournalTradeStatus.CLOSED and row.candidate_id is not None:
                from app.strategy_brain.records import record_journal_close

                record_journal_close(self._session, row)

    def _apply_reconcile_status(
        self, row: JournalTrade, payload: dict[str, object], *, stale: bool
    ) -> None:
        if stale:
            return
        desired = _authoritative_reconcile_status(payload)
        if desired is None:
            return
        row.status = desired
        if desired is JournalTradeStatus.CLOSED and row.candidate_id is not None:
            from app.strategy_brain.records import record_journal_close

            record_journal_close(self._session, row)

    def _apply_venue_fields(
        self,
        row: JournalTrade,
        payload: dict[str, object],
        *,
        fill_nulls_only: bool,
    ) -> None:
        for key in _VENUE_APPLY_FIELDS:
            if key not in payload:
                continue
            if fill_nulls_only and getattr(row, key, None) is not None:
                continue
            setattr(row, key, _coerce_field(key, payload[key]))


def _coerce_field(key: str, value: object) -> Any:
    if value is None:
        return None
    if key in _MONEY_FIELDS:
        return Decimal(str(value))
    if key in _UUID_FIELDS:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    if key == "direction":
        return value if isinstance(value, TradeDirection) else TradeDirection(str(value))
    if key == "status":
        mapped = _authoritative_reconcile_status({"status": value})
        if mapped is not None:
            return mapped
        return value if isinstance(value, JournalTradeStatus) else JournalTradeStatus(str(value))
    if key == "result":
        return value if isinstance(value, TradeResult) else TradeResult(str(value))
    if key in {"entry_time", "exit_time"} and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _authoritative_reconcile_status(payload: dict[str, object]) -> JournalTradeStatus | None:
    raw = payload.get("reconciliation_position")
    if raw is None:
        raw = payload.get("status")
    if raw is None:
        return None
    token = raw.value if isinstance(raw, JournalReconciliationPosition) else str(raw)
    normalized = token.strip()
    if normalized in {
        JournalReconciliationPosition.POSITION_OPEN.value,
        JournalTradeStatus.OPEN.value,
        "OPEN",
    }:
        return JournalTradeStatus.OPEN
    if normalized in {
        JournalReconciliationPosition.CLOSED.value,
        JournalTradeStatus.CLOSED.value,
        "CLOSED",
    }:
        return JournalTradeStatus.CLOSED
    return None
