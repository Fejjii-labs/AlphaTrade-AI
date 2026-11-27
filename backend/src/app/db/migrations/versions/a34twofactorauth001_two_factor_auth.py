"""two-factor authentication: TOTP enrolment, recovery codes, remembered devices

Three new tables; no existing table changes. The TOTP secret is stored as
AES-GCM ciphertext and recovery codes as keyed digests (app.security.two_factor).

Revision ID: a34twofactorauth001
Revises: a33auditlogresourceidx001
Create Date: 2026-11-27 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a34twofactorauth001"
down_revision: str | None = "a33auditlogresourceidx001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "user_two_factor",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_step", sa.BigInteger(), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_window_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_two_factor_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_two_factor"),
    )
    op.create_table(
        "two_factor_recovery_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_two_factor_recovery_codes_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_two_factor_recovery_codes"),
    )
    op.create_index(
        "ix_two_factor_recovery_codes_user_hash",
        "two_factor_recovery_codes",
        ["user_id", "code_hash"],
        unique=False,
    )
    op.create_table(
        "two_factor_trusted_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_two_factor_trusted_devices_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_two_factor_trusted_devices"),
    )
    op.create_index(
        "ix_two_factor_trusted_devices_user_id",
        "two_factor_trusted_devices",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_two_factor_trusted_devices_user_id", table_name="two_factor_trusted_devices"
    )
    op.drop_table("two_factor_trusted_devices")
    op.drop_index(
        "ix_two_factor_recovery_codes_user_hash", table_name="two_factor_recovery_codes"
    )
    op.drop_table("two_factor_recovery_codes")
    op.drop_table("user_two_factor")
