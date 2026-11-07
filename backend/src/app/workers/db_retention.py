"""Low-cadence supervised retention pass (H5). Absent unless RETENTION_ENABLED."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.orm import Session

from app.core.config import Settings

if TYPE_CHECKING:
    from app.workers.paper_worker import CycleOutcome

logger = structlog.get_logger(__name__)


class DbRetentionCycle:
    """Runs the retention policies at most once per ``retention_interval_seconds``.

    The supervisor ticks it at its poll interval; between passes a tick is a
    no-op. It never touches trades, orders, journal, audit or strategy rows.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        session_factory: Callable[[], Session] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._session_factory = session_factory
        self._monotonic = monotonic
        self._stop = threading.Event()
        # First pass waits one interval so a restart loop cannot hammer the DB.
        self._next_run = monotonic() + settings.retention_interval_seconds

    def __call__(self) -> CycleOutcome:
        from datetime import UTC, datetime

        from app.services.db_retention import run_retention
        from app.workers.paper_worker import CycleOutcome

        if self._stop.is_set():
            return CycleOutcome("stopped")
        if self._monotonic() < self._next_run:
            return CycleOutcome("waiting")
        self._next_run = self._monotonic() + self.settings.retention_interval_seconds
        factory = self._session_factory
        if factory is None:
            from app.db.session import get_session_factory

            factory = get_session_factory()
        results = run_retention(
            factory,
            self.settings,
            now=datetime.now(UTC),
            should_stop=self._stop.is_set,
        )
        deleted = sum(result.deleted for result in results)
        status = "dry_run" if self.settings.retention_dry_run else "pruned"
        return CycleOutcome(status, delivered=deleted)

    def request_stop(self) -> None:
        self._stop.set()
