"""PostgreSQL adapter for the existing WatcherStore contract.

Linearization point for lease claim / fencing: ``SELECT ... FOR UPDATE`` on the
unique ``(organization_id, scan_scope)`` lease row, or the unique insert of that
row. Every worker-authoritative durable write proves organization identity,
scan scope, lease owner, lease epoch, fencing token, and a current non-expired
lease at this boundary. Stale or incomplete authority fails closed with
``StaleFenceError`` and never returns a success-shaped persisted result.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TypeVar
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.watcher_orchestration import (
    WatcherHealthSnapshotRow,
    WatcherHeartbeatRow,
    WatcherObservabilityEventRow,
    WatcherPolicyVersionRow,
    WatcherScanAttemptRow,
    WatcherScanLineageRow,
    WatcherScheduledScanRow,
    WatcherSourceFetchAttemptRow,
    WatcherSubscriptionEvalAttemptRow,
    WatcherWorkerLeaseRow,
)
from app.persistence.unique import is_unique_violation
from app.watcher.contracts import (
    WATCHER_POLICY_VERSION_SCHEMA,
    EvaluationMode,
    RecoveryDisposition,
    ScanAttempt,
    ScanAttemptStatus,
    ScanLineage,
    ScanTrigger,
    ScheduledScan,
    SourceFetchAttempt,
    SubscriptionEvaluationAttempt,
    UnitAttemptStatus,
    WatcherHealthSnapshot,
    WatcherHealthState,
    WatcherHeartbeat,
    WatcherObservabilityEvent,
    WatcherPolicyIdentity,
    WatcherPolicyVersion,
    WorkerLease,
)
from app.watcher.errors import (
    StaleFenceError,
    WatcherIdempotencyConflictError,
    WatcherTenantMismatchError,
)
from app.watcher.hashing import tenant_scope_key

_T = TypeVar("_T")

_SCHEDULE_UNIQUES = (
    "uq_watcher_scheduled_scans_org_key_null_principal",
    "uq_watcher_scheduled_scans_org_principal_key",
)
_POLICY_UNIQUES = (
    "uq_watcher_policy_versions_org_policy_version",
    "uq_watcher_policy_versions_policy_version",
)
_LEASE_UNIQUE = "uq_watcher_worker_leases_org_scope"


class PostgresWatcherStore:
    """Durable WatcherStore. In-memory store remains the unit-test default."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def _run(self, work: Callable[[Session], _T]) -> _T:
        session = self._session_factory()
        try:
            with session.begin():
                return work(session)
        finally:
            session.close()

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
        def work(session: Session) -> ScheduledScan | None:
            row = _load_schedule(session, organization_id, principal_id, idempotency_key)
            return None if row is None else _schedule_from_row(row)

        return self._run(work)

    def insert_schedule(self, row: ScheduledScan) -> ScheduledScan:
        def work(session: Session) -> ScheduledScan:
            existing = _load_schedule(
                session, row.organization_id, row.principal_id, row.idempotency_key
            )
            if existing is not None:
                return _replay_or_conflict_schedule(existing, row)
            try:
                with session.begin_nested():
                    session.add(_schedule_to_row(row))
                    session.flush()
                return row
            except IntegrityError as exc:
                if not is_unique_violation(exc, *_SCHEDULE_UNIQUES):
                    raise
                current = _load_schedule(
                    session, row.organization_id, row.principal_id, row.idempotency_key
                )
                if current is None:
                    raise
                return _replay_or_conflict_schedule(current, row)

        return self._run(work)

    def get_lineage(self, lineage_id: UUID, organization_id: UUID) -> ScanLineage | None:
        def work(session: Session) -> ScanLineage | None:
            current = session.get(WatcherScanLineageRow, lineage_id)
            if current is None:
                return None
            self._reject_mismatch(current.organization_id, organization_id, record="lineage")
            return _lineage_from_row(current)

        return self._run(work)

    def insert_lineage(self, row: ScanLineage) -> ScanLineage:
        def work(session: Session) -> ScanLineage:
            existing = session.get(WatcherScanLineageRow, row.lineage_id)
            if existing is not None:
                self._reject_mismatch(
                    existing.organization_id, row.organization_id, record="lineage"
                )
                return _lineage_from_row(existing)
            session.add(_lineage_to_row(row))
            session.flush()
            return row

        return self._run(work)

    def cas_lineage_terminal(
        self,
        lineage_id: UUID,
        attempt_id: UUID,
        status: str,
        organization_id: UUID,
    ) -> ScanLineage:
        def work(session: Session) -> ScanLineage:
            current = session.get(WatcherScanLineageRow, lineage_id, with_for_update=True)
            if current is None:
                raise KeyError(lineage_id)
            self._reject_mismatch(current.organization_id, organization_id, record="lineage")
            if (
                current.terminal_status == ScanAttemptStatus.SUCCEEDED.value
                and current.terminal_attempt_id is not None
            ):
                return _lineage_from_row(current)
            attempt = session.get(WatcherScanAttemptRow, attempt_id)
            if attempt is None:
                raise KeyError(attempt_id)
            _require_worker_write_authority(
                session,
                organization_id=current.organization_id,
                scan_scope=current.scan_scope,
                worker_id=attempt.worker_id,
                lease_epoch=attempt.lease_epoch,
                fencing_token=attempt.fencing_token,
                now=attempt.finished_at or attempt.heartbeat_at,
                message="Stale fence holder cannot publish terminal lineage state.",
            )
            current.terminal_attempt_id = attempt_id
            current.terminal_status = status
            session.flush()
            return _lineage_from_row(current)

        return self._run(work)

    def list_attempts(self, lineage_id: UUID) -> tuple[ScanAttempt, ...]:
        def work(session: Session) -> tuple[ScanAttempt, ...]:
            rows = session.scalars(
                select(WatcherScanAttemptRow)
                .where(WatcherScanAttemptRow.lineage_id == lineage_id)
                .order_by(WatcherScanAttemptRow.attempt_number)
            ).all()
            return tuple(_attempt_from_row(item) for item in rows)

        return self._run(work)

    def get_attempt(self, attempt_id: UUID) -> ScanAttempt | None:
        def work(session: Session) -> ScanAttempt | None:
            row = session.get(WatcherScanAttemptRow, attempt_id)
            return None if row is None else _attempt_from_row(row)

        return self._run(work)

    def insert_attempt(self, row: ScanAttempt) -> ScanAttempt:
        def work(session: Session) -> ScanAttempt:
            lineage = session.get(WatcherScanLineageRow, row.lineage_id)
            if lineage is None:
                raise KeyError(row.lineage_id)
            _require_worker_write_authority(
                session,
                organization_id=lineage.organization_id,
                scan_scope=lineage.scan_scope,
                worker_id=row.worker_id,
                lease_epoch=row.lease_epoch,
                fencing_token=row.fencing_token,
                now=row.heartbeat_at,
                message="Stale fence holder cannot insert a watcher scan attempt.",
            )
            session.add(_attempt_to_row(row, lineage.organization_id, lineage.scan_scope))
            session.flush()
            return row

        return self._run(work)

    def update_attempt(self, row: ScanAttempt) -> ScanAttempt:
        def work(session: Session) -> ScanAttempt:
            current = session.get(WatcherScanAttemptRow, row.attempt_id, with_for_update=True)
            lineage = session.get(WatcherScanLineageRow, row.lineage_id)
            if lineage is None:
                raise KeyError(row.lineage_id)
            # A worker that lost its lease at Candidate persist must still be able
            # to confess REJECTED_STALE_FENCE on *its own* STARTED attempt. Success
            # and failure outcomes remain fenced so stale holders cannot publish.
            if not _is_own_stale_fence_confession(current, row):
                _require_worker_write_authority(
                    session,
                    organization_id=lineage.organization_id,
                    scan_scope=lineage.scan_scope,
                    worker_id=row.worker_id,
                    lease_epoch=row.lease_epoch,
                    fencing_token=row.fencing_token,
                    now=row.finished_at or row.heartbeat_at,
                    message="Stale fence holder cannot update a watcher scan attempt.",
                )
            if current is None:
                session.add(_attempt_to_row(row, lineage.organization_id, lineage.scan_scope))
            else:
                _apply_attempt(current, row, lineage.organization_id, lineage.scan_scope)
            session.flush()
            return row

        return self._run(work)

    def insert_source_fetch(self, row: SourceFetchAttempt) -> SourceFetchAttempt:
        def work(session: Session) -> SourceFetchAttempt:
            org, scope = _scope_for_attempt(session, row.scan_attempt_id, row.lineage_id)
            worker_id, lease_epoch, fencing_token = _authority_for_unit_write(
                session,
                scan_attempt_id=row.scan_attempt_id,
                lease_epoch=row.lease_epoch,
                fencing_token=row.fencing_token,
            )
            _require_worker_write_authority(
                session,
                organization_id=org,
                scan_scope=scope,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                fencing_token=fencing_token,
                now=row.finished_at or row.created_at,
                message="Stale fence holder cannot insert a watcher source fetch.",
            )
            session.add(_source_to_row(row, org, scope))
            session.flush()
            return row

        return self._run(work)

    def list_source_fetches(self, scan_attempt_id: UUID) -> tuple[SourceFetchAttempt, ...]:
        def work(session: Session) -> tuple[SourceFetchAttempt, ...]:
            rows = session.scalars(
                select(WatcherSourceFetchAttemptRow)
                .where(WatcherSourceFetchAttemptRow.scan_attempt_id == scan_attempt_id)
                .order_by(WatcherSourceFetchAttemptRow.created_at)
            ).all()
            return tuple(_source_from_row(item) for item in rows)

        return self._run(work)

    def insert_subscription_eval(
        self, row: SubscriptionEvaluationAttempt
    ) -> SubscriptionEvaluationAttempt:
        def work(session: Session) -> SubscriptionEvaluationAttempt:
            org, scope = _scope_for_attempt(session, row.scan_attempt_id, row.lineage_id)
            worker_id, lease_epoch, fencing_token = _authority_for_unit_write(
                session,
                scan_attempt_id=row.scan_attempt_id,
                lease_epoch=row.lease_epoch,
                fencing_token=row.fencing_token,
            )
            _require_worker_write_authority(
                session,
                organization_id=org,
                scan_scope=scope,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                fencing_token=fencing_token,
                now=row.finished_at or row.created_at,
                message="Stale fence holder cannot insert a watcher subscription evaluation.",
            )
            session.add(_subscription_to_row(row, org, scope))
            session.flush()
            return row

        return self._run(work)

    def list_subscription_evals(
        self, scan_attempt_id: UUID
    ) -> tuple[SubscriptionEvaluationAttempt, ...]:
        def work(session: Session) -> tuple[SubscriptionEvaluationAttempt, ...]:
            rows = session.scalars(
                select(WatcherSubscriptionEvalAttemptRow)
                .where(WatcherSubscriptionEvalAttemptRow.scan_attempt_id == scan_attempt_id)
                .order_by(WatcherSubscriptionEvalAttemptRow.created_at)
            ).all()
            return tuple(_subscription_from_row(item) for item in rows)

        return self._run(work)

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
        tenant_scope_key(organization_id, scan_scope)

        def work(session: Session) -> tuple[bool, WorkerLease, str]:
            current = _lock_lease(session, organization_id, scan_scope)
            if current is None:
                try:
                    with session.begin_nested():
                        claimed = _new_lease_row(
                            organization_id=organization_id,
                            scan_scope=scan_scope,
                            owner_id=owner_id,
                            epoch=1,
                            now=now,
                            ttl_seconds=ttl_seconds,
                        )
                        session.add(claimed)
                        session.flush()
                    return True, _lease_from_row(claimed), "claimed"
                except IntegrityError as exc:
                    if not is_unique_violation(exc, _LEASE_UNIQUE):
                        raise
                    current = _lock_lease(session, organization_id, scan_scope)
                    if current is None:
                        raise
            self._reject_mismatch(current.organization_id, organization_id, record="lease")
            active = _lease_is_active(current, now)
            if active:
                if (
                    current.owner_id == owner_id
                    and fencing_token is not None
                    and current.fencing_token == fencing_token
                ):
                    current.renewed_at = now
                    current.expires_at = now + timedelta(seconds=ttl_seconds)
                    session.flush()
                    return True, _lease_from_row(current), "renewed"
                return False, _lease_from_row(current), "lease_held"
            epoch = (
                1
                if current.lease_epoch == 0 and current.owner_id is None
                else current.lease_epoch + 1
            )
            current.owner_id = owner_id
            current.lease_epoch = epoch
            current.fencing_token = epoch
            current.acquired_at = now
            current.renewed_at = now
            current.expires_at = now + timedelta(seconds=ttl_seconds)
            session.flush()
            return True, _lease_from_row(current), "claimed"

        return self._run(work)

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
        def work(session: Session) -> bool:
            current = _lock_lease(session, organization_id, scan_scope)
            if current is None:
                return False
            self._reject_mismatch(current.organization_id, organization_id, record="lease")
            if not _fence_matches(current, owner_id, fencing_token, now):
                return False
            current.renewed_at = now
            current.expires_at = now + timedelta(seconds=ttl_seconds)
            session.flush()
            return True

        return self._run(work)

    def release_lease(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> bool:
        def work(session: Session) -> bool:
            current = _lock_lease(session, organization_id, scan_scope)
            if current is None:
                return False
            self._reject_mismatch(current.organization_id, organization_id, record="lease")
            if not _fence_matches(current, owner_id, fencing_token, now):
                return False
            current.renewed_at = now
            current.expires_at = now
            session.flush()
            return True

        return self._run(work)

    def get_lease(self, organization_id: UUID, scan_scope: str) -> WorkerLease | None:
        def work(session: Session) -> WorkerLease | None:
            current = _load_lease(session, organization_id, scan_scope)
            if current is None:
                return None
            self._reject_mismatch(current.organization_id, organization_id, record="lease")
            return _lease_from_row(current)

        return self._run(work)

    def fence_is_active(
        self,
        *,
        organization_id: UUID,
        scan_scope: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> bool:
        def work(session: Session) -> bool:
            current = _load_lease(session, organization_id, scan_scope)
            if current is None:
                return False
            self._reject_mismatch(current.organization_id, organization_id, record="lease")
            return _fence_matches(current, owner_id, fencing_token, now)

        return self._run(work)

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
        beat = WatcherHeartbeat(
            organization_id=organization_id,
            scan_scope=scan_scope,
            owner_id=owner_id,
            lease_epoch=lease_epoch,
            fencing_token=fencing_token,
            last_beat_at=now,
            detail=detail,
        )

        def work(session: Session) -> WatcherHeartbeat:
            lease = _lock_lease(session, organization_id, scan_scope)
            if lease is not None:
                self._reject_mismatch(lease.organization_id, organization_id, record="lease")
            _require_current_lease_authority(
                lease,
                owner_id=owner_id,
                lease_epoch=lease_epoch,
                fencing_token=fencing_token,
                now=now,
                scan_scope=scan_scope,
                message="Stale fence holder cannot record a watcher heartbeat.",
            )
            current = _load_heartbeat(session, organization_id, scan_scope)
            if current is None:
                session.add(_heartbeat_to_row(beat))
            else:
                self._reject_mismatch(current.organization_id, organization_id, record="heartbeat")
                current.owner_id = beat.owner_id
                current.lease_epoch = beat.lease_epoch
                current.fencing_token = beat.fencing_token
                current.last_beat_at = beat.last_beat_at
                current.detail = beat.detail
            session.flush()
            return beat

        return self._run(work)

    def get_heartbeat(self, organization_id: UUID, scan_scope: str) -> WatcherHeartbeat | None:
        def work(session: Session) -> WatcherHeartbeat | None:
            current = _load_heartbeat(session, organization_id, scan_scope)
            if current is None:
                return None
            self._reject_mismatch(current.organization_id, organization_id, record="heartbeat")
            return _heartbeat_from_row(current)

        return self._run(work)

    def put_policy_version(self, version: WatcherPolicyVersion) -> WatcherPolicyVersion:
        def work(session: Session) -> WatcherPolicyVersion:
            existing = session.get(
                WatcherPolicyVersionRow, (version.identity.policy_id, version.version)
            )
            if existing is not None:
                if existing.content_hash != version.content_hash:
                    raise WatcherIdempotencyConflictError(
                        "Policy version is immutable and already bound to a different hash.",
                        details={"policy_id": str(version.identity.policy_id)},
                    )
                return _policy_from_row(existing)
            try:
                with session.begin_nested():
                    session.add(_policy_to_row(version))
                    session.flush()
                return version
            except IntegrityError as exc:
                if not is_unique_violation(exc, *_POLICY_UNIQUES):
                    raise
                current = session.get(
                    WatcherPolicyVersionRow, (version.identity.policy_id, version.version)
                )
                if current is None:
                    raise
                if current.content_hash != version.content_hash:
                    raise WatcherIdempotencyConflictError(
                        "Policy version is immutable and already bound to a different hash.",
                        details={"policy_id": str(version.identity.policy_id)},
                    ) from exc
                return _policy_from_row(current)

        return self._run(work)

    def get_policy_version(self, policy_id: UUID, version: int) -> WatcherPolicyVersion | None:
        def work(session: Session) -> WatcherPolicyVersion | None:
            row = session.get(WatcherPolicyVersionRow, (policy_id, version))
            return None if row is None else _policy_from_row(row)

        return self._run(work)

    def remember_health(self, snapshot: WatcherHealthSnapshot) -> None:
        def work(session: Session) -> None:
            lease = _lock_lease(session, snapshot.organization_id, snapshot.scan_scope)
            if lease is not None:
                self._reject_mismatch(
                    lease.organization_id, snapshot.organization_id, record="lease"
                )
            if _snapshot_claims_worker_authority(snapshot):
                _require_current_lease_authority(
                    lease,
                    owner_id=snapshot.lease_owner,
                    lease_epoch=snapshot.lease_epoch,
                    fencing_token=snapshot.fencing_token,
                    now=snapshot.generated_at,
                    scan_scope=snapshot.scan_scope,
                    message="Stale fence holder cannot publish watcher health.",
                )
            elif _lease_is_active(lease, snapshot.generated_at):
                raise StaleFenceError(
                    "Observer health cannot overwrite an active worker lease.",
                    details={"scan_scope": snapshot.scan_scope, "cause": "observer_vs_active"},
                )
            current = _load_health(session, snapshot.organization_id, snapshot.scan_scope)
            if current is None:
                session.add(_health_to_row(snapshot))
            else:
                self._reject_mismatch(
                    current.organization_id, snapshot.organization_id, record="health"
                )
                _apply_health(current, snapshot)
            session.flush()

        self._run(work)

    def latest_health(self, organization_id: UUID, scan_scope: str) -> WatcherHealthSnapshot | None:
        def work(session: Session) -> WatcherHealthSnapshot | None:
            current = _load_health(session, organization_id, scan_scope)
            if current is None:
                return None
            self._reject_mismatch(current.organization_id, organization_id, record="health")
            return _health_from_row(current)

        return self._run(work)

    def latest_attempt_for_scope(
        self, organization_id: UUID, scan_scope: str
    ) -> ScanAttempt | None:
        def work(session: Session) -> ScanAttempt | None:
            row = session.scalars(
                select(WatcherScanAttemptRow)
                .where(
                    WatcherScanAttemptRow.organization_id == organization_id,
                    WatcherScanAttemptRow.scan_scope == scan_scope,
                )
                .order_by(
                    WatcherScanAttemptRow.started_at.desc(),
                    WatcherScanAttemptRow.attempt_number.desc(),
                )
                .limit(1)
            ).first()
            return None if row is None else _attempt_from_row(row)

        return self._run(work)

    def append_event(self, event: WatcherObservabilityEvent) -> None:
        def work(session: Session) -> None:
            session.add(
                WatcherObservabilityEventRow(
                    event_id=uuid4(),
                    name=event.name,
                    at=event.at,
                    lineage_id=event.lineage_id,
                    attempt_id=event.attempt_id,
                    organization_id=event.organization_id,
                    scan_scope=event.scan_scope,
                    fencing_token=event.fencing_token,
                    fields=dict(event.fields),
                )
            )

        self._run(work)

    def events(self) -> tuple[WatcherObservabilityEvent, ...]:
        def work(session: Session) -> tuple[WatcherObservabilityEvent, ...]:
            rows = session.scalars(
                select(WatcherObservabilityEventRow).order_by(
                    WatcherObservabilityEventRow.at, WatcherObservabilityEventRow.event_id
                )
            ).all()
            return tuple(
                WatcherObservabilityEvent(
                    name=item.name,
                    at=item.at,
                    lineage_id=item.lineage_id,
                    attempt_id=item.attempt_id,
                    organization_id=item.organization_id,
                    scan_scope=item.scan_scope,
                    fencing_token=item.fencing_token,
                    fields=dict(item.fields or {}),
                )
                for item in rows
            )

        return self._run(work)


def _load_schedule(
    session: Session,
    organization_id: UUID,
    principal_id: UUID | None,
    idempotency_key: str,
) -> WatcherScheduledScanRow | None:
    stmt = select(WatcherScheduledScanRow).where(
        WatcherScheduledScanRow.organization_id == organization_id,
        WatcherScheduledScanRow.idempotency_key == idempotency_key,
    )
    if principal_id is None:
        stmt = stmt.where(WatcherScheduledScanRow.principal_id.is_(None))
    else:
        stmt = stmt.where(WatcherScheduledScanRow.principal_id == principal_id)
    return session.scalars(stmt).first()


def _replay_or_conflict_schedule(
    existing: WatcherScheduledScanRow, incoming: ScheduledScan
) -> ScheduledScan:
    if existing.request_hash != incoming.request_hash:
        raise WatcherIdempotencyConflictError(
            "Idempotency key is bound to a different scan request.",
            details={
                "existing_request_hash": existing.request_hash,
                "incoming_request_hash": incoming.request_hash,
            },
        )
    return _schedule_from_row(existing)


def _load_lease(
    session: Session, organization_id: UUID, scan_scope: str
) -> WatcherWorkerLeaseRow | None:
    return session.scalars(
        select(WatcherWorkerLeaseRow).where(
            WatcherWorkerLeaseRow.organization_id == organization_id,
            WatcherWorkerLeaseRow.scan_scope == scan_scope,
        )
    ).first()


def _lock_lease(
    session: Session, organization_id: UUID, scan_scope: str
) -> WatcherWorkerLeaseRow | None:
    return session.scalars(
        select(WatcherWorkerLeaseRow)
        .where(
            WatcherWorkerLeaseRow.organization_id == organization_id,
            WatcherWorkerLeaseRow.scan_scope == scan_scope,
        )
        .with_for_update()
    ).first()


def _load_heartbeat(
    session: Session, organization_id: UUID, scan_scope: str
) -> WatcherHeartbeatRow | None:
    return session.scalars(
        select(WatcherHeartbeatRow).where(
            WatcherHeartbeatRow.organization_id == organization_id,
            WatcherHeartbeatRow.scan_scope == scan_scope,
        )
    ).first()


def _load_health(
    session: Session, organization_id: UUID, scan_scope: str
) -> WatcherHealthSnapshotRow | None:
    return session.scalars(
        select(WatcherHealthSnapshotRow).where(
            WatcherHealthSnapshotRow.organization_id == organization_id,
            WatcherHealthSnapshotRow.scan_scope == scan_scope,
        )
    ).first()


def _lease_is_active(row: WatcherWorkerLeaseRow | None, now: datetime) -> bool:
    return (
        row is not None
        and row.owner_id is not None
        and row.expires_at is not None
        and row.expires_at > now
    )


def _fence_matches(
    row: WatcherWorkerLeaseRow, owner_id: str, fencing_token: int, now: datetime
) -> bool:
    return _lease_authority_matches(
        row,
        owner_id=owner_id,
        lease_epoch=fencing_token,
        fencing_token=fencing_token,
        now=now,
    )


def _lease_authority_matches(
    row: WatcherWorkerLeaseRow,
    *,
    owner_id: str,
    lease_epoch: int,
    fencing_token: int,
    now: datetime,
) -> bool:
    if row.owner_id != owner_id:
        return False
    if row.lease_epoch != lease_epoch:
        return False
    if row.fencing_token != fencing_token:
        return False
    if row.expires_at is None:
        return False
    return row.expires_at > now


def _snapshot_claims_worker_authority(snapshot: WatcherHealthSnapshot) -> bool:
    return (
        snapshot.lease_owner is not None or snapshot.lease_epoch != 0 or snapshot.fencing_token != 0
    )


def _claims_worker_write_authority(
    worker_id: str | None, lease_epoch: int | None, fencing_token: int | None
) -> bool:
    return worker_id is not None or lease_epoch is not None or fencing_token is not None


_STALE_CONFESSION_STATUSES = frozenset(
    {
        ScanAttemptStatus.STARTED.value,
        ScanAttemptStatus.REJECTED_STALE_FENCE.value,
    }
)


def _is_own_stale_fence_confession(current: WatcherScanAttemptRow | None, row: ScanAttempt) -> bool:
    """True when a stale worker is marking its own attempt as rejected_stale_fence."""

    if current is None:
        return False
    if row.status is not ScanAttemptStatus.REJECTED_STALE_FENCE:
        return False
    if current.status not in _STALE_CONFESSION_STATUSES:
        return False
    if current.worker_id != row.worker_id:
        return False
    if current.lease_epoch != row.lease_epoch:
        return False
    if current.fencing_token != row.fencing_token:
        return False
    return _claims_worker_write_authority(row.worker_id, row.lease_epoch, row.fencing_token)


def _authority_for_unit_write(
    session: Session,
    *,
    scan_attempt_id: UUID,
    lease_epoch: int | None,
    fencing_token: int | None,
) -> tuple[str | None, int | None, int | None]:
    parent = session.get(WatcherScanAttemptRow, scan_attempt_id)
    worker_id = None if parent is None else parent.worker_id
    epoch = lease_epoch
    token = fencing_token
    if parent is not None:
        if epoch is None:
            epoch = parent.lease_epoch
        if token is None:
            token = parent.fencing_token
    return worker_id, epoch, token


def _require_worker_write_authority(
    session: Session,
    *,
    organization_id: UUID,
    scan_scope: str,
    worker_id: str | None,
    lease_epoch: int | None,
    fencing_token: int | None,
    now: datetime,
    message: str,
) -> None:
    if not _claims_worker_write_authority(worker_id, lease_epoch, fencing_token):
        return
    if worker_id is None or lease_epoch is None or fencing_token is None:
        raise StaleFenceError(
            message,
            details={"scan_scope": scan_scope, "cause": "incomplete_authority"},
        )
    lease = _lock_lease(session, organization_id, scan_scope)
    _require_current_lease_authority(
        lease,
        owner_id=worker_id,
        lease_epoch=lease_epoch,
        fencing_token=fencing_token,
        now=now,
        scan_scope=scan_scope,
        message=message,
    )


def _require_current_lease_authority(
    lease: WatcherWorkerLeaseRow | None,
    *,
    owner_id: str | None,
    lease_epoch: int,
    fencing_token: int,
    now: datetime,
    scan_scope: str,
    message: str,
) -> None:
    if lease is None:
        raise StaleFenceError(
            message,
            details={"scan_scope": scan_scope, "cause": "missing_lease"},
        )
    if owner_id is None:
        raise StaleFenceError(
            message,
            details={"scan_scope": scan_scope, "cause": "missing_owner"},
        )
    if not _lease_authority_matches(
        lease,
        owner_id=owner_id,
        lease_epoch=lease_epoch,
        fencing_token=fencing_token,
        now=now,
    ):
        cause = "expired_lease" if not _lease_is_active(lease, now) else "mismatched_authority"
        raise StaleFenceError(
            message,
            details={"scan_scope": scan_scope, "cause": cause},
        )


def _new_lease_row(
    *,
    organization_id: UUID,
    scan_scope: str,
    owner_id: str,
    epoch: int,
    now: datetime,
    ttl_seconds: int,
) -> WatcherWorkerLeaseRow:
    return WatcherWorkerLeaseRow(
        id=uuid4(),
        organization_id=organization_id,
        scan_scope=scan_scope,
        owner_id=owner_id,
        lease_epoch=epoch,
        fencing_token=epoch,
        acquired_at=now,
        renewed_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )


def _scope_for_attempt(
    session: Session, scan_attempt_id: UUID, lineage_id: UUID
) -> tuple[UUID, str]:
    attempt = session.get(WatcherScanAttemptRow, scan_attempt_id)
    if attempt is not None:
        return attempt.organization_id, attempt.scan_scope
    lineage = session.get(WatcherScanLineageRow, lineage_id)
    if lineage is None:
        raise KeyError(lineage_id)
    return lineage.organization_id, lineage.scan_scope


def _schedule_to_row(row: ScheduledScan) -> WatcherScheduledScanRow:
    return WatcherScheduledScanRow(
        scheduled_scan_id=row.scheduled_scan_id,
        organization_id=row.organization_id,
        principal_id=row.principal_id,
        idempotency_key=row.idempotency_key,
        request_hash=row.request_hash,
        scan_scope=row.scan_scope,
        lineage_id=row.lineage_id,
        created_at=row.created_at,
    )


def _schedule_from_row(row: WatcherScheduledScanRow) -> ScheduledScan:
    return ScheduledScan(
        scheduled_scan_id=row.scheduled_scan_id,
        organization_id=row.organization_id,
        principal_id=row.principal_id,
        idempotency_key=row.idempotency_key,
        request_hash=row.request_hash,
        scan_scope=row.scan_scope,
        lineage_id=row.lineage_id,
        created_at=row.created_at,
    )


def _lineage_to_row(row: ScanLineage) -> WatcherScanLineageRow:
    return WatcherScanLineageRow(
        lineage_id=row.lineage_id,
        organization_id=row.organization_id,
        principal_id=row.principal_id,
        scan_scope=row.scan_scope,
        request_hash=row.request_hash,
        policy_id=row.policy_id,
        policy_version=row.policy_version,
        policy_content_hash=row.policy_content_hash,
        trigger_created_by=row.trigger_created_by.value,
        created_at=row.created_at,
        terminal_attempt_id=row.terminal_attempt_id,
        terminal_status=None if row.terminal_status is None else row.terminal_status.value,
    )


def _lineage_from_row(row: WatcherScanLineageRow) -> ScanLineage:
    return ScanLineage(
        lineage_id=row.lineage_id,
        organization_id=row.organization_id,
        principal_id=row.principal_id,
        scan_scope=row.scan_scope,
        request_hash=row.request_hash,
        policy_id=row.policy_id,
        policy_version=row.policy_version,
        policy_content_hash=row.policy_content_hash,
        trigger_created_by=ScanTrigger(row.trigger_created_by),
        created_at=row.created_at,
        terminal_attempt_id=row.terminal_attempt_id,
        terminal_status=None
        if row.terminal_status is None
        else ScanAttemptStatus(row.terminal_status),
    )


def _attempt_to_row(
    row: ScanAttempt, organization_id: UUID, scan_scope: str
) -> WatcherScanAttemptRow:
    return WatcherScanAttemptRow(
        attempt_id=row.attempt_id,
        lineage_id=row.lineage_id,
        organization_id=organization_id,
        scan_scope=scan_scope,
        attempt_number=row.attempt_number,
        worker_id=row.worker_id,
        lease_epoch=row.lease_epoch,
        fencing_token=row.fencing_token,
        trigger=row.trigger.value,
        mode=row.mode.value,
        status=row.status.value,
        started_at=row.started_at,
        heartbeat_at=row.heartbeat_at,
        finished_at=row.finished_at,
        sanitized_error=row.sanitized_error,
        recovery_disposition=row.recovery_disposition.value,
        recovered_from_attempt_id=row.recovered_from_attempt_id,
        outcome_reason_code=row.outcome_reason_code,
        evaluation_input_hash=row.evaluation_input_hash,
    )


def _apply_attempt(
    current: WatcherScanAttemptRow,
    row: ScanAttempt,
    organization_id: UUID,
    scan_scope: str,
) -> None:
    current.lineage_id = row.lineage_id
    current.organization_id = organization_id
    current.scan_scope = scan_scope
    current.attempt_number = row.attempt_number
    current.worker_id = row.worker_id
    current.lease_epoch = row.lease_epoch
    current.fencing_token = row.fencing_token
    current.trigger = row.trigger.value
    current.mode = row.mode.value
    current.status = row.status.value
    current.started_at = row.started_at
    current.heartbeat_at = row.heartbeat_at
    current.finished_at = row.finished_at
    current.sanitized_error = row.sanitized_error
    current.recovery_disposition = row.recovery_disposition.value
    current.recovered_from_attempt_id = row.recovered_from_attempt_id
    current.outcome_reason_code = row.outcome_reason_code
    current.evaluation_input_hash = row.evaluation_input_hash


def _attempt_from_row(row: WatcherScanAttemptRow) -> ScanAttempt:
    return ScanAttempt(
        attempt_id=row.attempt_id,
        lineage_id=row.lineage_id,
        attempt_number=row.attempt_number,
        worker_id=row.worker_id,
        lease_epoch=row.lease_epoch,
        fencing_token=row.fencing_token,
        trigger=ScanTrigger(row.trigger),
        mode=EvaluationMode(row.mode),
        status=ScanAttemptStatus(row.status),
        started_at=row.started_at,
        heartbeat_at=row.heartbeat_at,
        finished_at=row.finished_at,
        sanitized_error=row.sanitized_error,
        recovery_disposition=RecoveryDisposition(row.recovery_disposition),
        recovered_from_attempt_id=row.recovered_from_attempt_id,
        outcome_reason_code=row.outcome_reason_code,
        evaluation_input_hash=row.evaluation_input_hash,
    )


def _source_to_row(
    row: SourceFetchAttempt, organization_id: UUID, scan_scope: str
) -> WatcherSourceFetchAttemptRow:
    return WatcherSourceFetchAttemptRow(
        fetch_attempt_id=row.fetch_attempt_id,
        scan_attempt_id=row.scan_attempt_id,
        lineage_id=row.lineage_id,
        organization_id=organization_id,
        scan_scope=scan_scope,
        lease_epoch=row.lease_epoch,
        fencing_token=row.fencing_token,
        subject_id=row.subject_id,
        status=row.status.value,
        reason_code=row.reason_code,
        source_freshness_token=row.source_freshness_token,
        created_at=row.created_at,
        finished_at=row.finished_at,
        sanitized_error=row.sanitized_error,
    )


def _source_from_row(row: WatcherSourceFetchAttemptRow) -> SourceFetchAttempt:
    return SourceFetchAttempt(
        fetch_attempt_id=row.fetch_attempt_id,
        scan_attempt_id=row.scan_attempt_id,
        lineage_id=row.lineage_id,
        lease_epoch=row.lease_epoch,
        fencing_token=row.fencing_token,
        subject_id=row.subject_id,
        status=UnitAttemptStatus(row.status),
        reason_code=row.reason_code,
        source_freshness_token=row.source_freshness_token,
        created_at=row.created_at,
        finished_at=row.finished_at,
        sanitized_error=row.sanitized_error,
    )


def _subscription_to_row(
    row: SubscriptionEvaluationAttempt, organization_id: UUID, scan_scope: str
) -> WatcherSubscriptionEvalAttemptRow:
    return WatcherSubscriptionEvalAttemptRow(
        subscription_attempt_id=row.subscription_attempt_id,
        scan_attempt_id=row.scan_attempt_id,
        lineage_id=row.lineage_id,
        organization_id=organization_id,
        scan_scope=scan_scope,
        lease_epoch=row.lease_epoch,
        fencing_token=row.fencing_token,
        subject_id=row.subject_id,
        status=row.status.value,
        reason_code=row.reason_code,
        evidence_validity_token=row.evidence_validity_token,
        created_at=row.created_at,
        finished_at=row.finished_at,
        sanitized_error=row.sanitized_error,
    )


def _subscription_from_row(row: WatcherSubscriptionEvalAttemptRow) -> SubscriptionEvaluationAttempt:
    return SubscriptionEvaluationAttempt(
        subscription_attempt_id=row.subscription_attempt_id,
        scan_attempt_id=row.scan_attempt_id,
        lineage_id=row.lineage_id,
        lease_epoch=row.lease_epoch,
        fencing_token=row.fencing_token,
        subject_id=row.subject_id,
        status=UnitAttemptStatus(row.status),
        reason_code=row.reason_code,
        evidence_validity_token=row.evidence_validity_token,
        created_at=row.created_at,
        finished_at=row.finished_at,
        sanitized_error=row.sanitized_error,
    )


def _lease_from_row(row: WatcherWorkerLeaseRow) -> WorkerLease:
    return WorkerLease(
        scan_scope=row.scan_scope,
        organization_id=row.organization_id,
        owner_id=row.owner_id,
        lease_epoch=row.lease_epoch,
        fencing_token=row.fencing_token,
        acquired_at=row.acquired_at,
        renewed_at=row.renewed_at,
        expires_at=row.expires_at,
    )


def _heartbeat_to_row(row: WatcherHeartbeat) -> WatcherHeartbeatRow:
    return WatcherHeartbeatRow(
        id=uuid4(),
        organization_id=row.organization_id,
        scan_scope=row.scan_scope,
        owner_id=row.owner_id,
        lease_epoch=row.lease_epoch,
        fencing_token=row.fencing_token,
        last_beat_at=row.last_beat_at,
        detail=row.detail,
    )


def _heartbeat_from_row(row: WatcherHeartbeatRow) -> WatcherHeartbeat:
    return WatcherHeartbeat(
        organization_id=row.organization_id,
        scan_scope=row.scan_scope,
        owner_id=row.owner_id,
        lease_epoch=row.lease_epoch,
        fencing_token=row.fencing_token,
        last_beat_at=row.last_beat_at,
        detail=row.detail,
    )


def _health_to_row(row: WatcherHealthSnapshot) -> WatcherHealthSnapshotRow:
    return WatcherHealthSnapshotRow(
        id=uuid4(),
        organization_id=row.organization_id,
        scan_scope=row.scan_scope,
        state=row.state.value,
        enabled=row.enabled,
        lease_owner=row.lease_owner,
        lease_epoch=row.lease_epoch,
        fencing_token=row.fencing_token,
        lease_expires_at=row.lease_expires_at,
        last_beat_at=row.last_beat_at,
        seconds_since_beat=row.seconds_since_beat,
        last_attempt_status=(
            None if row.last_attempt_status is None else row.last_attempt_status.value
        ),
        last_lineage_id=row.last_lineage_id,
        reason_code=row.reason_code,
        generated_at=row.generated_at,
    )


def _apply_health(current: WatcherHealthSnapshotRow, row: WatcherHealthSnapshot) -> None:
    current.state = row.state.value
    current.enabled = row.enabled
    current.lease_owner = row.lease_owner
    current.lease_epoch = row.lease_epoch
    current.fencing_token = row.fencing_token
    current.lease_expires_at = row.lease_expires_at
    current.last_beat_at = row.last_beat_at
    current.seconds_since_beat = row.seconds_since_beat
    current.last_attempt_status = (
        None if row.last_attempt_status is None else row.last_attempt_status.value
    )
    current.last_lineage_id = row.last_lineage_id
    current.reason_code = row.reason_code
    current.generated_at = row.generated_at


def _health_from_row(row: WatcherHealthSnapshotRow) -> WatcherHealthSnapshot:
    return WatcherHealthSnapshot(
        state=WatcherHealthState(row.state),
        organization_id=row.organization_id,
        scan_scope=row.scan_scope,
        enabled=row.enabled,
        lease_owner=row.lease_owner,
        lease_epoch=row.lease_epoch,
        fencing_token=row.fencing_token,
        lease_expires_at=row.lease_expires_at,
        last_beat_at=row.last_beat_at,
        seconds_since_beat=row.seconds_since_beat,
        last_attempt_status=None
        if row.last_attempt_status is None
        else ScanAttemptStatus(row.last_attempt_status),
        last_lineage_id=row.last_lineage_id,
        reason_code=row.reason_code,
        generated_at=row.generated_at,
    )


def _policy_to_row(version: WatcherPolicyVersion) -> WatcherPolicyVersionRow:
    return WatcherPolicyVersionRow(
        policy_id=version.identity.policy_id,
        version=version.version,
        organization_id=version.identity.organization_id,
        user_id=version.identity.user_id,
        watchlist_item_id=version.identity.watchlist_item_id,
        schema_version=version.schema_version,
        timeframe=version.timeframe,
        strategy_version_id=version.strategy_version_id,
        setup_definition_id=version.setup_definition_id,
        fusion_policy_version=version.fusion_policy_version,
        alert_threshold=version.alert_threshold,
        delivery_policy_id=version.delivery_policy_id,
        enabled=version.enabled,
        created_by=version.created_by,
        confirmed_by=version.confirmed_by,
        created_at=version.created_at,
        content_hash=version.content_hash,
    )


def _policy_from_row(row: WatcherPolicyVersionRow) -> WatcherPolicyVersion:
    return WatcherPolicyVersion(
        schema_version=WATCHER_POLICY_VERSION_SCHEMA,
        identity=WatcherPolicyIdentity(
            policy_id=row.policy_id,
            organization_id=row.organization_id,
            user_id=row.user_id,
            watchlist_item_id=row.watchlist_item_id,
        ),
        version=row.version,
        timeframe=row.timeframe,
        strategy_version_id=row.strategy_version_id,
        setup_definition_id=row.setup_definition_id,
        fusion_policy_version=row.fusion_policy_version,
        alert_threshold=row.alert_threshold,
        delivery_policy_id=row.delivery_policy_id,
        enabled=row.enabled,
        created_by=row.created_by,
        confirmed_by=row.confirmed_by,
        created_at=row.created_at,
        content_hash=row.content_hash,
    )
