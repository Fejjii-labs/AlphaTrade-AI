"""Persist breaker open and close intervals.

Revision ID: a18riskpolicy003
Revises: a17riskpolicy002
Create Date: 2026-10-18 03:05:00.000000

The live breaker row is the current latch. These rows are the history used
to decide whether a loosening's effective time fell inside a breaker window.
Downgrade refuses while any interval exists.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a18riskpolicy003"
down_revision: str | None = "a17riskpolicy002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "risk_policy_breaker_intervals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=8), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('daily', 'consecutive', 'drawdown')",
            name="ck_risk_policy_breaker_interval_kind",
        ),
        sa.CheckConstraint(
            "phase IN ('open', 'close')",
            name="ck_risk_policy_breaker_interval_phase",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "account_id",
            "kind",
            "phase",
            "occurred_at",
            name="uq_risk_policy_breaker_interval",
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    stored = bind.execute(sa.text("SELECT 1 FROM risk_policy_breaker_intervals LIMIT 1")).first()
    if stored is not None:
        raise RuntimeError("Risk-policy breaker intervals exist; downgrade would delete them.")
    op.drop_table("risk_policy_breaker_intervals")
