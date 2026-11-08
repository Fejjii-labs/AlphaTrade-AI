"""Soft RSS watchdog for the paper worker.

Render kills the worker at its plan memory limit without a graceful stop. The
watchdog checks resident memory on the supervisor's main thread and, above a
lower soft limit, asks the supervisor to stop cleanly and exit nonzero so the
platform restarts it.

A worker whose baseline is already at the limit must not restart every check
interval. No check runs during a startup grace period counted from process
start. A small state file (trip count and time only) doubles that grace after
each trip that came within a short window after the grace ended, up to a cap. When the file does not
survive the restart, the plain grace still limits restarts to one per grace.

An optional emergency ceiling, above the soft limit, is checked on its own
short interval even during the grace. A fast spike toward the plan limit then
becomes the same clean stop and exit instead of a kernel OOM kill, which
skips the shutdown path and leaves leases held.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

import structlog

from app.observability.process_memory import read_process_memory

logger = structlog.get_logger("observability.memory_watchdog")

# EX_TEMPFAIL: the process is healthy to restart, not misconfigured.
MEMORY_WATCHDOG_EXIT_CODE = 75


def _rss_bytes() -> int:
    return read_process_memory().rss_bytes


def _prior_trips(path: Path | None, *, now: float, max_age_seconds: float) -> int:
    """Consecutive early trips from the previous run. Missing, stale or bad state is 0."""
    if path is None:
        return 0
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        trips = state["consecutive_trips"]
        tripped_at = state["tripped_at"]
    except FileNotFoundError:
        return 0
    except Exception as exc:
        logger.warning(
            "paper_worker_memory_watchdog_state_unreadable", error_type=type(exc).__name__
        )
        return 0
    if type(trips) is not int or not isinstance(tripped_at, int | float) or trips < 0:
        return 0
    if not 0 <= now - tripped_at <= max_age_seconds:
        return 0
    return trips


def _write_trips(path: Path | None, *, trips: int, now: float) -> None:
    if path is None:
        return
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps({"consecutive_trips": trips, "tripped_at": now}), encoding="utf-8"
        )
        os.replace(tmp, path)
    except Exception as exc:
        # Losing the backoff state must never stop the trip.
        logger.warning(
            "paper_worker_memory_watchdog_state_unwritten", error_type=type(exc).__name__
        )


class MemoryWatchdog:
    """Trips once RSS reaches ``limit_bytes``. Checks at most every ``check_seconds``.

    No check runs before ``startup_grace_seconds * 2**n`` (capped at
    ``backoff_max_seconds``) after construction, where ``n`` counts the earlier
    consecutive trips recorded in ``state_path``. A trip within
    ``early_trip_window_seconds`` after the grace ends continues the streak
    (the baseline was already at the limit); a later trip is an ordinary leak
    and starts a new streak of one, so it does not lengthen the next grace.

    ``ceiling_bytes`` (None or 0 = off) is checked every
    ``ceiling_check_seconds`` from construction, grace or not. It is never
    below ``limit_bytes``, so it cannot trip earlier than the soft limit would
    after the grace.
    """

    def __init__(
        self,
        *,
        limit_bytes: int,
        check_seconds: float,
        startup_grace_seconds: float = 0.0,
        backoff_max_seconds: float = 0.0,
        early_trip_window_seconds: float = 900.0,
        state_path: str | Path | None = None,
        ceiling_bytes: int | None = None,
        ceiling_check_seconds: float = 5.0,
        read_rss: Callable[[], int] = _rss_bytes,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.limit_bytes = limit_bytes
        self.check_seconds = check_seconds
        self._read_rss = read_rss
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._state_path = Path(state_path) if state_path else None
        self._early_trip_window_seconds = early_trip_window_seconds
        self.prior_trips = _prior_trips(
            self._state_path,
            now=wall_clock(),
            max_age_seconds=max(backoff_max_seconds, startup_grace_seconds),
        )
        self.grace_seconds = min(
            startup_grace_seconds * 2**self.prior_trips,
            max(backoff_max_seconds, startup_grace_seconds),
        )
        self._started_at = monotonic()
        self._next_check_at: float | None = (
            self._started_at + self.grace_seconds if self.grace_seconds > 0 else None
        )
        self.ceiling_bytes = max(ceiling_bytes, limit_bytes) if ceiling_bytes else None
        self._ceiling_check_seconds = ceiling_check_seconds
        self._next_ceiling_check_at = self._started_at
        self.tripped = False
        self.consecutive_trips = 0
        logger.info(
            "paper_worker_memory_watchdog_armed",
            limit_bytes=limit_bytes,
            ceiling_bytes=self.ceiling_bytes,
            grace_seconds=self.grace_seconds,
            prior_trips=self.prior_trips,
        )

    def seconds_until_check(self) -> float:
        now = self._monotonic()
        due = 0.0 if self._next_check_at is None else max(self._next_check_at - now, 0.0)
        if self.ceiling_bytes is not None:
            due = min(due, max(self._next_ceiling_check_at - now, 0.0))
        return due

    def check(self) -> bool:
        """Return True when RSS is at or above the limit. A read failure never trips."""
        if self.tripped:
            return True
        now = self._monotonic()
        limit_due = self._next_check_at is None or now >= self._next_check_at
        ceiling_due = self.ceiling_bytes is not None and now >= self._next_ceiling_check_at
        if not (limit_due or ceiling_due):
            return False
        if limit_due:
            self._next_check_at = now + self.check_seconds
        if ceiling_due:
            self._next_ceiling_check_at = now + self._ceiling_check_seconds
        try:
            rss = self._read_rss()
        except Exception as exc:
            logger.warning(
                "paper_worker_memory_watchdog_read_failed", error_type=type(exc).__name__
            )
            return False
        if limit_due and rss >= self.limit_bytes:
            trigger = "limit"
        elif self.ceiling_bytes is not None and rss >= self.ceiling_bytes:
            trigger = "ceiling"
        else:
            return False
        self.tripped = True
        uptime = now - self._started_at
        early = uptime <= self.grace_seconds + self._early_trip_window_seconds
        trips = self.prior_trips + 1 if early else 1
        self.consecutive_trips = trips
        _write_trips(self._state_path, trips=trips, now=self._wall_clock())
        logger.error(
            "paper_worker_memory_watchdog_tripped",
            rss_bytes=rss,
            limit_bytes=self.limit_bytes,
            ceiling_bytes=self.ceiling_bytes,
            trigger=trigger,
            uptime_seconds=round(uptime, 1),
            consecutive_trips=trips,
            exit_code=MEMORY_WATCHDOG_EXIT_CODE,
            action="graceful_stop_then_exit",
        )
        return True
