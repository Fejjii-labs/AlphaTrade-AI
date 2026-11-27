"""Database storage alert: Telegram at 80% and 90% of the configured disk.

Attached to the paper worker only when ``DATABASE_DISK_LIMIT_MB`` is set (the
Render database's disk). Every check reads ``pg_database_size`` (read-only) and
sends ``database_storage_high`` through the safety-alert path; the alert's
cooldown (6 hours per level) keeps it from repeating. Nothing is deleted here:
the daily retention pass (``RETENTION_ENABLED``) prunes scan data; trades are kept.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings

if TYPE_CHECKING:
    from app.workers.paper_worker import CycleOutcome

logger = structlog.get_logger(__name__)
DATABASE_STORAGE_HIGH = "database_storage_high"
LEVELS = (90, 80)  # percent, highest first


def storage_level(size_bytes: int, limit_mb: int) -> int | None:
    """The highest alert level reached, or None below 80%."""

    percent = 100 * size_bytes / (limit_mb * 1_000_000)
    return next((level for level in LEVELS if percent >= level), None)


def storage_message(size_bytes: int, limit_mb: int) -> str:
    used = size_bytes / 1_000_000
    percent = 100 * used / limit_mb
    icon = "\U0001f534" if percent >= 90 else "\U0001f7e0"
    return "\n".join(
        [
            f"{icon} Database storage at {percent:.0f}% ({used:,.0f} MB of {limit_mb:,} MB)",
            "Old scan data is cleaned daily; trades, orders and the Journal are kept.",
            "If it keeps rising: a bigger disk, or a shorter scan-data window.",
        ]
    )


class DbStorageCheck:
    def __init__(
        self,
        settings: Settings,
        *,
        session_factory: Callable[[], Session] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        send: Callable[..., bool] | None = None,
    ) -> None:
        self.settings = settings
        self._session_factory = session_factory
        self._now = now
        self._send = send
        self._stop = threading.Event()

    def __call__(self) -> CycleOutcome:
        from app.workers.paper_worker import CycleOutcome

        limit = self.settings.database_disk_limit_mb
        if self._stop.is_set() or not limit:
            return CycleOutcome("disabled")
        try:
            factory = self._session_factory
            if factory is None:
                from app.db.session import get_session_factory

                factory = get_session_factory()
            with factory() as session:
                if session.get_bind().dialect.name != "postgresql":
                    return CycleOutcome("unsupported_database")
                size = int(
                    session.execute(
                        text("SELECT pg_database_size(current_database())")
                    ).scalar_one()
                )
                session.rollback()
        except Exception as exc:
            logger.warning("db_storage_check_failed", error=type(exc).__name__)
            return CycleOutcome("failed")
        level = storage_level(size, limit)
        logger.info("db_storage_check", size_mb=round(size / 1_000_000, 1), limit_mb=limit)
        if level is None:
            return CycleOutcome("ok")
        from app.services.paper_exit_monitor import ExitMonitorAlert
        from app.services.safety_alerts import deliver_safety_alert

        alert = ExitMonitorAlert(
            code=DATABASE_STORAGE_HIGH,
            severity="critical" if level >= 90 else "warning",
            message=storage_message(size, limit),
            fields={"dedup_key": f"{DATABASE_STORAGE_HIGH}:{level}"},
            telegram_verbatim=True,
        )
        sent = (self._send or deliver_safety_alert)(self.settings, alert, now=self._now())
        return CycleOutcome(f"alert_{level}", delivered=1 if sent else 0)

    def request_stop(self) -> None:
        self._stop.set()
