"""Worker runtime flags and the owner paper switch.

Revision ID: a22workerruntime001
Revises: a21killswitchkey001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a22workerruntime001"
down_revision: str | None = "a21killswitchkey001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_runtime_state",
        sa.Column("component", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=80), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_cycle_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("autonomy_mode", sa.String(length=64), nullable=False),
        sa.Column("autonomous_internal_paper_env_ceiling", sa.Boolean(), nullable=False),
        sa.Column("autonomous_internal_paper_db_enabled", sa.Boolean(), nullable=False),
        sa.Column("autonomous_internal_paper_effective", sa.Boolean(), nullable=False),
        sa.Column("watcher_orchestration_enabled", sa.Boolean(), nullable=False),
        sa.Column("watcher_paper_staging_activation", sa.Boolean(), nullable=False),
        sa.Column("telegram_paper_activation_armed", sa.Boolean(), nullable=False),
        sa.Column("telegram_interaction_enabled", sa.Boolean(), nullable=False),
        sa.Column("telegram_alerts_enabled", sa.Boolean(), nullable=False),
        sa.Column("automatic_telegram_delivery_enabled", sa.Boolean(), nullable=False),
        sa.Column("watchlist", sa.JSON(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("component"),
    )
    op.create_table(
        "autonomous_paper_switches",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("organization_id"),
    )


def downgrade() -> None:
    op.drop_table("autonomous_paper_switches")
    op.drop_table("worker_runtime_state")
