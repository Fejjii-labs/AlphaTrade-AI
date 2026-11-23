"""Telegram page when the daily loss budget first blocks a new entry on a UTC day.

The budget is the account's ``max_daily_loss``: realized loss today plus loss
reserved by open entries plus the new entry's planned loss must stay within it.
When an entry is refused for that reason (``daily_loss_limit``) the owner gets
one page per organization per UTC day. ``daily_locked`` stays display only
(AT-ADR-152) and the 10% account equity stop keeps its own kill-switch page.

The page is a default safety-alert code (``TELEGRAM_ALERTS_ENABLED`` does not
gate it) and reuses the existing bot token and chat id. It is sent on the
safety-alert background thread, and its numbers are read in a separate short
session, so the scan never waits on Telegram and never shares a transaction
with this read. A failure is logged and never changes the scan.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import AccountRiskAccountingState
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.safety_alerts import deliver_safety_alert_in_background

logger = structlog.get_logger(__name__)

DAILY_LOSS_LIMIT_REACHED = "daily_loss_limit_reached"
_HEADLINE = "AlphaTrade DEMO: daily loss budget reached."


def _amount(value: Decimal | None, unit: str) -> str:
    if value is None:
        return "unknown"
    return f"{value.quantize(Decimal('0.01'))} {unit}"


def format_daily_loss_limit_text(
    *,
    day: str,
    max_daily_loss: Decimal | None,
    actual_daily_loss: Decimal | None,
    reserved_daily_loss: Decimal | None,
    unit: str = "USDT",
) -> str:
    return "\n".join(
        (
            _HEADLINE,
            f"New entries are blocked for the rest of {day} (UTC).",
            f"Budget: {_amount(max_daily_loss, unit)}",
            f"Realized loss today: {_amount(actual_daily_loss, unit)}",
            f"Reserved by open entries: {_amount(reserved_daily_loss, unit)}",
            "Open positions keep their stops and targets.",
        )
    )


def _budget(
    session_factory: Callable[[], Session], settings: Settings, organization_id: UUID
) -> AccountRiskAccountingState | None:
    if str(organization_id) != settings.governed_blofin_demo_organization_id:
        return None
    account = settings.governed_blofin_demo_account_id
    if not account:
        return None
    with session_factory() as session:
        row = session.scalar(
            select(AccountRiskAccountingState).where(
                AccountRiskAccountingState.organization_id == organization_id,
                AccountRiskAccountingState.account_id == UUID(account),
            )
        )
        if row is not None:
            session.expunge(row)
        session.rollback()
        return row


def page_daily_loss_limit_reached(
    session_factory: Callable[[], Session] | None,
    settings: Settings,
    *,
    organization_id: UUID,
    now: datetime,
) -> bool:
    """Send the once-a-day page in the background. False when skipped or failed."""

    try:
        row = None
        if session_factory is not None:
            try:
                row = _budget(session_factory, settings, organization_id)
            except Exception:
                logger.warning("daily_loss_alert_budget_unreadable", exc_info=True)
        day = now.date().isoformat()
        body = format_daily_loss_limit_text(
            day=day,
            max_daily_loss=None if row is None else row.max_daily_loss,
            actual_daily_loss=None if row is None else row.actual_daily_loss,
            reserved_daily_loss=None if row is None else row.reserved_daily_loss,
            unit="USDT" if row is None else (row.exposure_unit or "USDT"),
        )
        return deliver_safety_alert_in_background(
            settings,
            ExitMonitorAlert(
                code=DAILY_LOSS_LIMIT_REACHED,
                severity="warning",
                message=body,
                fields={
                    "dedup_key": f"{DAILY_LOSS_LIMIT_REACHED}:{organization_id}:{day}",
                    "organization_id": str(organization_id),
                },
                telegram_verbatim=True,
                background=True,
            ),
        )
    except Exception:
        logger.warning("daily_loss_alert_failed", exc_info=True)
        return False
