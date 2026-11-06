"""Alert on a paper-exit loss the live breakers cannot cover.

The close stays journaled. The live consecutive-loss streak and the daily
lock are not rewritten from a backfill or from a stale bar clock. A paper
alert and an audit row record the loss. A single loss does not arm the kill
switch (AT-ADR-152); the account daily equity stop is the only loss rule
that does. This module does not place an order.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import structlog
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    ActorType,
    AuditEventType,
    AuditResult,
    AuditSeverity,
    PaperAlertSeverity,
    PaperAlertSource,
    PaperAlertType,
)
from app.schemas.execution_protocol import ClosePaperPlanRequest
from app.services.audit_service import AuditService
from app.services.paper_alert_service import PaperAlertService
from app.services.paper_exit_monitor import ExitMonitorAlert

logger = structlog.get_logger(__name__)

_ALERT_TYPES = {
    "backfill_loss": PaperAlertType.PAPER_EXIT_BACKFILL_LOSS,
    "stale_live_loss": PaperAlertType.PAPER_EXIT_STALE_LOSS,
    "degraded_repeat": PaperAlertType.PAPER_EXIT_DEGRADED_REPEAT,
    "clock_skew": PaperAlertType.PAPER_EXIT_CLOCK_SKEW,
    "clock_untrusted": PaperAlertType.PAPER_EXIT_CLOCK_SKEW,
}


def build_backfill_loss_alert(request: ClosePaperPlanRequest) -> ExitMonitorAlert:
    """Structured alert for one losing backfill. The PnL amount is not included."""

    return ExitMonitorAlert(
        code="backfill_loss",
        severity="critical",
        message=(
            "A historical backfill close recorded a net loss. "
            "The close is journaled. The account daily equity stop still applies."
        ),
        fields={
            "exit_origin": "backfill",
            "net_loss": "true",
            "command_id": str(request.command_id),
            "organization_id": str(request.organization_id),
            "account_id": str(request.account_id),
            "refusal": "none",
            "dedup_key": f"backfill_loss:{request.organization_id}:{request.command_id}",
        },
    )


def build_stale_live_loss_alert(request: ClosePaperPlanRequest) -> ExitMonitorAlert:
    """Structured alert for one stale live loss. The PnL amount is not included."""

    return ExitMonitorAlert(
        code="stale_live_loss",
        severity="critical",
        message=(
            "A losing close is older than the loss pause or one candle, "
            "and younger than a historical backfill. "
            "The close is journaled. The account daily equity stop still applies."
        ),
        fields={
            "exit_origin": "live",
            "net_loss": "true",
            "command_id": str(request.command_id),
            "organization_id": str(request.organization_id),
            "account_id": str(request.account_id),
            "refusal": "none",
            "dedup_key": f"stale_live_loss:{request.organization_id}:{request.command_id}",
        },
    )


def alert_backfill_loss(
    session: Session,
    *,
    request: ClosePaperPlanRequest,
    net_pnl: Decimal,
    settings: Settings,
) -> ExitMonitorAlert:
    """Alert one losing backfill. Does not arm the kill switch or move the streak.

    ``net_pnl`` is accepted so the caller has already required a real loss.
    The amount is not stored on the alert. The alert shares the caller's
    transaction.
    """

    if not isinstance(net_pnl, Decimal):
        raise TypeError("backfill loss alert requires a decimal net pnl")
    return _alert(
        session, request=request, alert=build_backfill_loss_alert(request), settings=settings
    )


def alert_stale_live_loss(
    session: Session,
    *,
    request: ClosePaperPlanRequest,
    net_pnl: Decimal,
    settings: Settings,
) -> ExitMonitorAlert:
    """Alert one stale live loss. Does not arm the kill switch or move the streak."""

    if not isinstance(net_pnl, Decimal):
        raise TypeError("stale live loss alert requires a decimal net pnl")
    return _alert(
        session, request=request, alert=build_stale_live_loss_alert(request), settings=settings
    )


def _alert(
    session: Session,
    *,
    request: ClosePaperPlanRequest,
    alert: ExitMonitorAlert,
    settings: Settings,
) -> ExitMonitorAlert:
    record_exit_monitor_alert(
        session,
        alert,
        organization_id=request.organization_id,
        user_id=request.user_id,
        settings=settings,
    )
    logger.warning("paper_exit_monitor_alert", **alert.as_log())
    return alert


def record_exit_monitor_alert(
    session: Session,
    alert: ExitMonitorAlert,
    *,
    organization_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    settings: Settings | None = None,
) -> bool:
    """Persist one structured alert. Returns False when dedup suppresses it.

    A paper-alert row needs an organization. An audit row is written for the
    same event, including when no organization is known. A suppressed
    duplicate does not write a second audit row.
    """

    created = True
    if organization_id is not None:
        row = PaperAlertService(session).create(
            organization_id=organization_id,
            user_id=user_id,
            alert_type=_ALERT_TYPES.get(alert.code, PaperAlertType.PAPER_EXIT_DEGRADED_REPEAT),
            severity=_severity(alert.severity),
            message=alert.message,
            metadata=dict(alert.fields),
            dedup_key=alert.fields.get("dedup_key"),
            source=PaperAlertSource.PAPER_EXIT_MONITOR,
        )
        created = row is not None
    if organization_id is not None and not created:
        return False
    request_id = uuid.uuid4().hex
    AuditService(session).record(
        AuditRecordCreate(
            request_id=request_id,
            trace_id=request_id,
            event_type=AuditEventType.RISK_BLOCK,
            resource_type="paper_exit_monitor",
            actor_type=ActorType.SYSTEM,
            result=AuditResult.BLOCKED,
            severity=AuditSeverity.HIGH if alert.severity == "critical" else AuditSeverity.MEDIUM,
            user_id=user_id,
            organization_id=organization_id,
            resource_id=alert.fields.get("command_id"),
            metadata=alert.as_log(),
        )
    )
    if settings is not None:
        from app.services.safety_alerts import queue_safety_alert

        queue_safety_alert(session, settings, alert)
    return True


def _severity(value: str) -> PaperAlertSeverity:
    if value == "critical":
        return PaperAlertSeverity.CRITICAL
    if value == "warning":
        return PaperAlertSeverity.WARNING
    return PaperAlertSeverity.INFO
