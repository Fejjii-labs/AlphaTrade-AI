"""Griller follow-up guards for the grade-based risk policy.

Revision ID: a17riskpolicy002
Revises: a16riskpolicy001
Create Date: 2026-10-18 02:10:00.000000

Postgres refuses updates and deletes of profile versions. Cancellations,
stored promotion reports, and breaker resets are append-only. Breaker state
gains the realized and unrealized P&L used by the daily breaker. Downgrade
does not drop breaker rows; that refusal stays on a16.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a17riskpolicy002"
down_revision: str | None = "a16riskpolicy001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = sa.Numeric(20, 8)
_VERSION_GUARD = """
CREATE FUNCTION risk_policy_versions_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'risk_policy_version_immutable';
END;
$$
"""
_VERSION_TRIGGER = """
CREATE TRIGGER risk_policy_versions_immutable
BEFORE UPDATE OR DELETE ON risk_policy_versions
FOR EACH ROW EXECUTE FUNCTION risk_policy_versions_immutable()
"""


def upgrade() -> None:
    op.add_column(
        "risk_policy_breaker_states",
        sa.Column("realized_pnl", _MONEY, nullable=True),
    )
    op.add_column(
        "risk_policy_breaker_states",
        sa.Column("unrealized_pnl", _MONEY, nullable=True),
    )
    op.create_table(
        "risk_policy_version_cancellations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["version_id"], ["risk_policy_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", name="uq_risk_policy_version_cancellation"),
    )
    op.create_table(
        "risk_policy_promotion_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("grader_version", sa.String(64), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("ordered", sa.Boolean(), nullable=False),
        sa.Column("walk_forward_ok", sa.Boolean(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "grader_version",
            name="uq_risk_policy_promotion_grader",
        ),
    )
    op.create_table(
        "risk_policy_breaker_resets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("actor_channel", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_VERSION_GUARD)
        op.execute(_VERSION_TRIGGER)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS risk_policy_versions_immutable ON risk_policy_versions")
        op.execute("DROP FUNCTION IF EXISTS risk_policy_versions_immutable()")
    op.drop_table("risk_policy_breaker_resets")
    op.drop_table("risk_policy_promotion_reports")
    op.drop_table("risk_policy_version_cancellations")
    with op.batch_alter_table("risk_policy_breaker_states") as batch:
        batch.drop_column("unrealized_pnl")
        batch.drop_column("realized_pnl")
