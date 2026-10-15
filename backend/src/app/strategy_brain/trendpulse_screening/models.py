"""Append-only receipt snapshots and database-enforced natural signal uniqueness."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    event,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, mapped_column

from app.core.errors import ConflictError
from app.db.base import Base, UUIDPrimaryKeyMixin


class TrendPulseScreeningRow(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "trendpulse_screening_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["version_id", "experiment_id", "organization_id", "user_id"],
            [
                "experiment_versions.id",
                "experiment_versions.experiment_id",
                "experiment_versions.organization_id",
                "experiment_versions.user_id",
            ],
        ),
        UniqueConstraint("id", "version_id", "organization_id", "user_id"),
        ForeignKeyConstraint(
            ["duplicate_of", "version_id", "organization_id", "user_id"],
            [
                "trendpulse_screening_runs.id",
                "trendpulse_screening_runs.version_id",
                "trendpulse_screening_runs.organization_id",
                "trendpulse_screening_runs.user_id",
            ],
        ),
        UniqueConstraint("organization_id", "user_id", "request_id"),
        UniqueConstraint("version_id", "variant_key", "dedupe_key"),
        Index(
            "ix_trendpulse_screening_read",
            "organization_id",
            "user_id",
            "version_id",
            "decision_at",
        ),
        CheckConstraint("decision_at >= acquisition_started_at", name="decision_time"),
        CheckConstraint(
            "trend_receipts BETWEEN 0 AND 252 AND entry_receipts BETWEEN 0 AND 62",
            name="receipt_bounds",
        ),
        CheckConstraint("evidence_mode IN ('public_rest','replay')", name="evidence_mode"),
        CheckConstraint(
            "(evidence_mode='public_rest' AND receipt_provenance='live_public_rest') OR "
            "(evidence_mode='replay' AND receipt_provenance IN "
            "('recorded_public_receipts','synthetic_fixture'))",
            name="receipt_provenance",
        ),
        CheckConstraint(
            "status IN ('unavailable','refused','no_setup',"
            "'qualified_research_signal','duplicate')",
            name="status",
        ),
        CheckConstraint(
            "(status = 'qualified_research_signal' AND dedupe_key IS NOT NULL "
            "AND signal_id IS NOT NULL AND signal IS NOT NULL AND duplicate_of IS NULL) OR "
            "(status = 'duplicate' AND dedupe_key IS NULL AND signal_id IS NOT NULL "
            "AND duplicate_of IS NOT NULL AND signal IS NULL) OR "
            "(status IN ('unavailable','refused','no_setup') AND dedupe_key IS NULL "
            "AND signal_id IS NULL AND duplicate_of IS NULL AND signal IS NULL)",
            name="signal_status",
        ),
    )
    organization_id: Mapped[UUID] = mapped_column(Uuid)
    user_id: Mapped[UUID] = mapped_column(Uuid)
    experiment_id: Mapped[UUID] = mapped_column(Uuid)
    version_id: Mapped[UUID] = mapped_column(Uuid)
    request_id: Mapped[UUID] = mapped_column(Uuid)
    request_hash: Mapped[str] = mapped_column(String(64))
    configuration_hash: Mapped[str] = mapped_column(String(64))
    variant_key: Mapped[str] = mapped_column(String(40))
    strategy_version_id: Mapped[UUID] = mapped_column(Uuid)
    strategy_content_hash: Mapped[str] = mapped_column(String(64))
    trigger_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acquisition_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence_mode: Mapped[str] = mapped_column(String(20))
    receipt_provenance: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(100))
    trend_receipts: Mapped[int] = mapped_column(Integer)
    entry_receipts: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON)
    evidence_hash: Mapped[str] = mapped_column(String(64))
    signal: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    signal_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duplicate_of: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)


def _immutable(*_args: Any) -> None:
    raise ConflictError("Screening receipt history is append-only.", code="screening_immutable")


event.listen(TrendPulseScreeningRow, "before_update", _immutable)
event.listen(TrendPulseScreeningRow, "before_delete", _immutable)

# Generated migration freezes these statements; fixtures install the same guards.
SCREENING_GUARD_SQL = (
    "CREATE FUNCTION trendpulse_screening_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ "
    "BEGIN RAISE EXCEPTION 'screening_immutable'; END; $$",
    "CREATE TRIGGER trendpulse_screening_guard BEFORE UPDATE OR DELETE ON "
    "trendpulse_screening_runs FOR EACH ROW EXECUTE FUNCTION trendpulse_screening_immutable()",
)


def _install_guards(_target: Any, connection: Connection, **_kwargs: Any) -> None:
    if connection.dialect.name == "postgresql":
        for statement in SCREENING_GUARD_SQL:
            connection.execute(text(statement))


event.listen(TrendPulseScreeningRow.__table__, "after_create", _install_guards)
