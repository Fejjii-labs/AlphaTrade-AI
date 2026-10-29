"""Worker-published perpetual monitor snapshots. The API reads them; it never ticks."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MarketMonitorSnapshotRow(Base):
    """Latest ``SymbolMonitorSnapshot`` per symbol, written by the paper worker."""

    __tablename__ = "market_monitor_snapshots"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    written_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
