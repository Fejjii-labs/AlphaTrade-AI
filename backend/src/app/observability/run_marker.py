"""A "running" marker file that shows whether the previous run stopped cleanly.

The worker writes the marker at boot and removes it after a clean stop
(a signal-driven shutdown or a memory-watchdog exit). A marker that is still
there at the next boot means the previous run ended without that stop: a
platform OOM kill, SIGKILL after the shutdown deadline, or a crash.

The file holds the previous start time only; nothing secret. It is
best-effort: a read or write failure is logged and never stops the worker.
The check only works when the file survives the restart. Render's default
filesystem is ephemeral, so on a container replacement the marker is lost
and an unclean exit goes unreported (``docs/paper_worker_memory_watchdog.md``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger("observability.run_marker")


@dataclass(frozen=True)
class UncleanExit:
    """The previous run left its marker. ``started_at`` is None when unreadable."""

    started_at: float | None


def claim_run_marker(path: str | Path | None, *, now: float) -> UncleanExit | None:
    """Report a leftover marker, then write this run's marker. Never raises."""

    if not path:
        return None
    marker = Path(path)
    previous: UncleanExit | None = None
    try:
        text = marker.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = None
    except Exception as exc:
        logger.warning("paper_worker_run_marker_unreadable", error_type=type(exc).__name__)
        text = None
    if text is not None:
        started_at: float | None = None
        try:
            value = json.loads(text)["started_at"]
            if isinstance(value, int | float) and not isinstance(value, bool):
                started_at = float(value)
        except Exception:
            started_at = None
        previous = UncleanExit(started_at=started_at)
    try:
        tmp = marker.with_name(marker.name + ".tmp")
        tmp.write_text(json.dumps({"started_at": now}), encoding="utf-8")
        os.replace(tmp, marker)
    except Exception as exc:
        logger.warning("paper_worker_run_marker_unwritten", error_type=type(exc).__name__)
    return previous


def clear_run_marker(path: str | Path | None) -> None:
    """Remove the marker after a clean stop. Never raises."""

    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("paper_worker_run_marker_uncleared", error_type=type(exc).__name__)
