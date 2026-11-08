"""ORM models for watcher orchestration durable persistence.

These tables bind the existing WatcherStore contract. They are not wired into
the live worker. Tenant identity is ``organization_id`` plus ``scan_scope``;
``scan_scope`` is never a tenant key by itself.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WatcherPolicyVersionRow(Base):
    __tablename__ = "watcher_policy_versions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "policy_id",
            "version",
            name="uq_watcher_policy_versions_org_policy_version",
        ),
        UniqueConstraint(
            "policy_id",
            "version",
            name="uq_watcher_policy_versions_policy_version",
        ),
        CheckConstraint("version >= 1", name="watcher_policy_version_min"),
        CheckConstraint("length(content_hash) = 64", name="watcher_policy_hash_len"),
    )

    policy_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    watchlist_item_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    setup_definition_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    fusion_policy_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    alert_threshold: Mapped[str | None] = mapped_column(String(128), nullable=True)
    delivery_policy_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class WatcherScanLineageRow(Base):
    __tablename__ = "watcher_scan_lineages"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "lineage_id",
            name="uq_watcher_scan_lineages_org_lineage",
        ),
        Index(
            "ix_watcher_scan_lineages_org_scope",
            "organization_id",
            "scan_scope",
        ),
    )

    lineage_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    principal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    scan_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_created_by: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminal_attempt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    terminal_status: Mapped[str | None] = mapped_column(String(32), nullable=True)


class WatcherScheduledScanRow(Base):
    __tablename__ = "watcher_scheduled_scans"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "scheduled_scan_id",
            name="uq_watcher_scheduled_scans_org_id",
        ),
        Index(
            "uq_watcher_scheduled_scans_org_key_null_principal",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("principal_id IS NULL"),
        ),
        Index(
            "uq_watcher_scheduled_scans_org_principal_key",
            "organization_id",
            "principal_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("principal_id IS NOT NULL"),
        ),
        Index(
            "ix_watcher_scheduled_scans_org_scope",
            "organization_id",
            "scan_scope",
        ),
    )

    scheduled_scan_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    principal_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    lineage_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("watcher_scan_lineages.lineage_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WatcherScanAttemptRow(Base):
    __tablename__ = "watcher_scan_attempts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "attempt_id",
            name="uq_watcher_scan_attempts_org_attempt",
        ),
        UniqueConstraint(
            "organization_id",
            "lineage_id",
            "attempt_number",
            name="uq_watcher_scan_attempts_org_lineage_number",
        ),
        Index(
            "ix_watcher_scan_attempts_org_scope_started",
            "organization_id",
            "scan_scope",
            "started_at",
        ),
        CheckConstraint("attempt_number >= 1", name="watcher_attempt_number_min"),
    )

    attempt_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    lineage_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("watcher_scan_lineages.lineage_id"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scan_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fencing_token: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sanitized_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recovery_disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    recovered_from_attempt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    outcome_reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evaluation_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WatcherSourceFetchAttemptRow(Base):
    __tablename__ = "watcher_source_fetch_attempts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "fetch_attempt_id",
            name="uq_watcher_source_fetch_org_id",
        ),
        Index("ix_watcher_source_fetch_scan_attempt", "scan_attempt_id"),
    )

    fetch_attempt_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    scan_attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("watcher_scan_attempts.attempt_id"), nullable=False
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scan_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    lease_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fencing_token: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_freshness_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sanitized_error: Mapped[str | None] = mapped_column(String(255), nullable=True)


class WatcherSubscriptionEvalAttemptRow(Base):
    __tablename__ = "watcher_subscription_eval_attempts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "subscription_attempt_id",
            name="uq_watcher_sub_eval_org_id",
        ),
        Index("ix_watcher_sub_eval_scan_attempt", "scan_attempt_id"),
    )

    subscription_attempt_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    scan_attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("watcher_scan_attempts.attempt_id"), nullable=False
    )
    lineage_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scan_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    lease_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fencing_token: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evidence_validity_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sanitized_error: Mapped[str | None] = mapped_column(String(255), nullable=True)


class WatcherWorkerLeaseRow(Base):
    """Lease linearization key is ``(organization_id, scan_scope)``."""

    __tablename__ = "watcher_worker_leases"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "scan_scope",
            name="uq_watcher_worker_leases_org_scope",
        ),
        CheckConstraint("lease_epoch >= 0", name="watcher_lease_epoch_min"),
        CheckConstraint("fencing_token >= 0", name="watcher_lease_fence_min"),
        CheckConstraint("fencing_token = lease_epoch", name="watcher_lease_fence_equals_epoch"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scan_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False)
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WatcherHeartbeatRow(Base):
    __tablename__ = "watcher_heartbeats"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "scan_scope",
            name="uq_watcher_heartbeats_org_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scan_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False)
    last_beat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(255), nullable=True)


class WatcherHealthSnapshotRow(Base):
    __tablename__ = "watcher_health_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "scan_scope",
            name="uq_watcher_health_snapshots_org_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scan_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_beat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seconds_since_beat: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_attempt_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_lineage_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WatcherObservabilityEventRow(Base):
    __tablename__ = "watcher_observability_events"
    __table_args__ = (
        Index(
            "ix_watcher_observability_events_org_scope",
            "organization_id",
            "scan_scope",
        ),
        Index("ix_watcher_observability_events_at", "at"),
        Index("ix_watcher_observability_events_org_at", "organization_id", "at"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lineage_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    scan_scope: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fencing_token: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fields: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
