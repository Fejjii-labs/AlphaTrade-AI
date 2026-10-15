"""Additive native facts and scoped stream checkpoints; no execution ledger writes."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BloFinActivityAccount(Base):
    __tablename__ = "blofin_activity_accounts"
    __table_args__ = (
        CheckConstraint("environment = 'demo'", name="ck_blofin_activity_account_demo"),
        CheckConstraint("length(account_uid) > 0", name="ck_blofin_activity_account_uid"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), primary_key=True
    )
    environment: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    credential_binding: Mapped[str] = mapped_column(String(64))
    identity_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    identity_error: Mapped[str | None] = mapped_column(String(80), nullable=True)


def account_fk(name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["organization_id", "environment", "account_uid"],
        [
            f"blofin_activity_accounts.{c}"
            for c in ("organization_id", "environment", "account_uid")
        ],
        name=name,
    )


class BloFinActivityFact(Base):
    __tablename__ = "blofin_activity_facts"
    __table_args__ = (
        account_fk("fk_blofin_activity_fact_scope"),
        CheckConstraint("kind IN ('order', 'fill')", name="ck_blofin_activity_fact_kind"),
        CheckConstraint("length(content_hash) = 64", name="ck_blofin_activity_fact_hash"),
        CheckConstraint("occurred_at_ms >= 0", name="ck_blofin_activity_fact_time"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    environment: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    native_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(128), index=True)
    occurred_at_ms: Mapped[int] = mapped_column(BigInteger, index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    instrument_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    metadata_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BloFinActivityCursor(Base):
    __tablename__ = "blofin_activity_cursors"
    __table_args__ = (
        account_fk("fk_blofin_activity_cursor_scope"),
        CheckConstraint("kind IN ('order', 'fill')", name="ck_blofin_activity_cursor_kind"),
        CheckConstraint(
            "window_begin_ms >= 0 AND window_end_ms >= window_begin_ms",
            name="ck_blofin_activity_cursor_window",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    environment: Mapped[str] = mapped_column(String(16), primary_key=True)
    account_uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    window_begin_ms: Mapped[int] = mapped_column(BigInteger)
    window_end_ms: Mapped[int] = mapped_column(BigInteger)
    native_cursor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    seen_cursors: Mapped[list[str]] = mapped_column(JSON, default=list)
    window_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    covered_begin_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    covered_end_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    gap_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    last_successful_sync: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
