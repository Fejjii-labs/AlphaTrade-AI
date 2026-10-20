"""Paper-worker heartbeat written each cycle and read outside the process.

The worker publishes one ``paper_worker`` row after every watcher cycle.
``GET /health/paper-worker`` is the check a staging cron or an uptime
monitor can hit. A missing or stale row raises a deduped Telegram alert.
A disarmed worker does not open the database; the check then reports the
heartbeat missing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.disarmed_worker_boot import settings_are_disarmed_paper_worker
from app.db.runtime_status import ControlledRuntimeStatusRow
from app.persistence.runtime_health import project_worker_component
from app.persistence.runtime_status import (
    PAPER_WORKER_COMPONENT,
    RuntimeStatusWrite,
    publish_runtime_status,
)
from app.schemas.health import WorkerComponentObservation
from app.services.arming_gates import _positive_finite
from app.services.paper_exit_monitor import ExitMonitorAlert
from app.services.safety_alerts import deliver_safety_alert, telegram_safety_configured

logger = structlog.get_logger(__name__)

PAPER_WORKER_ALERT_COMPONENT = "paper_worker_alert"
HEARTBEAT_ALERT_COOLDOWN_SECONDS = 900

_MISSING = "worker_heartbeat_missing"
_STALE = "worker_heartbeat_stale"


class HeartbeatAlertLedger(Protocol):
    """Remember the last heartbeat alert so a poll does not send it again."""

    def should_send(self, code: str, now: datetime) -> bool: ...

    def remember(self, code: str, now: datetime) -> None: ...


class MemoryHeartbeatAlertLedger:
    """Process-local ledger. The route also persists one row when it can."""

    def __init__(self, *, cooldown_seconds: int = HEARTBEAT_ALERT_COOLDOWN_SECONDS) -> None:
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._sent: dict[str, datetime] = {}

    def should_send(self, code: str, now: datetime) -> bool:
        previous = self._sent.get(code)
        if previous is None:
            return True
        moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        return moment - previous >= self._cooldown

    def remember(self, code: str, now: datetime) -> None:
        self._sent[code] = now if now.tzinfo is not None else now.replace(tzinfo=UTC)

    def clear(self) -> None:
        self._sent.clear()


def paper_worker_stale_after_seconds(settings: object) -> int:
    """Freshness window: at least three poll intervals, and the watcher floor."""

    base = _positive_finite(getattr(settings, "watcher_heartbeat_stale_after_seconds", None))
    poll = _positive_finite(getattr(settings, "watcher_paper_poll_interval_seconds", None))
    floor = int(base) if base is not None else 90
    scaled = floor if poll is None else max(floor, int(poll * 3) + 1)
    return max(5, min(scaled, 3600))


def publish_paper_worker_heartbeat(
    settings: Settings,
    *,
    now: datetime,
    status: str,
    error: str = "",
    session_factory: sessionmaker[Session] | None = None,
) -> None:
    """Upsert the worker heartbeat. A disarmed worker returns before any session."""

    if settings_are_disarmed_paper_worker(settings):
        return
    try:
        factory = session_factory or _session_factory()
        publish_runtime_status(
            factory,
            RuntimeStatusWrite(
                component=PAPER_WORKER_COMPONENT,
                worker_id=settings.watcher_paper_worker_id[:80],
                heartbeat_at=now,
                activation_state=(status or "running")[:32],
                last_error_code=error[:64],
                market_source="paper_worker",
            ),
        )
    except Exception as exc:
        if type(exc) is AssertionError:
            raise
        logger.warning("paper_worker_heartbeat_unpublished")


def watch_cycle_with_heartbeat[CycleT](
    settings: Settings,
    cycle: Callable[[], CycleT],
    *,
    now: Callable[[], datetime] | None = None,
) -> Callable[[], CycleT]:
    """Publish a heartbeat around one worker cycle, including a failed cycle."""

    clock = now or (lambda: datetime.now(UTC))

    def wrapped() -> CycleT:
        publish_paper_worker_heartbeat(settings, now=clock(), status="running", error="")
        try:
            outcome = cycle()
        except Exception:
            publish_paper_worker_heartbeat(
                settings,
                now=clock(),
                status="failed",
                error="paper_worker_cycle_failed",
            )
            raise
        publish_paper_worker_heartbeat(
            settings,
            now=clock(),
            status=str(getattr(outcome, "status", "")),
            error=str(getattr(outcome, "error", "")),
        )
        return outcome

    return wrapped


def project_paper_worker_heartbeat(
    row: object | None,
    *,
    now: datetime,
    stale_after_seconds: int,
) -> WorkerComponentObservation:
    """Same freshness rule as the Watcher row. A missing row is unavailable."""

    return project_worker_component(row, now=now, stale_after_seconds=stale_after_seconds)


def heartbeat_status(
    observation: WorkerComponentObservation,
) -> Literal["fresh", "stale", "missing"]:
    """``fresh``, ``stale``, or ``missing`` for the external check."""

    if observation.health_state == "RUNNING":
        return "fresh"
    if observation.heartbeat_at is None or observation.health_state == "UNAVAILABLE":
        return "missing"
    return "stale"


def heartbeat_alert(observation: WorkerComponentObservation) -> ExitMonitorAlert | None:
    """Alert for a heartbeat that is not fresh. None when the worker is current."""

    status = heartbeat_status(observation)
    if status == "fresh":
        return None
    code = _MISSING if status == "missing" else _STALE
    if status == "missing":
        message = (
            "Paper worker heartbeat is missing. The external check did not see a fresh heartbeat."
        )
    else:
        message = (
            "Paper worker heartbeat is stale. "
            "The external check saw a heartbeat outside the freshness window."
        )
    age = observation.heartbeat_age_seconds
    return ExitMonitorAlert(
        code=code,
        severity="critical",
        message=message,
        fields={
            "component": PAPER_WORKER_COMPONENT,
            "health_state": observation.health_state,
            "heartbeat_age_seconds": "" if age is None else f"{age:.3f}",
            "threshold_seconds": str(observation.heartbeat_stale_after_seconds),
            "refusal": "heartbeat",
            "dedup_key": code,
        },
    )


def raise_heartbeat_alert(
    settings: Settings,
    observation: WorkerComponentObservation,
    *,
    now: datetime,
    ledger: HeartbeatAlertLedger,
) -> bool:
    """Send the heartbeat alert once per cooldown. False when nothing is sent."""

    alert = heartbeat_alert(observation)
    if alert is None:
        return False
    if not ledger.should_send(alert.code, now):
        return False
    if not telegram_safety_configured(settings):
        logger.warning("safety_alert_telegram_unconfigured", code=alert.code)
        ledger.remember(alert.code, now)
        return False
    sent = deliver_safety_alert(settings, alert, now=now)
    if sent:
        ledger.remember(alert.code, now)
    return sent


class RuntimeStatusHeartbeatLedger:
    """Dedupe heartbeat alerts in the runtime-status table, then in memory."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        cooldown_seconds: int = HEARTBEAT_ALERT_COOLDOWN_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._memory = MemoryHeartbeatAlertLedger(cooldown_seconds=cooldown_seconds)

    def should_send(self, code: str, now: datetime) -> bool:
        if not self._memory.should_send(code, now):
            return False
        try:
            with self._session_factory() as session:
                row = session.get(ControlledRuntimeStatusRow, PAPER_WORKER_ALERT_COMPONENT)
        except Exception:
            return True
        if row is None or row.last_error_code != code or row.last_delivery_at is None:
            return True
        last = row.last_delivery_at
        if last.tzinfo is None:
            return True
        moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
        return moment - last.astimezone(UTC) >= self._cooldown

    def remember(self, code: str, now: datetime) -> None:
        self._memory.remember(code, now)
        try:
            publish_runtime_status(
                self._session_factory,
                RuntimeStatusWrite(
                    component=PAPER_WORKER_ALERT_COMPONENT,
                    worker_id="health-check",
                    heartbeat_at=now,
                    activation_state="alerted",
                    last_error_code=code[:64],
                    last_delivery_at=now,
                    market_source="paper_worker",
                ),
            )
        except Exception:
            logger.warning("paper_worker_heartbeat_alert_unrecorded", code=code)


def load_paper_worker_row(session: Session) -> object | None:
    """The worker heartbeat row, or None when this process has not written one."""

    from app.persistence.runtime_status import load_runtime_rows

    return load_runtime_rows(session).get(PAPER_WORKER_COMPONENT)


def _session_factory() -> sessionmaker[Session]:
    from app.db.session import get_session_factory

    return get_session_factory()
