"""Durable first receipt of existing canonical public-market facts (no tenant owner)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Index, String, Uuid, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.db.base import Base
from app.market_contracts.errors import DuplicateDataError


class PublicMarketObservationRow(Base):
    __tablename__ = "public_market_observations"

    __table_args__ = (
        Index("ix_public_observation_identity_time", "identity_hash", "bar_start"),
        Index("ix_public_observation_bar_start", "bar_start"),
    )

    identity_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    observation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    bar_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ohlcv: Mapped[dict[str, object] | None] = mapped_column(JSON)


@event.listens_for(PublicMarketObservationRow, "before_update")
@event.listens_for(PublicMarketObservationRow, "before_delete")
def immutable_receipt(
    mapper: Mapper[Any], connection: Connection, target: PublicMarketObservationRow
) -> None:
    raise DuplicateDataError("Canonical market receipts are immutable.")
