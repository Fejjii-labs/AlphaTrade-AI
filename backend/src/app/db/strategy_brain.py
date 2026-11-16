"""Tenant-scoped setup projections and append-only detector/decision observations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BrainSetupRow(Base):
    __tablename__ = "strategy_brain_setups"
    __table_args__ = (
        Index("ix_brain_org_symbol_time", "organization_id", "symbol", "observed_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    strategy_id: Mapped[UUID] = mapped_column(ForeignKey("user_strategies.id"), nullable=False)
    strategy_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_strategy_versions.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    candidate_id: Mapped[UUID | None] = mapped_column(Uuid)
    assessment_id: Mapped[UUID | None] = mapped_column(Uuid)
    decision_id: Mapped[UUID | None] = mapped_column(Uuid)
    journal_trade_id: Mapped[UUID | None] = mapped_column(Uuid)


class BrainSetupEventRow(Base):
    __tablename__ = "strategy_brain_setup_events"
    __table_args__ = (
        Index("ix_brain_event_setup_time", "organization_id", "setup_id", "occurred_at"),
        Index("ix_brain_event_kind_time", "kind", "occurred_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    setup_id: Mapped[UUID] = mapped_column(ForeignKey("strategy_brain_setups.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
