"""Typed persistence, evaluation, clock, and side-effect ports.

PostgreSQL adapters bind these interfaces in a later integration phase.
This wave uses deterministic in-memory repositories only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.watcher.contracts import (
    EvaluationCommand,
    EvaluationOutcome,
    ScanAttempt,
    ScanLineage,
    ScheduledScan,
    SourceFetchAttempt,
    SubscriptionEvaluationAttempt,
    WatcherHealthSnapshot,
    WatcherHeartbeat,
    WatcherObservabilityEvent,
    WatcherPolicyVersion,
    WorkerLease,
)
from app.watcher.errors import SimulatedWorkerCrashError


class Clock(Protocol):
    def now(self) -> datetime: ...


class WatcherEvaluationBoundary(Protocol):
    """Single evaluation boundary for manual and worker callers."""

    def evaluate(self, command: EvaluationCommand) -> EvaluationOutcome: ...


class SideEffectPorts(Protocol):
    """Execution / journal / Telegram ports. Orchestration must not invoke them."""

    def submit_execution(self, payload: str) -> None: ...

    def record_journal(self, payload: str) -> None: ...

    def send_telegram(self, payload: str) -> None: ...


class CrashBarrier(Protocol):
    def checkpoint(self, name: str) -> None: ...


class WatcherStore(Protocol):
    def get_schedule(
        self,
        organization_id: UUID,
        principal_id: UUID | None,
        idempotency_key: str,
    ) -> ScheduledScan | None: ...

    def insert_schedule(self, row: ScheduledScan) -> ScheduledScan: ...

    def get_lineage(self, lineage_id: UUID, organization_id: UUID) -> ScanLineage | None: ...

    def insert_lineage(self, row: ScanLineage) -> ScanLineage: ...

    def cas_lineage_terminal(
        self,
        lineage_id: UUID,
        attempt_id: UUID,
        status: str,
        organization_id: UUID,
    ) -> ScanLineage: ...

    def list_attempts(self, lineage_id: UUID) -> tuple[ScanAttempt, ...]: ...

    def get_attempt(self, attempt_id: UUID) -> ScanAttempt | None: ...

    def insert_attempt(self, row: ScanAttempt) -> ScanAttempt: ...

    def update_attempt(self, row: ScanAttempt) -> ScanAttempt: ...

    def insert_source_fetch(self, row: SourceFetchAttempt) -> SourceFetchAttempt: ...

    def list_source_fetches(self, scan_attempt_id: UUID) -> tuple[SourceFetchAttempt, ...]: ...

    def insert_subscription_eval(
        self, row: SubscriptionEvaluationAttempt
    ) -> SubscriptionEvaluationAttempt: ...

    def list_subscription_evals(
        self, scan_attempt_id: UUID
    ) -> tuple[SubscriptionEvaluationAttempt, ...]: ...

    def claim_lease(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        ttl_seconds: int,
        now: datetime,
        fencing_token: int | None = None,
    ) -> tuple[bool, WorkerLease, str]:
        """Claim or renew one scope lease.

        An active lease renews only when ``fencing_token`` is the current token
        held by ``owner_id``. Same-owner callers without that token are
        ``lease_held``. Expired leases may be taken over without a token.
        """
        ...

    def renew_lease(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        fencing_token: int,
        ttl_seconds: int,
        now: datetime,
    ) -> bool: ...

    def release_lease(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> bool:
        """Expire one lease now, only if ``owner_id`` still holds ``fencing_token``.

        Owner, epoch and token are kept, so the next claim takes over with a new
        epoch exactly as after a natural expiry. Any mismatch is a no-op (False).
        """
        ...

    def get_lease(self, organization_id: UUID, scan_scope: str) -> WorkerLease | None: ...

    def fence_is_active(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> bool: ...

    def record_heartbeat(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        lease_epoch: int,
        fencing_token: int,
        now: datetime,
        detail: str | None = None,
    ) -> WatcherHeartbeat: ...

    def get_heartbeat(self, organization_id: UUID, scan_scope: str) -> WatcherHeartbeat | None: ...

    def put_policy_version(self, version: WatcherPolicyVersion) -> WatcherPolicyVersion: ...

    def get_policy_version(self, policy_id: UUID, version: int) -> WatcherPolicyVersion | None: ...

    def remember_health(self, snapshot: WatcherHealthSnapshot) -> None: ...

    def latest_health(
        self, organization_id: UUID, scan_scope: str
    ) -> WatcherHealthSnapshot | None: ...

    def latest_attempt_for_scope(
        self, organization_id: UUID, scan_scope: str
    ) -> ScanAttempt | None: ...

    def append_event(self, event: WatcherObservabilityEvent) -> None: ...

    def events(self) -> tuple[WatcherObservabilityEvent, ...]: ...


class NoCrashBarrier:
    def checkpoint(self, name: str) -> None:
        del name


class NamedCrashBarrier:
    """Raises :class:`SimulatedWorkerCrashError` at the configured checkpoint."""

    def __init__(self, crash_at: str | None = None) -> None:
        self.crash_at = crash_at
        self.seen: list[str] = []

    def checkpoint(self, name: str) -> None:
        self.seen.append(name)
        if self.crash_at is not None and name == self.crash_at:
            raise SimulatedWorkerCrashError(name)
