"""Additive experiment identities, frozen versions and append-only facts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class ExperimentRow(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("id", "organization_id", "user_id", name="uq_experiment_tenant"),
        UniqueConstraint("organization_id", "user_id", "idempotency_key"),
        CheckConstraint("latest_version >= 1", name="positive_version"),
    )
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    latest_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExperimentVersionRow(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "experiment_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id", "organization_id", "user_id"],
            ["experiments.id", "experiments.organization_id", "experiments.user_id"],
        ),
        ForeignKeyConstraint(
            ["execution_account_id", "organization_id", "user_id"],
            [
                "execution_accounts.id",
                "execution_accounts.organization_id",
                "execution_accounts.user_id",
            ],
        ),
        *(
            ForeignKeyConstraint(
                [field, "experiment_id", "organization_id", "user_id"],
                [
                    "experiment_versions.id",
                    "experiment_versions.experiment_id",
                    "experiment_versions.organization_id",
                    "experiment_versions.user_id",
                ],
            )
            for field in ("parent_version_id", "promotion_version_id")
        ),
        UniqueConstraint("experiment_id", "version"),
        UniqueConstraint("id", "organization_id", "user_id", name="uq_experiment_version_tenant"),
        UniqueConstraint("id", "experiment_id", "organization_id", "user_id"),
        UniqueConstraint("sample_group_id"),
        CheckConstraint("version >= 1 AND revision >= 0", name="positive_revision"),
        CheckConstraint("length(configuration_hash) = 64", name="configuration_hash"),
        CheckConstraint(
            "state IN ('draft','pending_approval','approved','running',"
            "'paused','completed','promoted')",
            name="state",
        ),
        CheckConstraint(
            "(execution_source = 'blofin_demo' AND native_uid IS NOT NULL) OR "
            "(execution_source = 'internal_simulation' AND native_uid IS NULL)",
            name="source_identity",
        ),
    )
    experiment_id: Mapped[UUID] = mapped_column(Uuid)
    organization_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(Integer)
    parent_version_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    execution_account_id: Mapped[UUID] = mapped_column(Uuid)
    execution_source: Mapped[str] = mapped_column(String(32))
    native_uid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_scope: Mapped[str] = mapped_column(String(160))
    state: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON)
    configuration_hash: Mapped[str] = mapped_column(String(64))
    strategy_content_hashes: Mapped[dict[str, str]] = mapped_column(JSON)
    sample_group_id: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    authorized_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promotion_version_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)


class ExperimentEventRow(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "experiment_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "organization_id", "user_id"],
            [
                "experiment_versions.id",
                "experiment_versions.organization_id",
                "experiment_versions.user_id",
            ],
        ),
        UniqueConstraint("version_id", "revision"),
    )
    version_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    configuration_hash: Mapped[str] = mapped_column(String(64))


class ExperimentSampleRow(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "experiment_samples"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "organization_id", "user_id"],
            [
                "experiment_versions.id",
                "experiment_versions.organization_id",
                "experiment_versions.user_id",
            ],
        ),
        UniqueConstraint("organization_id", "source", "account_scope", "kind", "source_record_id"),
        CheckConstraint("kind IN ('closed_trade','setup_observation')", name="kind"),
        CheckConstraint("source IN ('blofin_demo','internal_simulation')", name="source"),
        CheckConstraint("completed_at >= opened_at", name="sample_time"),
    )
    version_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    organization_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    sample_group_id: Mapped[UUID] = mapped_column(Uuid)
    variant_key: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(32))
    account_scope: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(32))
    source_record_id: Mapped[str] = mapped_column(String(128))
    evidence_hash: Mapped[str] = mapped_column(String(64))
    proof: Mapped[dict[str, Any]] = mapped_column(JSON)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
