"""Server-owned reservations for bounded interactive voice sessions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VoiceSessionReservation(Base):
    __tablename__ = "voice_session_reservations"
    __table_args__ = (
        CheckConstraint("reserved_cost >= 0", name="voice_reserved_cost"),
        CheckConstraint(
            "accounted_cost IS NULL OR accounted_cost >= 0", name="voice_accounted_cost"
        ),
        CheckConstraint("response_count >= 0", name="voice_response_count"),
        CheckConstraint("reserved_tokens >= 0", name="voice_reserved_tokens"),
        CheckConstraint("status IN ('pending','active','closed','uncertain')", name="voice_status"),
        Index("ix_voice_session_budget", "organization_id", "budget_reference"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    conversation_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id"), nullable=True
    )
    budget_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reserved_cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    accounted_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    response_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ledger: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
