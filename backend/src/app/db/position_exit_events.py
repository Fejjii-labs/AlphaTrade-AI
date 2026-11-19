"""ORM for partial-exit records: reduction fills and protective stop moves.

Append-only and keyed per position: one row per (organization, execution
command, venue, event key). A replay of the same key converges; different
content under the same key is refused by the repository before any write.
This table is not a JournalTrade writer; the final close still goes through
the paper close service.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_NUMERIC = Numeric(36, 18)


class PositionExitEventRow(Base):
    __tablename__ = "position_exit_events"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "execution_lifecycle_id",
            "venue",
            "event_key",
            name="uq_position_exit_events_key",
        ),
        CheckConstraint(
            "venue IN ('PAPER_INTERNAL', 'BLOFIN_DEMO')",
            name="position_exit_events_venue",
        ),
        CheckConstraint(
            "(event_kind = 'fill' AND fill_kind IN ('target', 'stop', 'runner_stop')"
            " AND quantity > 0 AND price > 0)"
            " OR (event_kind = 'stop_move' AND stop_reason IN ('breakeven', 'trail')"
            " AND previous_stop > 0 AND new_stop > 0 AND tightened IS NOT NULL)",
            name="position_exit_events_shape",
        ),
        Index(
            "ix_position_exit_events_position",
            "organization_id",
            "execution_lifecycle_id",
            "venue",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    execution_lifecycle_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    venue: Mapped[str] = mapped_column(String(40), nullable=False)
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    event_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    fill_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    leg_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(_NUMERIC, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(_NUMERIC, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)
    previous_stop: Mapped[Decimal | None] = mapped_column(_NUMERIC, nullable=True)
    new_stop: Mapped[Decimal | None] = mapped_column(_NUMERIC, nullable=True)
    tightened: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
