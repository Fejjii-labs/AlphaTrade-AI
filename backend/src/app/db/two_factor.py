"""Two-factor authentication rows (TOTP enrolment, recovery codes, remembered devices).

Secrets never sit here in plain text: the TOTP secret is AES-GCM ciphertext and
recovery codes are keyed digests (``app.security.two_factor``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserTwoFactorRow(TimestampMixin, Base):
    """One row per user who started TOTP setup.

    ``enabled_at`` is null while setup awaits its first valid code. The failure
    columns implement the per-user lock on code checks (login step, disable,
    regenerate); they are shared so every code check draws on one budget.
    """

    __tablename__ = "user_two_factor"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Highest accepted TOTP time step: a code is never accepted twice.
    last_used_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failure_window_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TwoFactorRecoveryCodeRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single-use recovery code, stored as a keyed digest."""

    __tablename__ = "two_factor_recovery_codes"
    __table_args__ = (Index("ix_two_factor_recovery_codes_user_hash", "user_id", "code_hash"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TwoFactorTrustedDeviceRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A "remember this device" grant. The browser holds a signed token naming this row."""

    __tablename__ = "two_factor_trusted_devices"
    __table_args__ = (Index("ix_two_factor_trusted_devices_user_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TwoFactorUsedPendingTokenRow(Base):
    """A redeemed sign-in pending token (by ``jti``): each one opens one session only.

    Written in the same transaction that holds the ``user_two_factor`` row lock,
    so two concurrent redemptions of one token cannot both succeed (the second
    either sees this row or fails on the primary key). Expired rows are pruned
    per user on the next redemption.
    """

    __tablename__ = "two_factor_used_pending_tokens"
    __table_args__ = (Index("ix_two_factor_used_pending_tokens_user_id", "user_id"),)

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
