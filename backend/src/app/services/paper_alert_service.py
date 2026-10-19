"""Paper validation alert storage (Slice 40 — no delivery)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationAppError
from app.db.models import PaperValidationAlert as AlertModel
from app.guardrails.redaction import redact_mapping
from app.repositories.paper_scheduler import PaperAlertRepository
from app.schemas.alerts import (
    PaginatedPaperAlerts,
    PaginatedSetupAlertReview,
    PaperAlert,
    PaperAlertSummary,
    SetupAlertReviewItem,
    SetupAlertReviewSummary,
    SetupAlertReviewSummaryItem,
    SetupAlertReviewUpdate,
)
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    ActorType,
    AuditEventType,
    AuditResult,
    AuditSeverity,
    PaperAlertSeverity,
    PaperAlertSource,
    PaperAlertType,
    SetupAlertReviewStatus,
)
from app.services.audit_service import AuditService

if TYPE_CHECKING:
    from app.services.alert_delivery_service import AlertDeliveryService

# Cooldown seconds per alert type — suppress duplicate notifications within the window.
ALERT_COOLDOWN_SECONDS: dict[PaperAlertType, int] = {
    PaperAlertType.SETUP_SIGNAL_DETECTED: 3600,
    PaperAlertType.PAPER_TRADE_OPENED: 1800,
    PaperAlertType.PAPER_TRADE_CLOSED: 86400,
    PaperAlertType.STOP_HIT: 86400,
    PaperAlertType.TP_HIT: 86400,
    PaperAlertType.RUNNER_EXIT: 86400,
    PaperAlertType.DATA_STALE: 3600,
    PaperAlertType.STRATEGY_BLOCKED: 3600,
    PaperAlertType.OVERTRADING_WARNING: 86400,
    PaperAlertType.DAILY_LOSS_LOCK_WARNING: 86400,
    PaperAlertType.PROMOTION_STATUS_CHANGED: 3600,
    PaperAlertType.RESEARCH_VALIDATION_PROMOTION: 0,
    PaperAlertType.DEMO_LIQUIDATION_THROUGH_STOP: 3600,
}


def build_alert_dedup_key(
    *,
    alert_type: PaperAlertType,
    organization_id: uuid.UUID,
    paper_validation_run_id: uuid.UUID | None = None,
    paper_trade_id: uuid.UUID | None = None,
    strategy_id: uuid.UUID | None = None,
) -> str:
    parts = [alert_type.value, str(organization_id)]
    if paper_validation_run_id is not None:
        parts.append(str(paper_validation_run_id))
    if paper_trade_id is not None:
        parts.append(str(paper_trade_id))
    elif strategy_id is not None:
        parts.append(str(strategy_id))
    return ":".join(parts)


class PaperAlertService:
    def __init__(
        self,
        session: Session,
        *,
        audit_service: AuditService | None = None,
        delivery_service: AlertDeliveryService | None = None,
    ) -> None:
        self._session = session
        self._alerts = PaperAlertRepository(session)
        self._audit = audit_service or AuditService(session)
        self._delivery = delivery_service

    def create(
        self,
        *,
        organization_id: uuid.UUID,
        alert_type: PaperAlertType,
        message: str,
        severity: PaperAlertSeverity = PaperAlertSeverity.INFO,
        user_id: uuid.UUID | None = None,
        strategy_id: uuid.UUID | None = None,
        paper_validation_run_id: uuid.UUID | None = None,
        paper_trade_id: uuid.UUID | None = None,
        metadata: dict | None = None,
        dedup_key: str | None = None,
        skip_dedup: bool = False,
        source: PaperAlertSource = PaperAlertSource.PAPER_VALIDATION_RUNTIME,
    ) -> PaperAlert | None:
        """Create an alert, returning None when deduplication suppresses a duplicate."""
        meta = dict(metadata or {})
        meta.setdefault("source", source.value)
        key = dedup_key or build_alert_dedup_key(
            alert_type=alert_type,
            organization_id=organization_id,
            paper_validation_run_id=paper_validation_run_id,
            paper_trade_id=paper_trade_id,
            strategy_id=strategy_id,
        )
        if not skip_dedup:
            cooldown = ALERT_COOLDOWN_SECONDS.get(alert_type, 3600)
            since = datetime.now(UTC) - timedelta(seconds=cooldown)
            existing = self._alerts.find_recent_by_dedup_key(organization_id, key, since=since)
            if existing is not None:
                return None

        row = AlertModel(
            organization_id=organization_id,
            user_id=user_id,
            alert_type=alert_type,
            severity=severity,
            strategy_id=strategy_id,
            paper_validation_run_id=paper_validation_run_id,
            paper_trade_id=paper_trade_id,
            message=message,
            metadata_json=meta,
            dedup_key=key,
        )
        delivery = self._delivery
        if delivery is None:
            from app.services.alert_delivery_service import AlertDeliveryService

            delivery = AlertDeliveryService(self._session)
        delivery.initialize_delivery_fields(row, organization_id=organization_id, user_id=user_id)
        self._alerts.add(row)
        return self._to_schema(row)

    def list_alerts(
        self,
        organization_id: uuid.UUID,
        *,
        alert_type: PaperAlertType | None = None,
        severity: PaperAlertSeverity | None = None,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedPaperAlerts:
        rows, total = self._alerts.list_for_org(
            organization_id,
            alert_type=alert_type,
            severity=severity,
            unread_only=unread_only,
            limit=limit,
            offset=offset,
        )
        return PaginatedPaperAlerts(
            items=[self._to_schema(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_alert(self, alert_id: uuid.UUID, *, organization_id: uuid.UUID) -> PaperAlert:
        from sqlalchemy import select

        row = self._session.scalar(
            select(AlertModel).where(
                AlertModel.id == alert_id,
                AlertModel.organization_id == organization_id,
            )
        )
        if row is None:
            raise NotFoundError("Alert not found.")
        return self._to_schema(row)

    def mark_read(
        self,
        alert_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> PaperAlert:
        row = self._alerts.mark_read(alert_id, organization_id=organization_id)
        if row is None:
            raise NotFoundError("Alert not found.")
        self._audit.record(
            AuditRecordCreate(
                request_id=f"alert-read-{alert_id}",
                trace_id=str(uuid.uuid4()),
                user_id=user_id,
                organization_id=organization_id,
                event_type=AuditEventType.PAPER_VALIDATION_RUNTIME,
                resource_type="paper_validation_alert",
                resource_id=str(alert_id),
                actor_type=ActorType.USER,
                result=AuditResult.SUCCESS,
                severity=AuditSeverity.INFO,
                metadata={"action": "alert_read"},
            )
        )
        return self._to_schema(row)

    def mark_all_read(self, organization_id: uuid.UUID, *, user_id: uuid.UUID | None = None) -> int:
        count = self._alerts.mark_all_read(organization_id)
        if count > 0:
            self._audit.record(
                AuditRecordCreate(
                    request_id=f"alert-read-all-{organization_id}",
                    trace_id=str(uuid.uuid4()),
                    user_id=user_id,
                    organization_id=organization_id,
                    event_type=AuditEventType.PAPER_VALIDATION_RUNTIME,
                    resource_type="paper_validation_alert",
                    resource_id=str(organization_id),
                    actor_type=ActorType.USER,
                    result=AuditResult.SUCCESS,
                    severity=AuditSeverity.INFO,
                    metadata={"action": "alert_read_all", "marked_read": count},
                )
            )
        return count

    def summary(self, organization_id: uuid.UUID) -> PaperAlertSummary:
        rows, total = self._alerts.list_for_org(organization_id, limit=500)
        unread = self._alerts.count_unread(organization_id)
        by_type: dict[str, int] = {}
        by_severity: dict[str, int] = {}
        for row in rows:
            by_type[row.alert_type.value] = by_type.get(row.alert_type.value, 0) + 1
            by_severity[row.severity.value] = by_severity.get(row.severity.value, 0) + 1
        return PaperAlertSummary(
            total=total,
            unread=unread,
            by_type=by_type,
            by_severity=by_severity,
        )

    def list_setup_review(
        self,
        organization_id: uuid.UUID,
        *,
        review_status: SetupAlertReviewStatus | None = None,
        condition: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        direction: str | None = None,
        min_confidence: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> PaginatedSetupAlertReview:
        rows, total = self._alerts.list_setup_review(
            organization_id,
            review_status=review_status,
            condition=condition,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            min_confidence=min_confidence,
            limit=limit,
            offset=offset,
        )
        return PaginatedSetupAlertReview(
            items=[self._to_setup_review_item(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def setup_review_summary(self, organization_id: uuid.UUID) -> SetupAlertReviewSummary:
        status_counts = self._alerts.count_setup_review_by_status(organization_id)
        rows = self._alerts.list_setup_review_for_summary(organization_id)
        by_condition: dict[str, int] = {}
        by_symbol: dict[str, int] = {}
        latest_created_at = rows[0].created_at if rows else None
        ranked: list[tuple[float, AlertModel]] = []
        for row in rows:
            meta = row.metadata_json or {}
            condition = meta.get("condition")
            symbol = meta.get("symbol")
            if isinstance(condition, str):
                by_condition[condition] = by_condition.get(condition, 0) + 1
            if isinstance(symbol, str):
                by_symbol[symbol] = by_symbol.get(symbol, 0) + 1
            confidence = meta.get("confidence")
            if isinstance(confidence, (int, float)):
                ranked.append((float(confidence), row))
        ranked.sort(key=lambda item: (-item[0], item[1].created_at), reverse=False)
        highest = [
            SetupAlertReviewSummaryItem(
                alert_id=row.id,
                symbol=(row.metadata_json or {}).get("symbol"),
                condition=(row.metadata_json or {}).get("condition"),
                confidence=float(confidence),
                created_at=row.created_at,
            )
            for confidence, row in ranked[:5]
        ]
        return SetupAlertReviewSummary(
            total_unreviewed=status_counts.get(SetupAlertReviewStatus.UNREVIEWED.value, 0),
            total_watching=status_counts.get(SetupAlertReviewStatus.WATCHING.value, 0),
            total_important=status_counts.get(SetupAlertReviewStatus.IMPORTANT.value, 0),
            total_ignored=status_counts.get(SetupAlertReviewStatus.IGNORED.value, 0),
            by_condition=by_condition,
            by_symbol=by_symbol,
            latest_created_at=latest_created_at,
            highest_confidence_alerts=highest,
        )

    def update_setup_review(
        self,
        alert_id: uuid.UUID,
        payload: SetupAlertReviewUpdate,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> SetupAlertReviewItem:
        from sqlalchemy import select

        row = self._alerts.get_setup_review_alert(alert_id, organization_id=organization_id)
        if row is None:
            generic = self._session.scalar(
                select(AlertModel).where(
                    AlertModel.id == alert_id,
                    AlertModel.organization_id == organization_id,
                )
            )
            if generic is not None:
                raise ValidationAppError(
                    "Only scanner-created setup alerts can be reviewed.",
                    details={"alert_id": str(alert_id)},
                )
            raise NotFoundError("Alert not found.")
        row.review_status = payload.review_status.value
        row.review_notes = payload.review_notes
        row.reviewed_at = datetime.now(UTC)
        row.reviewed_by = user_id
        self._audit.record(
            AuditRecordCreate(
                request_id=f"setup-alert-review-{alert_id}",
                trace_id=str(uuid.uuid4()),
                user_id=user_id,
                organization_id=organization_id,
                event_type=AuditEventType.PAPER_VALIDATION_RUNTIME,
                resource_type="paper_validation_alert",
                resource_id=str(alert_id),
                actor_type=ActorType.USER,
                result=AuditResult.SUCCESS,
                severity=AuditSeverity.INFO,
                metadata={
                    "action": "setup_alert_review",
                    "review_status": payload.review_status.value,
                    "has_notes": bool(payload.review_notes),
                },
            )
        )
        return self._to_setup_review_item(row)

    @staticmethod
    def _to_setup_review_item(row: AlertModel) -> SetupAlertReviewItem:
        meta = row.metadata_json or {}
        metrics = meta.get("metrics") if isinstance(meta.get("metrics"), dict) else {}
        latest_price = metrics.get("latest_price")
        if latest_price is None:
            latest_price = meta.get("latest_price")
        confidence = meta.get("confidence")
        trigger_level = meta.get("trigger_level")
        invalidation_level = meta.get("invalidation_level")
        try:
            review_status = SetupAlertReviewStatus(row.review_status)
        except ValueError:
            review_status = SetupAlertReviewStatus.UNREVIEWED
        return SetupAlertReviewItem(
            alert_id=row.id,
            created_at=row.created_at,
            symbol=meta.get("symbol"),
            timeframe=meta.get("timeframe"),
            condition=meta.get("condition"),
            direction=meta.get("direction"),
            confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            reason=meta.get("reason"),
            trigger_level=float(trigger_level) if isinstance(trigger_level, (int, float)) else None,
            invalidation_level=(
                float(invalidation_level) if isinstance(invalidation_level, (int, float)) else None
            ),
            latest_price=float(latest_price) if isinstance(latest_price, (int, float)) else None,
            delivery_channel=row.delivery_channel,
            delivery_status=row.delivery_status,
            dedupe_key=row.dedup_key,
            review_status=review_status,
            review_notes=row.review_notes,
            reviewed_at=row.reviewed_at,
            reviewed_by=row.reviewed_by,
            metadata=redact_mapping(meta) if meta else None,
        )

    @staticmethod
    def _to_schema(row: AlertModel) -> PaperAlert:
        meta = row.metadata_json or {}
        raw_source = meta.get("source", PaperAlertSource.PAPER_VALIDATION_RUNTIME.value)
        try:
            alert_source = PaperAlertSource(raw_source)
        except ValueError:
            alert_source = PaperAlertSource.PAPER_VALIDATION_RUNTIME
        skipped_reason = meta.get("delivery_skipped_reason")
        max_retries = 2
        retry_exhausted = (
            row.delivery_status.value == "failed"
            and row.next_retry_at is None
            and row.delivery_attempts > max_retries
        )
        return PaperAlert(
            id=row.id,
            organization_id=row.organization_id,
            user_id=row.user_id,
            alert_type=row.alert_type,
            severity=row.severity,
            strategy_id=row.strategy_id,
            paper_validation_run_id=row.paper_validation_run_id,
            paper_trade_id=row.paper_trade_id,
            message=row.message,
            read_at=row.read_at,
            metadata=row.metadata_json,
            alert_source=alert_source,
            delivery_status=row.delivery_status,
            delivery_channel=row.delivery_channel,
            delivery_attempts=row.delivery_attempts,
            last_delivery_error=row.last_delivery_error,
            delivered_at=row.delivered_at,
            next_retry_at=row.next_retry_at,
            delivery_skipped_reason=skipped_reason,
            retry_exhausted=retry_exhausted,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def alert_type_for_exit(exit_reason: str | None) -> PaperAlertType:
        if not exit_reason:
            return PaperAlertType.PAPER_TRADE_CLOSED
        lowered = exit_reason.lower()
        if "stop" in lowered:
            return PaperAlertType.STOP_HIT
        if "take_profit" in lowered or lowered.startswith("tp"):
            return PaperAlertType.TP_HIT
        if "runner" in lowered:
            return PaperAlertType.RUNNER_EXIT
        return PaperAlertType.PAPER_TRADE_CLOSED
