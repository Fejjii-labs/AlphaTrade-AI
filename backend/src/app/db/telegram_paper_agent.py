"""ORM models for paper Telegram notification/thread durability.

Delivery state remains on telegram_security_outbox. These tables recover
notification identity, discussion threads, and presented confirmations.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelegramPaperNotificationRow(Base):
    __tablename__ = "telegram_paper_notifications"
    __table_args__ = (
        UniqueConstraint("identity_hash", name="uq_tg_paper_notify_identity"),
        UniqueConstraint(
            "organization_id",
            "intent_id",
            name="uq_tg_paper_notify_org_intent",
        ),
        Index("ix_tg_paper_notify_created_at", "created_at"),
    )

    intent_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_revision_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    watcher_lineage_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    watcher_reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TelegramPaperThreadRow(Base):
    __tablename__ = "telegram_paper_threads"
    __table_args__ = (
        Index(
            "uq_tg_paper_thread_scope_resource",
            "organization_id",
            "binding_id",
            "resource_type",
            "resource_id",
            unique=True,
            postgresql_where=text("resource_id IS NOT NULL"),
        ),
        Index(
            "uq_tg_paper_thread_scope_null_resource",
            "organization_id",
            "binding_id",
            "resource_type",
            unique=True,
            postgresql_where=text("resource_id IS NULL"),
        ),
    )

    thread_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    binding_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    bot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    notification_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TelegramPaperMessageRow(Base):
    __tablename__ = "telegram_paper_messages"

    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("telegram_paper_threads.thread_id"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    intent: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TelegramPaperConfirmationRow(Base):
    __tablename__ = "telegram_paper_confirmations"

    confirmation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("telegram_paper_threads.thread_id"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revision_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    presented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
