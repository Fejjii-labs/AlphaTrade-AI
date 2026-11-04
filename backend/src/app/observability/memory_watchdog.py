"""Soft RSS watchdog for the paper worker.

Render kills the worker at its plan memory limit without a graceful stop. The
watchdog checks resident memory on the supervisor's main thread and, above a
lower soft limit, asks the supervisor to stop cleanly and exit nonzero so the
platform restarts it.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import structlog

from app.observability.process_memory import read_process_memory

logger = structlog.get_logger("observability.memory_watchdog")

# EX_TEMPFAIL: the process is healthy to restart, not misconfigured.
MEMORY_WATCHDOG_EXIT_CODE = 75


def _rss_bytes() -> int:
    return read_process_memory().rss_bytes


class MemoryWatchdog:
    """Trips once RSS reaches ``limit_bytes``. Checks at most every ``check_seconds``."""

    def __init__(
        self,
        *,
        limit_bytes: int,
        check_seconds: float,
        read_rss: Callable[[], int] = _rss_bytes,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit_bytes = limit_bytes
        self.check_seconds = check_seconds
        self._read_rss = read_rss
        self._monotonic = monotonic
        self._next_check_at: float | None = None
        self.tripped = False

    def seconds_until_check(self) -> float:
        if self._next_check_at is None:
            return 0.0
        return max(self._next_check_at - self._monotonic(), 0.0)

    def check(self) -> bool:
        """Return True when RSS is at or above the limit. A read failure never trips."""
        if self.tripped:
            return True
        now = self._monotonic()
        if self._next_check_at is not None and now < self._next_check_at:
            return False
        self._next_check_at = now + self.check_seconds
        try:
            rss = self._read_rss()
        except Exception as exc:
            logger.warning(
                "paper_worker_memory_watchdog_read_failed", error_type=type(exc).__name__
            )
            return False
        if rss < self.limit_bytes:
            return False
        self.tripped = True
        logger.error(
            "paper_worker_memory_watchdog_tripped",
            rss_bytes=rss,
            limit_bytes=self.limit_bytes,
            exit_code=MEMORY_WATCHDOG_EXIT_CODE,
            action="graceful_stop_then_exit",
        )
        return True
