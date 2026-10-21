"""Autonomous trading settings per organization user (issue #284).

Revision ID: a20autonomoussettings001
Revises: a19riskpolicy004
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a20autonomoussettings001"
down_revision: str | None = "a19riskpolicy004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autonomous_trading_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("daily_loss_limit_percent", sa.Numeric(20, 8), nullable=False),
        sa.Column("max_trades_per_day", sa.Integer(), nullable=False),
        sa.Column("risk_per_trade_percent", sa.Numeric(20, 8), nullable=True),
        sa.Column("strategy_id", sa.Uuid(), nullable=True),
        sa.Column("symbol", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["strategy_id"], ["user_strategies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_autonomous_trading_settings_org_user",
        ),
        sa.CheckConstraint(
            "daily_loss_limit_percent > 0 AND daily_loss_limit_percent <= 6",
            name="ck_autonomous_trading_settings_daily_loss",
        ),
        sa.CheckConstraint(
            "max_trades_per_day >= 1 AND max_trades_per_day <= 1000",
            name="ck_autonomous_trading_settings_max_trades",
        ),
        sa.CheckConstraint(
            "(risk_per_trade_percent IS NULL) OR "
            "(risk_per_trade_percent > 0 AND risk_per_trade_percent <= 5)",
            name="ck_autonomous_trading_settings_risk_override",
        ),
    )


def downgrade() -> None:
    op.drop_table("autonomous_trading_settings")
