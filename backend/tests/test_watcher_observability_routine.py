"""M11/L10: replayed-bar events are neither stored nor logged at INFO."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
import structlog
from structlog.testing import capture_logs

from app.watcher import observability as observability_module
from app.watcher.observability import emit, is_routine_event


class _Store:
    def __init__(self) -> None:
        self.events: list[object] = []

    def append_event(self, event: object) -> None:
        self.events.append(event)


_AT = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    # structlog caches the module logger on first use once app logging is
    # configured (e.g. by an earlier watcher test); a fresh proxy keeps
    # capture_logs independent of test order.
    monkeypatch.setattr(
        observability_module, "logger", structlog.get_logger("watcher.orchestration")
    )


def test_replay_events_are_not_stored_and_log_at_debug() -> None:
    store = _Store()
    org = uuid4()
    with capture_logs() as logs:
        emit(store, name="watcher_schedule_replayed", at=_AT, organization_id=org)  # type: ignore[arg-type]
        emit(store, name="watcher_scan_replayed", at=_AT, organization_id=org)  # type: ignore[arg-type]
        emit(
            store,  # type: ignore[arg-type]
            name="watcher_lease_claimed",
            at=_AT,
            organization_id=org,
            reason_code="renewed",
        )
    assert store.events == []
    # The configured level filter drops DEBUG before capture; nothing reaches INFO.
    assert all(e["log_level"] == "debug" for e in logs)


def test_first_time_events_are_still_stored_at_info() -> None:
    store = _Store()
    with capture_logs() as logs:
        emit(store, name="watcher_lease_claimed", at=_AT, reason_code="claimed")  # type: ignore[arg-type]
        emit(store, name="watcher_scan_finished", at=_AT, reason_code="succeeded")  # type: ignore[arg-type]
        emit(store, name="watcher_lease_rejected", at=_AT, reason_code="lease_held")  # type: ignore[arg-type]
    assert len(store.events) == 3
    assert [e["log_level"] for e in logs] == ["info"] * 3
    assert not is_routine_event("watcher_stale_fence_rejected", {})
