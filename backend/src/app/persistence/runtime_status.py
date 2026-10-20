"""Read and write observed Watcher and Telegram runtime status."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.runtime_status import ControlledRuntimeStatusRow

WATCHER_COMPONENT = "watcher"
TELEGRAM_COMPONENT = "telegram"
PAPER_WORKER_COMPONENT = "paper_worker"


@dataclass(frozen=True, slots=True)
class RuntimeStatusWrite:
    """Fields a process may publish. Empty strings mean unset, not a secret."""

    component: str
    worker_id: str
    heartbeat_at: datetime
    activation_state: str
    lease_owner: str = ""
    lease_epoch: int = 0
    lease_expires_at: datetime | None = None
    fence_held: bool = False
    last_scan_at: datetime | None = None
    last_scan_reason: str = ""
    market_source: str = ""
    freshness_seconds: float | None = None
    telegram_runtime_state: str = "absent"
    inbound_mode: str = "off"
    outbox_pending: int = 0
    outbox_retryable: int = 0
    outbox_dead_letter: int = 0
    last_delivery_at: datetime | None = None
    last_error_code: str = ""
    kill_switch_active: bool = False
    request_count: int = 0
    request_weight: int = 0
    rate_limited_count: int = 0
    cache_hits: int = 0
    process_rss_bytes: int | None = None
    process_rss_peak_bytes: int | None = None
    preserve_lease: bool = False


def publish_runtime_status(
    session_factory: sessionmaker[Session],
    snapshot: RuntimeStatusWrite,
) -> None:
    """Upsert one component row. ``preserve_lease`` keeps an existing lease."""

    with session_factory() as session, session.begin():
        row = session.get(
            ControlledRuntimeStatusRow,
            snapshot.component,
            with_for_update=True,
        )
        if row is None:
            session.add(_row_from_snapshot(snapshot))
            return
        _apply_snapshot(row, snapshot)


def try_acquire_runtime_lease(
    session_factory: sessionmaker[Session],
    *,
    component: str,
    owner: str,
    now: datetime,
    ttl_seconds: int,
) -> bool:
    """Claim or renew one runtime lease. A live lease owned by someone else loses."""

    expires = now + timedelta(seconds=ttl_seconds)
    with session_factory() as session, session.begin():
        row = session.get(ControlledRuntimeStatusRow, component, with_for_update=True)
        if row is None:
            session.add(
                ControlledRuntimeStatusRow(
                    component=component,
                    worker_id=owner,
                    heartbeat_at=now,
                    activation_state="starting",
                    lease_owner=owner,
                    lease_epoch=1,
                    lease_expires_at=expires,
                    fence_held=True,
                )
            )
            return True
        held_by_other = (
            row.lease_owner not in ("", owner)
            and row.lease_expires_at is not None
            and row.lease_expires_at > now
        )
        if held_by_other:
            return False
        row.lease_owner = owner
        row.lease_epoch = int(row.lease_epoch) + 1
        row.lease_expires_at = expires
        row.fence_held = True
        row.worker_id = owner
        row.heartbeat_at = now
        return True


def load_runtime_rows(
    session: Session,
) -> dict[str, ControlledRuntimeStatusRow]:
    rows = session.scalars(select(ControlledRuntimeStatusRow)).all()
    return {row.component: row for row in rows}


def _row_from_snapshot(snapshot: RuntimeStatusWrite) -> ControlledRuntimeStatusRow:
    return ControlledRuntimeStatusRow(
        component=snapshot.component,
        worker_id=snapshot.worker_id,
        heartbeat_at=snapshot.heartbeat_at,
        activation_state=snapshot.activation_state[:32],
        lease_owner=snapshot.lease_owner[:80],
        lease_epoch=snapshot.lease_epoch,
        lease_expires_at=snapshot.lease_expires_at,
        fence_held=snapshot.fence_held,
        last_scan_at=snapshot.last_scan_at,
        last_scan_reason=snapshot.last_scan_reason[:64],
        market_source=snapshot.market_source[:32],
        freshness_seconds=snapshot.freshness_seconds,
        telegram_runtime_state=snapshot.telegram_runtime_state[:32],
        inbound_mode=snapshot.inbound_mode[:16],
        outbox_pending=snapshot.outbox_pending,
        outbox_retryable=snapshot.outbox_retryable,
        outbox_dead_letter=snapshot.outbox_dead_letter,
        last_delivery_at=snapshot.last_delivery_at,
        last_error_code=snapshot.last_error_code[:64],
        kill_switch_active=snapshot.kill_switch_active,
        request_count=snapshot.request_count,
        request_weight=snapshot.request_weight,
        rate_limited_count=snapshot.rate_limited_count,
        cache_hits=snapshot.cache_hits,
        process_rss_bytes=snapshot.process_rss_bytes,
        process_rss_peak_bytes=snapshot.process_rss_peak_bytes,
    )


def _apply_snapshot(row: ControlledRuntimeStatusRow, snapshot: RuntimeStatusWrite) -> None:
    row.heartbeat_at = snapshot.heartbeat_at
    row.activation_state = snapshot.activation_state[:32]
    if not snapshot.preserve_lease:
        row.worker_id = snapshot.worker_id[:80]
        row.lease_owner = snapshot.lease_owner[:80]
        row.lease_epoch = snapshot.lease_epoch
        row.lease_expires_at = snapshot.lease_expires_at
        row.fence_held = snapshot.fence_held
    row.last_scan_at = snapshot.last_scan_at
    row.last_scan_reason = snapshot.last_scan_reason[:64]
    row.market_source = snapshot.market_source[:32]
    row.freshness_seconds = snapshot.freshness_seconds
    row.telegram_runtime_state = snapshot.telegram_runtime_state[:32]
    row.inbound_mode = snapshot.inbound_mode[:16]
    row.outbox_pending = snapshot.outbox_pending
    row.outbox_retryable = snapshot.outbox_retryable
    row.outbox_dead_letter = snapshot.outbox_dead_letter
    # An idle cycle did not deliver. Keep the previous timestamp.
    if snapshot.last_delivery_at is not None:
        row.last_delivery_at = snapshot.last_delivery_at
    row.last_error_code = snapshot.last_error_code[:64]
    row.kill_switch_active = snapshot.kill_switch_active
    row.request_count = snapshot.request_count
    row.request_weight = snapshot.request_weight
    row.rate_limited_count = snapshot.rate_limited_count
    row.cache_hits = snapshot.cache_hits
    row.process_rss_bytes = snapshot.process_rss_bytes
    row.process_rss_peak_bytes = snapshot.process_rss_peak_bytes
