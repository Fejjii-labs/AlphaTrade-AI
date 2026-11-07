"""Structured watcher orchestration observability. No secrets."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog

from app.watcher.contracts import WatcherObservabilityEvent
from app.watcher.ports import WatcherStore

logger = structlog.get_logger("watcher.orchestration")

# Routine events repeat every cycle while a closed bar is replayed (about 59 of
# every 60 cycles on a 15 m timeframe). They carry no new fact, so they are not
# stored and log at DEBUG (M11, L10). Every first-time event, block, rejection
# and failure is still stored and logged at INFO.
_ROUTINE_EVENTS = frozenset({"watcher_schedule_replayed", "watcher_scan_replayed"})


def is_routine_event(name: str, fields: dict[str, str | int | bool | None]) -> bool:
    if name in _ROUTINE_EVENTS:
        return True
    return name == "watcher_lease_claimed" and fields.get("reason_code") == "renewed"


def emit(
    store: WatcherStore,
    *,
    name: str,
    at: datetime,
    lineage_id: UUID | None = None,
    attempt_id: UUID | None = None,
    organization_id: UUID | None = None,
    scan_scope: str | None = None,
    fencing_token: int | None = None,
    **fields: str | int | bool | None,
) -> None:
    event = WatcherObservabilityEvent(
        name=name,
        at=at,
        lineage_id=lineage_id,
        attempt_id=attempt_id,
        organization_id=organization_id,
        scan_scope=scan_scope,
        fencing_token=fencing_token,
        fields=dict(fields),
    )
    routine = is_routine_event(name, event.fields)
    if not routine:
        store.append_event(event)
    log = logger.debug if routine else logger.info
    log(
        name,
        lineage_id=None if lineage_id is None else str(lineage_id),
        attempt_id=None if attempt_id is None else str(attempt_id),
        organization_id=None if organization_id is None else str(organization_id),
        scan_scope=scan_scope,
        fencing_token=fencing_token,
        **fields,
    )
