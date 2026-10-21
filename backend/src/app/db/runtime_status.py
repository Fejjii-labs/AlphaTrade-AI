"""Observed Watcher and Telegram runtime rows. No secrets and no trading authority."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Wide enough for ``k`` plus a full organization UUID hex (33 characters).
CONTROLLED_RUNTIME_COMPONENT_LENGTH = 64


class ControlledRuntimeStatusRow(Base):
    """One row per process role. The API reads it; it does not arm anything."""

    __tablename__ = "controlled_runtime_status"

    component: Mapped[str] = mapped_column(
        String(CONTROLLED_RUNTIME_COMPONENT_LENGTH), primary_key=True
    )
    worker_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activation_state: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    lease_owner: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fence_held: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_scan_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    market_source: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    freshness_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    telegram_runtime_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="absent"
    )
    inbound_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="off")
    outbox_pending: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outbox_retryable: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outbox_dead_letter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_weight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limited_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    process_rss_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    process_rss_peak_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
