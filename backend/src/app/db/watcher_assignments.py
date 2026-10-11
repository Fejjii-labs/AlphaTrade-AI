"""Pinned monitoring assignments; no trading or operator activation authority."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WatcherStrategyAssignmentRow(Base):
    __tablename__ = "watcher_strategy_assignments"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "symbol", "strategy_version_id", name="uq_watcher_assignment_version"
        ),
        CheckConstraint("length(version_content_hash) = 64", name="ck_watcher_assignment_hash"),
        Index("ix_watcher_assignments_org_user", "organization_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    strategy_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("user_strategies.id"), nullable=False
    )
    strategy_version_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("user_strategy_versions.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    version_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    timeframes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WatcherAssignmentReceiptRow(Base):
    __tablename__ = "watcher_assignment_receipts"
    __table_args__ = (
        CheckConstraint("length(payload_hash) = 64", name="ck_watcher_assignment_receipt_hash"),
        CheckConstraint(
            "configuration_revision >= 0", name="ck_watcher_assignment_receipt_revision"
        ),
        Index("ix_watcher_assignment_receipts_org_user", "organization_id", "user_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), primary_key=True
    )
    request_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
