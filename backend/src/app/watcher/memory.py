"""Deterministic in-memory repositories and test doubles."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import UUID

from app.watcher.contracts import (
    EvaluationCommand,
    EvaluationOutcome,
    EvaluationStatus,
    ScanAttempt,
    ScanAttemptStatus,
    ScanLineage,
    ScheduledScan,
    SourceFetchAttempt,
    SubscriptionEvaluationAttempt,
    UnitAttempt,
    UnitAttemptKind,
    UnitAttemptStatus,
    WatcherHealthSnapshot,
    WatcherHeartbeat,
    WatcherObservabilityEvent,
    WatcherPolicyVersion,
    WorkerLease,
)
from app.watcher.errors import WatcherIdempotencyConflictError, WatcherTenantMismatchError
from app.watcher.hashing import tenant_scope_key


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 9, 16, 16, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now


class SideEffectProbe:
    """Records forbidden side effects. Orchestration must leave these empty."""

    def __init__(self) -> None:
        self.execution: list[str] = []
        self.journal: list[str] = []
        self.telegram: list[str] = []

    def submit_execution(self, payload: str) -> None:
        self.execution.append(payload)

    def record_journal(self, payload: str) -> None:
        self.journal.append(payload)

    def send_telegram(self, payload: str) -> None:
        self.telegram.append(payload)

    @property
    def unused(self) -> bool:
        return not self.execution and not self.journal and not self.telegram


class ScriptedEvaluationBoundary:
    """Deterministic evaluation double. Never creates candidates or market I/O."""

    def __init__(
        self,
        *,
        outcomes: list[EvaluationStatus] | None = None,
        default: EvaluationStatus = EvaluationStatus.SUCCEEDED,
        reason_code: str = "scripted",
        error: str | None = None,
        raise_on: int | None = None,
        raise_exc: Exception | None = None,
        unit_factory: Callable[[EvaluationCommand, EvaluationStatus], tuple[UnitAttempt, ...]]
        | None = None,
    ) -> None:
        self._queued = list(outcomes or [])
        self._default = default
        self._reason_code = reason_code
        self._error = error
        self._raise_on = raise_on
        self._raise_exc = raise_exc or RuntimeError("scripted evaluator crash")
        self._unit_factory = unit_factory
        self.commands: list[EvaluationCommand] = []

    @property
    def call_count(self) -> int:
        return len(self.commands)

    def evaluate(self, command: EvaluationCommand) -> EvaluationOutcome:
        self.commands.append(command)
        if self._raise_on is not None and self.call_count == self._raise_on:
            raise self._raise_exc
        status = self._queued.pop(0) if self._queued else self._default
        units = (
            self._unit_factory(command, status)
            if self._unit_factory is not None
            else _default_units(command, status)
        )
        failed = sum(1 for unit in units if unit.status is UnitAttemptStatus.FAILED)
        error = self._error if status is EvaluationStatus.FAILED else None
        if status is EvaluationStatus.FAILED and error is None:
            error = "scripted_failure"
        return EvaluationOutcome(
            command_id=command.command_id,
            request_hash=command.request_hash,
            evaluation_input_hash=command.evaluation_input_hash,
            status=status,
            reason_code=(
                self._reason_code if status is not EvaluationStatus.FAILED else "scripted_failure"
            ),
            evaluated_units=len(units),
            failed_units=failed,
            unit_attempts=units,
            error=error,
            candidate_ids=(),
        )


def _default_units(command: EvaluationCommand, status: EvaluationStatus) -> tuple[UnitAttempt, ...]:
    unit_status = {
        EvaluationStatus.SUCCEEDED: UnitAttemptStatus.SUCCEEDED,
        EvaluationStatus.DEGRADED: UnitAttemptStatus.DEGRADED,
        EvaluationStatus.FAILED: UnitAttemptStatus.FAILED,
        EvaluationStatus.BLOCKED: UnitAttemptStatus.SKIPPED,
    }[status]
    return tuple(
        UnitAttempt(
            kind=UnitAttemptKind.SUBSCRIPTION_EVALUATION,
            subject_id=item_id,
            status=unit_status,
            reason_code="scripted",
        )
        for item_id in command.request.watchlist_item_ids
    )


class InMemoryWatcherStore:
    """Thread-safe deterministic store substituting PostgreSQL for this wave."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._schedules: dict[tuple[UUID, UUID | None, str], ScheduledScan] = {}
        self._lineages: dict[UUID, ScanLineage] = {}
        self._attempts: dict[UUID, ScanAttempt] = {}
        self._attempts_by_lineage: dict[UUID, list[UUID]] = {}
        self._source_fetches: dict[UUID, list[SourceFetchAttempt]] = {}
        self._subscription_evals: dict[UUID, list[SubscriptionEvaluationAttempt]] = {}
        self._leases: dict[tuple[UUID, str], WorkerLease] = {}
        self._heartbeats: dict[tuple[UUID, str], WatcherHeartbeat] = {}
        self._policies: dict[tuple[UUID, int], WatcherPolicyVersion] = {}
        self._health: dict[tuple[UUID, str], WatcherHealthSnapshot] = {}
        self._events: list[WatcherObservabilityEvent] = []
        self._lineage_by_scope: dict[tuple[UUID, str], list[UUID]] = {}

    @staticmethod
    def _reject_mismatch(stored_org: UUID, organization_id: UUID, *, record: str) -> None:
        if stored_org != organization_id:
            raise WatcherTenantMismatchError(
                "Watcher record belongs to a different organization.",
                details={"record": record},
            )

    def get_schedule(
        self,
        organization_id: UUID,
        principal_id: UUID | None,
        idempotency_key: str,
    ) -> ScheduledScan | None:
        with self._lock:
            return self._schedules.get((organization_id, principal_id, idempotency_key))

    def insert_schedule(self, row: ScheduledScan) -> ScheduledScan:
        key = (row.organization_id, row.principal_id, row.idempotency_key)
        with self._lock:
            existing = self._schedules.get(key)
            if existing is not None:
                if existing.request_hash != row.request_hash:
                    raise WatcherIdempotencyConflictError(
                        "Idempotency key is bound to a different scan request.",
                        details={
                            "existing_request_hash": existing.request_hash,
                            "incoming_request_hash": row.request_hash,
                        },
                    )
                return existing
            self._schedules[key] = row
            return row

    def get_lineage(self, lineage_id: UUID, organization_id: UUID) -> ScanLineage | None:
        with self._lock:
            current = self._lineages.get(lineage_id)
            if current is None:
                return None
            self._reject_mismatch(current.organization_id, organization_id, record="lineage")
            return current

    def insert_lineage(self, row: ScanLineage) -> ScanLineage:
        with self._lock:
            existing = self._lineages.get(row.lineage_id)
            if existing is not None:
                self._reject_mismatch(
                    existing.organization_id, row.organization_id, record="lineage"
                )
                return existing
            self._lineages[row.lineage_id] = row
            self._lineage_by_scope.setdefault(
                tenant_scope_key(row.organization_id, row.scan_scope), []
            ).append(row.lineage_id)
            return row

    def cas_lineage_terminal(
        self,
        lineage_id: UUID,
        attempt_id: UUID,
        status: str,
        organization_id: UUID,
    ) -> ScanLineage:
        with self._lock:
            current = self._lineages[lineage_id]
            self._reject_mismatch(current.organization_id, organization_id, record="lineage")
            if (
                current.terminal_status is ScanAttemptStatus.SUCCEEDED
                and current.terminal_attempt_id is not None
            ):
                return current
            updated = current.model_copy(
                update={
                    "terminal_attempt_id": attempt_id,
                    "terminal_status": ScanAttemptStatus(status),
                }
            )
            self._lineages[lineage_id] = updated
            return updated

    def list_attempts(self, lineage_id: UUID) -> tuple[ScanAttempt, ...]:
        with self._lock:
            ids = self._attempts_by_lineage.get(lineage_id, [])
            return tuple(self._attempts[item] for item in ids)

    def get_attempt(self, attempt_id: UUID) -> ScanAttempt | None:
        with self._lock:
            return self._attempts.get(attempt_id)

    def insert_attempt(self, row: ScanAttempt) -> ScanAttempt:
        with self._lock:
            self._attempts[row.attempt_id] = row
            self._attempts_by_lineage.setdefault(row.lineage_id, []).append(row.attempt_id)
            return row

    def update_attempt(self, row: ScanAttempt) -> ScanAttempt:
        with self._lock:
            self._attempts[row.attempt_id] = row
            return row

    def insert_source_fetch(self, row: SourceFetchAttempt) -> SourceFetchAttempt:
        with self._lock:
            self._source_fetches.setdefault(row.scan_attempt_id, []).append(row)
            return row

    def list_source_fetches(self, scan_attempt_id: UUID) -> tuple[SourceFetchAttempt, ...]:
        with self._lock:
            return tuple(self._source_fetches.get(scan_attempt_id, ()))

    def insert_subscription_eval(
        self, row: SubscriptionEvaluationAttempt
    ) -> SubscriptionEvaluationAttempt:
        with self._lock:
            self._subscription_evals.setdefault(row.scan_attempt_id, []).append(row)
            return row

    def list_subscription_evals(
        self, scan_attempt_id: UUID
    ) -> tuple[SubscriptionEvaluationAttempt, ...]:
        with self._lock:
            return tuple(self._subscription_evals.get(scan_attempt_id, ()))

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
        key = tenant_scope_key(organization_id, scan_scope)
        with self._lock:
            current = self._leases.get(key)
            if current is not None:
                self._reject_mismatch(current.organization_id, organization_id, record="lease")
            active = (
                current is not None
                and current.owner_id is not None
                and current.expires_at is not None
                and current.expires_at > now
            )
            if active and current is not None:
                if (
                    current.owner_id == owner_id
                    and fencing_token is not None
                    and current.fencing_token == fencing_token
                ):
                    renewed = current.model_copy(
                        update={
                            "renewed_at": now,
                            "expires_at": now + timedelta(seconds=ttl_seconds),
                        }
                    )
                    self._leases[key] = renewed
                    return True, renewed, "renewed"
                return False, current, "lease_held"
            epoch = 1 if current is None else current.lease_epoch + 1
            claimed = WorkerLease(
                scan_scope=scan_scope,
                organization_id=organization_id,
                owner_id=owner_id,
                lease_epoch=epoch,
                fencing_token=epoch,
                acquired_at=now,
                renewed_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
            self._leases[key] = claimed
            return True, claimed, "claimed"

    def renew_lease(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        fencing_token: int,
        ttl_seconds: int,
        now: datetime,
    ) -> bool:
        with self._lock:
            if not self._fence_is_active_unlocked(
                organization_id=organization_id,
                scan_scope=scan_scope,
                owner_id=owner_id,
                fencing_token=fencing_token,
                now=now,
            ):
                return False
            key = tenant_scope_key(organization_id, scan_scope)
            current = self._leases[key]
            renewed = current.model_copy(
                update={
                    "renewed_at": now,
                    "expires_at": now + timedelta(seconds=ttl_seconds),
                }
            )
            self._leases[key] = renewed
            return True

    def release_lease(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> bool:
        with self._lock:
            if not self._fence_is_active_unlocked(
                organization_id=organization_id,
                scan_scope=scan_scope,
                owner_id=owner_id,
                fencing_token=fencing_token,
                now=now,
            ):
                return False
            key = tenant_scope_key(organization_id, scan_scope)
            current = self._leases[key]
            self._leases[key] = current.model_copy(update={"renewed_at": now, "expires_at": now})
            return True

    def get_lease(self, organization_id: UUID, scan_scope: str) -> WorkerLease | None:
        with self._lock:
            current = self._leases.get(tenant_scope_key(organization_id, scan_scope))
            if current is None:
                return None
            self._reject_mismatch(current.organization_id, organization_id, record="lease")
            return current

    def fence_is_active(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> bool:
        with self._lock:
            return self._fence_is_active_unlocked(
                organization_id=organization_id,
                scan_scope=scan_scope,
                owner_id=owner_id,
                fencing_token=fencing_token,
                now=now,
            )

    def _fence_is_active_unlocked(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> bool:
        current = self._leases.get(tenant_scope_key(organization_id, scan_scope))
        if current is None:
            return False
        self._reject_mismatch(current.organization_id, organization_id, record="lease")
        if current.owner_id != owner_id:
            return False
        if current.fencing_token != fencing_token or current.lease_epoch != fencing_token:
            return False
        if current.expires_at is None:
            return False
        return current.expires_at > now

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
    ) -> WatcherHeartbeat:
        key = tenant_scope_key(organization_id, scan_scope)
        beat = WatcherHeartbeat(
            organization_id=organization_id,
            scan_scope=scan_scope,
            owner_id=owner_id,
            lease_epoch=lease_epoch,
            fencing_token=fencing_token,
            last_beat_at=now,
            detail=detail,
        )
        with self._lock:
            existing = self._heartbeats.get(key)
            if existing is not None:
                self._reject_mismatch(existing.organization_id, organization_id, record="heartbeat")
            self._heartbeats[key] = beat
            return beat

    def get_heartbeat(self, organization_id: UUID, scan_scope: str) -> WatcherHeartbeat | None:
        with self._lock:
            current = self._heartbeats.get(tenant_scope_key(organization_id, scan_scope))
            if current is None:
                return None
            self._reject_mismatch(current.organization_id, organization_id, record="heartbeat")
            return current

    def put_policy_version(self, version: WatcherPolicyVersion) -> WatcherPolicyVersion:
        key = (version.identity.policy_id, version.version)
        with self._lock:
            existing = self._policies.get(key)
            if existing is not None:
                if existing.content_hash != version.content_hash:
                    raise WatcherIdempotencyConflictError(
                        "Policy version is immutable and already bound to a different hash.",
                        details={"policy_id": str(version.identity.policy_id)},
                    )
                return existing
            self._policies[key] = version
            return version

    def get_policy_version(self, policy_id: UUID, version: int) -> WatcherPolicyVersion | None:
        with self._lock:
            return self._policies.get((policy_id, version))

    def remember_health(self, snapshot: WatcherHealthSnapshot) -> None:
        key = tenant_scope_key(snapshot.organization_id, snapshot.scan_scope)
        with self._lock:
            existing = self._health.get(key)
            if existing is not None:
                self._reject_mismatch(
                    existing.organization_id, snapshot.organization_id, record="health"
                )
            self._health[key] = snapshot

    def latest_health(self, organization_id: UUID, scan_scope: str) -> WatcherHealthSnapshot | None:
        with self._lock:
            current = self._health.get(tenant_scope_key(organization_id, scan_scope))
            if current is None:
                return None
            self._reject_mismatch(current.organization_id, organization_id, record="health")
            return current

    def latest_attempt_for_scope(
        self, organization_id: UUID, scan_scope: str
    ) -> ScanAttempt | None:
        with self._lock:
            lineage_ids = self._lineage_by_scope.get(
                tenant_scope_key(organization_id, scan_scope), []
            )
            latest: ScanAttempt | None = None
            for lineage_id in lineage_ids:
                lineage = self._lineages[lineage_id]
                self._reject_mismatch(lineage.organization_id, organization_id, record="lineage")
                for attempt_id in self._attempts_by_lineage.get(lineage_id, []):
                    attempt = self._attempts[attempt_id]
                    if latest is None or attempt.started_at >= latest.started_at:
                        latest = attempt
            return latest

    def append_event(self, event: WatcherObservabilityEvent) -> None:
        with self._lock:
            self._events.append(event)

    def events(self) -> tuple[WatcherObservabilityEvent, ...]:
        with self._lock:
            return tuple(self._events)
