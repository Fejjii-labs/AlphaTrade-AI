"""Shared Binance REST ban deadline and startup lease across worker instances.

Revision ID: a24binancegate001
Revises: a23marketmonitor001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a24binancegate001"
down_revision: str | None = "a23marketmonitor001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "binance_rest_coordination",
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("gate_kind", sa.String(length=16), nullable=False),
        sa.Column("gate_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gate_status_code", sa.Integer(), nullable=True),
        sa.Column("gate_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gate_updated_by", sa.String(length=80), nullable=False),
        sa.Column("lease_owner", sa.String(length=80), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("scope"),
    )


def downgrade() -> None:
    op.drop_table("binance_rest_coordination")
