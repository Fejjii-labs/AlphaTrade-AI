"""Daily, supervised retention in the paper worker. Disabled by default."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.runtime_status import ControlledRuntimeStatusRow

if TYPE_CHECKING:
    from app.workers.paper_worker import CycleOutcome

logger = structlog.get_logger(__name__)
_COMPONENT = "db_retention_daily"
# One transaction-scoped lock across all days and workers. PostgreSQL releases
# it on disconnect/rollback, including a process crash; no pooled lock leaks.
_LOCK_KEY = 0x4154524554454E


class DbRetentionCycle:
    """Claim one UTC day durably before work, under a pass-wide advisory lock.

    A failed/interrupted pass consumes the day, preventing retries on restart.
    Bounded batches may leave a backlog for the next day. The existing runtime
    status table stores the claim; no migration or service is needed.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        session_factory: Callable[[], Session] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self._session_factory = session_factory
        self._now = now
        self._stop = threading.Event()

    def __call__(self) -> CycleOutcome:
        from app.workers.paper_worker import CycleOutcome

        if self._stop.is_set():
            return CycleOutcome("stopped")
        if not self.settings.retention_enabled:
            return CycleOutcome("disabled")
        now = self._now().astimezone(UTC)
        if now.hour < self.settings.retention_schedule_hour_utc:
            return CycleOutcome("waiting")
        try:
            factory = self._session_factory
            if factory is None:
                from app.db.session import get_session_factory

                factory = get_session_factory()
            with factory() as lock_session, lock_session.begin():
                if lock_session.get_bind().dialect.name == "postgresql":
                    held = lock_session.execute(
                        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}
                    ).scalar_one()
                    if not held:
                        return CycleOutcome("busy")
                elif lock_session.get_bind().dialect.name != "sqlite":
                    # SQLite is a unit-test stand-in, never the worker datastore.
                    return CycleOutcome("unsupported_database")
                if not self._claim_day(factory, now):
                    return CycleOutcome("already_ran")
                return self._run(factory, now)
        except Exception as exc:
            # Never include exception text: DB errors can carry credentials/SQL.
            logger.error("db_retention_cycle_failed", error=type(exc).__name__)
            return CycleOutcome("failed")

    @staticmethod
    def _claim_day(factory: Callable[[], Session], now: datetime) -> bool:
        # Caller holds the global advisory lock, so initial insertion is safe.
        with factory() as session, session.begin():
            row = session.get(ControlledRuntimeStatusRow, _COMPONENT)
            if row is not None and row.last_scan_at is not None:
                previous = row.last_scan_at
                if previous.tzinfo is None:  # SQLite strips timezone metadata.
                    previous = previous.replace(tzinfo=UTC)
                if previous.astimezone(UTC).date() >= now.date():
                    return False
            if row is None:
                row = ControlledRuntimeStatusRow(component=_COMPONENT, heartbeat_at=now)
                session.add(row)
            row.heartbeat_at = now
            row.last_scan_at = now
            row.last_scan_reason = "daily_claim"
            row.activation_state = "claimed"
        return True

    def _run(self, factory: Callable[[], Session], now: datetime) -> CycleOutcome:
        from app.services.db_retention import (
            retention_policies,
            run_retention,
            tables_with_deletes,
            vacuum_tables,
        )
        from app.workers.paper_worker import CycleOutcome

        results = run_retention(factory, self.settings, now=now, should_stop=self._stop.is_set)
        deleted = sum(result.deleted for result in results)
        status = "dry_run" if self.settings.retention_dry_run else "pruned"
        # Every policy plus the candidate purge reports; a missing one failed.
        if len(results) != len(retention_policies(self.settings)) + 1:
            status = "incomplete"
        if self.settings.retention_vacuum_analyze and not self.settings.retention_dry_run:
            vacuum_tables(factory, self.settings, tables_with_deletes(results))
        logger.info(
            "db_retention_cycle_finished",
            status=status,
            matched=sum(result.matched for result in results),
            deleted=deleted,
            complete=all(result.complete for result in results),
        )
        return CycleOutcome(status, delivered=deleted)

    def request_stop(self) -> None:
        self._stop.set()
