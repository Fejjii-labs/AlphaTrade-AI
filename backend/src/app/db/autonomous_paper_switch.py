"""Owner switch for autonomous internal paper. One row per organization."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AutonomousPaperSwitchRow(TimestampMixin, Base):
    """DB half of the paper ceiling. Env false still wins. Real money is not a column."""

    __tablename__ = "autonomous_paper_switches"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
