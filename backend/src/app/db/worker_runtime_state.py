"""Worker-published effective flags. The API reads this row; it does not arm anything."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

WORKER_EFFECTIVE_COMPONENT = "paper_worker_effective"


class WorkerRuntimeStateRow(Base):
    """One row for the paper worker process.

    Flags are the worker's effective env, not the API process env. The paper
    switch is the organization value the worker consulted. Real trading is not
    a column: this row cannot report it as enabled.
    """

    __tablename__ = "worker_runtime_state"

    component: Mapped[str] = mapped_column(String(32), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_cycle_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    autonomy_mode: Mapped[str] = mapped_column(String(64), nullable=False, default="off")
    autonomous_internal_paper_env_ceiling: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    autonomous_internal_paper_db_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    autonomous_internal_paper_effective: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    watcher_orchestration_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    watcher_paper_staging_activation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    telegram_paper_activation_armed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    telegram_interaction_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    telegram_alerts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    automatic_telegram_delivery_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    watchlist: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
