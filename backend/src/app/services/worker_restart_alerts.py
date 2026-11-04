"""Telegram ops alerts for paper worker restarts that logs alone would hide.

Both use the existing safety-alert path (``deliver_safety_alert``), so they
reuse ``telegram_bot_token`` and ``telegram_chat_id`` and add no secret. The
text carries counts and times only.

* ``worker_memory_watchdog_repeat``: the memory watchdog tripped at least
  ``PAPER_WORKER_MEMORY_WATCHDOG_ALERT_AFTER_TRIPS`` times in a row (0 turns
  it off). Sent once per trip, just before the restart exit. The watchdog's
  growing grace spaces these out.
* ``worker_unclean_exit``: the previous run's "running" marker was still
  there at boot, so that run ended without a clean stop (platform OOM kill,
  SIGKILL, or a crash).

Neither raises: an alert failure must not stop or crash the worker.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from app.core.config import Settings
from app.observability.memory_watchdog import MemoryWatchdog
from app.observability.run_marker import UncleanExit

logger = structlog.get_logger("services.worker_restart_alerts")

COMPONENT = "paper_worker"


def alert_memory_watchdog_repeat(
    settings: Settings, watchdog: MemoryWatchdog | None, *, now: datetime | None = None
) -> bool:
    """Page when a tripped watchdog's streak reaches the configured count."""

    threshold = settings.paper_worker_memory_watchdog_alert_after_trips
    if watchdog is None or not watchdog.tripped or threshold <= 0:
        return False
    trips = watchdog.consecutive_trips
    if trips < threshold:
        return False
    from app.services.paper_exit_monitor import ExitMonitorAlert
    from app.services.safety_alerts import deliver_safety_alert

    alert = ExitMonitorAlert(
        code="worker_memory_watchdog_repeat",
        severity="critical",
        message=(
            f"Paper worker memory watchdog tripped {trips} times in a row and is "
            "restarting the worker again. Its baseline memory may be at the limit, "
            "or it is leaking."
        ),
        fields={
            "component": COMPONENT,
            "consecutive_trips": str(trips),
            "grace_seconds": str(round(watchdog.grace_seconds)),
            "dedup_key": f"worker_memory_watchdog_repeat:{trips}",
        },
    )
    try:
        return deliver_safety_alert(settings, alert, now=now or datetime.now(UTC))
    except Exception as exc:
        logger.error("worker_restart_alert_failed", code=alert.code, error_type=type(exc).__name__)
        return False


def alert_unclean_previous_exit(
    settings: Settings, previous: UncleanExit | None, *, now: datetime | None = None
) -> bool:
    """Log and page a previous run that ended without a clean stop."""

    if previous is None:
        return False
    moment = now or datetime.now(UTC)
    since: int | None = None
    if previous.started_at is not None:
        since = max(round(moment.timestamp() - previous.started_at), 0)
    logger.error(
        "paper_worker_previous_exit_unclean",
        previous_run_seconds_since_start=since,
        likely_causes="oom_kill,sigkill,crash",
    )
    from app.services.paper_exit_monitor import ExitMonitorAlert
    from app.services.safety_alerts import deliver_safety_alert

    alert = ExitMonitorAlert(
        code="worker_unclean_exit",
        severity="critical",
        message=(
            "The previous paper worker run ended without a clean stop: likely a "
            "platform out-of-memory kill, a SIGKILL after the shutdown deadline, or "
            "a crash. Check the platform events and the last worker logs."
        ),
        fields={
            "component": COMPONENT,
            "elapsed_seconds": "unknown" if since is None else str(since),
            "dedup_key": "worker_unclean_exit",
        },
    )
    try:
        return deliver_safety_alert(settings, alert, now=moment)
    except Exception as exc:
        logger.error("worker_restart_alert_failed", code=alert.code, error_type=type(exc).__name__)
        return False
