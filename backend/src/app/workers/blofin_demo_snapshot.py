"""Read-only BloFin demo snapshot cycle for the paper worker.

Calls the existing ``BloFinSyncService.sync`` on a 30-60 second cadence.
Demo gates and native identity checks stay inside that service. A closed gate
or a missing owner pin returns without opening a database or calling BloFin.
Errors back off. A snapshot newer than the interval is left in place so the
browser refresh and this cycle do not both hit the venue. The hand-trade
mirror still runs on that fresh path. A mirror read failure rolls back only
its own write and does not back off the snapshot.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import TYPE_CHECKING, Protocol

import structlog

from app.core.config import Settings
from app.schemas.common import ActorType, BloFinSyncHealthStatus
from app.services.dashboard.demo_account import demo_account_active

if TYPE_CHECKING:
    from app.schemas.blofin_sync import BloFinSyncSnapshotItem
    from app.workers.paper_worker import CycleOutcome

logger = structlog.get_logger("workers.blofin_demo_snapshot")

_BACKOFF_CAP_SECONDS = 900
_FRESH = {BloFinSyncHealthStatus.OK, BloFinSyncHealthStatus.DEGRADED}


class BloFinDemoSnapshotCycle:
    """One supervised read-only tick. It never places or cancels orders."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings.model_copy(deep=True)
        self._clock: Callable[[], datetime] = clock or _now
        self._stop = Event()
        self._failures = 0
        self._next_attempt: datetime | None = None

    def __call__(self) -> CycleOutcome:
        from app.workers.paper_worker import CycleOutcome

        if self._stop.is_set():
            return CycleOutcome("stopped")
        now = self._clock()
        if self._next_attempt is not None and now < self._next_attempt:
            return CycleOutcome("backing_off", error="snapshot_backoff")
        if not demo_account_active(self.settings):
            self._failures = 0
            self._next_attempt = None
            return CycleOutcome("skipped", error="demo_gates_closed")
        owner = snapshot_owner(self.settings)
        if owner is None:
            return CycleOutcome("skipped", error="demo_owner_unpinned")
        try:
            outcome = self._sync_once(*owner)
        except Exception as exc:
            logger.warning(
                "blofin_demo_snapshot_cycle_failed",
                error_type=type(exc).__name__,
            )
            self._arm_backoff(now)
            return CycleOutcome("degraded", error="snapshot_sync_failed")
        if outcome == "unavailable":
            self._arm_backoff(now)
            return CycleOutcome("degraded", error="snapshot_unavailable")
        self._failures = 0
        self._next_attempt = None
        return CycleOutcome("ok")

    def request_stop(self) -> None:
        self._stop.set()

    def _arm_backoff(self, now: datetime) -> None:
        self._failures += 1
        delay = min(
            _BACKOFF_CAP_SECONDS,
            self.settings.blofin_demo_snapshot_sync_seconds * 2 ** min(self._failures, 4),
        )
        self._next_attempt = now + timedelta(seconds=delay)

    def _sync_once(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> str:
        from app.db.session import get_session_factory
        from app.services.blofin_sync_service import BloFinSyncService

        session = get_session_factory()()
        try:
            service = BloFinSyncService(session, self.settings)
            fresh = _fresh_snapshot(service, organization_id, self._clock(), self.settings)
            if not fresh:
                result = service.sync(
                    organization_id=organization_id,
                    user_id=user_id,
                    request_id=f"blofin-snapshot-worker-{organization_id}",
                    include_instrument_metadata=True,
                    actor_type=ActorType.SYSTEM,
                    sync_origin="worker",
                )
                session.commit()
                if result.snapshot.health_status not in _FRESH:
                    return "unavailable"
            self._mirror_without_backing_off(session, organization_id, user_id)
            return "fresh" if fresh else "ok"
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _mirror_without_backing_off(
        self,
        session: _UnitOfWork,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Run the journal mirror. A read failure must not delay the snapshot."""
        if not self.settings.blofin_demo_manual_mirror_enabled:
            return
        try:
            self._run_mirror(session, organization_id, user_id)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.warning(
                "blofin_demo_manual_mirror_failed",
                error_type=type(exc).__name__,
            )
            self._record_failed_cycle(session, organization_id, user_id)

    def _run_mirror(
        self,
        session: object,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Journal mirror. The flag stays off unless an operator sets it."""
        from app.core.blofin_readonly_access import get_readonly_client
        from app.services.blofin_demo_manual_mirror import BloFinDemoManualMirror

        client = get_readonly_client(self.settings, activity_history=True)
        BloFinDemoManualMirror(client, now=self._clock()).apply(
            session,  # type: ignore[arg-type]
            organization_id=organization_id,
            user_id=user_id,
            settings=self.settings,
        )

    def _record_failed_cycle(
        self,
        session: _UnitOfWork,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Persist a withheld cycle after the mirror transaction has rolled back."""
        from app.services.blofin_demo_manual_mirror import record_mirror_cycle

        try:
            record_mirror_cycle(
                session,  # type: ignore[arg-type]
                organization_id=organization_id,
                user_id=user_id,
                outcome="failed",
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.warning(
                "blofin_demo_manual_mirror_status_unrecorded",
                error_type=type(exc).__name__,
            )


def snapshot_owner(settings: Settings) -> tuple[uuid.UUID, uuid.UUID] | None:
    """Owner pin already used by governed demo. No organization is guessed."""
    org = settings.governed_blofin_demo_organization_id.strip()
    user = settings.governed_blofin_demo_user_id.strip()
    if not org or not user:
        return None
    try:
        return uuid.UUID(org), uuid.UUID(user)
    except ValueError:
        return None


class _UnitOfWork(Protocol):
    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class _SnapshotReader(Protocol):
    def latest(self, *, organization_id: uuid.UUID) -> BloFinSyncSnapshotItem: ...


def _fresh_snapshot(
    service: _SnapshotReader,
    organization_id: uuid.UUID,
    now: datetime,
    settings: Settings,
) -> bool:
    from app.core.errors import NotFoundError

    try:
        snapshot = service.latest(organization_id=organization_id)
    except NotFoundError:
        return False
    health = snapshot.health_status
    if health not in _FRESH:
        return False
    synced_at = snapshot.synced_at
    if synced_at.tzinfo is None:
        synced_at = synced_at.replace(tzinfo=UTC)
    age = (now - synced_at).total_seconds()
    return 0 <= age < settings.blofin_demo_snapshot_sync_seconds


def _now() -> datetime:
    return datetime.now(UTC)
