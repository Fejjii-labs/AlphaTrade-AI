"""Persist the paper-exit degraded-cycle counter across process restarts.

One ``controlled_runtime_status`` row holds the streak. A healthy cycle
deletes it. The row stores the start, the cycle count, and the last alert
time. It does not store P&L.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.db.runtime_status import ControlledRuntimeStatusRow
from app.services.paper_exit_monitor import DegradationStreak

PAPER_EXIT_DEGRADATION_COMPONENT = "paper_exit_degradation"
_WORKER_ID = "paper-exit-monitor"


class DegradationStreakStore(Protocol):
    """Load and replace the degraded-cycle streak."""

    def load(self) -> DegradationStreak | None: ...

    def save(self, streak: DegradationStreak | None, *, observed_at: datetime) -> None: ...


class PostgresDegradationStreakStore:
    """The streak row in ``controlled_runtime_status``."""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory

    def load(self) -> DegradationStreak | None:
        with self._factory()() as session:
            row = session.get(ControlledRuntimeStatusRow, PAPER_EXIT_DEGRADATION_COMPONENT)
            return streak_from_row(row)

    def save(self, streak: DegradationStreak | None, *, observed_at: datetime) -> None:
        with self._factory()() as session, session.begin():
            row = session.get(ControlledRuntimeStatusRow, PAPER_EXIT_DEGRADATION_COMPONENT)
            if streak is None:
                if row is not None:
                    session.delete(row)
                return
            target = row or ControlledRuntimeStatusRow(
                component=PAPER_EXIT_DEGRADATION_COMPONENT,
                worker_id=_WORKER_ID,
                heartbeat_at=observed_at,
            )
            apply_streak(target, streak, observed_at=observed_at)
            if row is None:
                session.add(target)

    def _factory(self) -> sessionmaker[Session]:
        if self._session_factory is not None:
            return self._session_factory
        from app.db.session import get_session_factory

        return get_session_factory()


def streak_from_row(row: object | None) -> DegradationStreak | None:
    """Read a streak. A missing, closed, or naive row is an empty streak."""

    if row is None:
        return None
    if getattr(row, "activation_state", "") != "open":
        return None
    started = getattr(row, "last_scan_at", None)
    if not isinstance(started, datetime) or started.tzinfo is None:
        return None
    cycles = getattr(row, "request_count", 0)
    if isinstance(cycles, bool) or not isinstance(cycles, int) or cycles < 1:
        return None
    last_alert = getattr(row, "last_delivery_at", None)
    if not isinstance(last_alert, datetime) or last_alert.tzinfo is None:
        last_alert = None
    return DegradationStreak(started_at=started, cycles=cycles, last_alert_at=last_alert)


def apply_streak(
    row: ControlledRuntimeStatusRow,
    streak: DegradationStreak,
    *,
    observed_at: datetime,
) -> None:
    """Write the streak onto an existing runtime-status row."""

    row.worker_id = _WORKER_ID
    row.heartbeat_at = observed_at
    row.activation_state = "open"
    row.last_scan_at = streak.started_at
    row.request_count = streak.cycles
    row.last_delivery_at = streak.last_alert_at
    row.last_error_code = ""
    row.market_source = "paper_exit_monitor"
