"""Telegram page the first time a UTC day's loss budget refuses a new entry.

The risk gate refuses an entry when its planned loss is larger than what remains
of the account's daily loss budget after realized and reserved loss:
``daily_loss_limit`` in the demo pre-check (budget ``max_daily_loss``), or
``insufficient_daily_loss_allocation`` in the risk-model-v2 check (budget
``equity * daily_stop_pct``). Only BloFin demo refusals page; internal paper
refusals of the same budget do not. The owner gets one page per organization per
UTC day. The page cannot know whether any budget remains (realized loss may
already have used all of it), so it claims neither that trading stopped nor that
smaller entries will pass. ``daily_locked`` stays display only (AT-ADR-152) and
the 10% account equity stop keeps its own kill-switch page.

Like every loss-protection page it carries no P&L amount. It is a default
safety-alert code (``TELEGRAM_ALERTS_ENABLED`` does not gate it) that reuses
the existing bot token and chat id. It reports an observed refusal, so it is
queued on the safety-alert background thread without waiting for the scan's
commit; the scan never waits on Telegram and a failure never changes it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import structlog

from app.core.config import Settings
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.safety_alerts import _aware, deliver_safety_alert_in_background

logger = structlog.get_logger(__name__)

DAILY_LOSS_LIMIT_REACHED = "daily_loss_limit_reached"
DAILY_LOSS_REFUSAL_REASONS = frozenset({"daily_loss_limit", "insufficient_daily_loss_allocation"})


def format_daily_loss_limit_text(*, day: str) -> str:
    return "\n".join(
        (
            "AlphaTrade DEMO: the daily loss budget refused a new entry.",
            f"Its planned loss was larger than what remains of the {day} (UTC) budget.",
            "A smaller entry may still pass if any budget remains.",
            "Open positions keep their stops and targets. Sent once per UTC day.",
        )
    )


def page_daily_loss_limit_reached(
    settings: Settings, *, organization_id: UUID, now: datetime
) -> bool:
    """Queue the once-a-day page in the background. False when skipped or failed."""

    try:
        day = _aware(now).astimezone(UTC).date().isoformat()
        return deliver_safety_alert_in_background(
            settings,
            ExitMonitorAlert(
                code=DAILY_LOSS_LIMIT_REACHED,
                severity="warning",
                message=format_daily_loss_limit_text(day=day),
                fields={
                    "dedup_key": f"{DAILY_LOSS_LIMIT_REACHED}:{organization_id}:{day}",
                    "organization_id": str(organization_id),
                },
                telegram_verbatim=True,
                background=True,
            ),
            now=now,
        )
    except Exception:
        logger.warning("daily_loss_alert_failed", exc_info=True)
        return False
