"""Compact, kept-forever summary of each trade candidate (data retention policy).

Written by the nightly retention pass before a candidate's full rows are pruned,
so taken and missed setups stay comparable after the 7-day window. One row per
canonical candidate; the row is a measurement, never execution authority.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Index, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_PRICE = Numeric(28, 12)


class TradeCandidateSummaryRow(Base):
    __tablename__ = "trade_candidate_summaries"
    __table_args__ = (
        Index("ix_trade_candidate_summaries_org_detected", "organization_id", "detected_at"),
        Index(
            "ix_trade_candidate_summaries_org_strategy",
            "organization_id",
            "strategy_version_id",
        ),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    symbol: Mapped[str] = mapped_column(String(80), nullable=False)
    venue: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timeframe: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    setup_definition_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    brain_setup_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    final_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    planned_entry: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    planned_stop: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    planned_target: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    taken: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    journal_trade_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    # win / loss / breakeven (taken, closed trade) or target_first / stop_first /
    # ambiguous / neither (missed, from stored candles). NULL when not derivable.
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # journal_trade / trade_open / candles / unavailable
    outcome_source: Mapped[str] = mapped_column(String(32), nullable=False)
    realized_net_pnl: Mapped[Decimal | None] = mapped_column(_PRICE, nullable=True)
    summarized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
