"""Cross-instance Binance REST coordination: shared ban deadline and startup lease."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BinanceRestCoordinationRow(Base):
    """One row per REST scope (``binance_usdm``). Written only by the paper worker.

    ``gate_until`` is the latest 418/429 deadline any instance saw; writers keep
    the max. ``lease_*`` is the "binance market reads" lease a new instance waits
    on during a deploy overlap. No request data, no secrets.
    """

    __tablename__ = "binance_rest_coordination"

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    gate_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    gate_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gate_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gate_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gate_updated_by: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    lease_owner: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
