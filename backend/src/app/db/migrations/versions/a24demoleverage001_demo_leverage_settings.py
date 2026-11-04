"""Configurable BloFin demo leverage and margin mode on autonomous settings.

Revision ID: a24demoleverage001
Revises: a24binancegate001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a24demoleverage001"
down_revision: str | None = "a24binancegate001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "autonomous_trading_settings"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("demo_leverage", sa.Integer(), nullable=True))
    op.add_column(_TABLE, sa.Column("demo_margin_mode", sa.String(length=16), nullable=True))
    op.add_column(_TABLE, sa.Column("demo_leverage_overrides", sa.JSON(), nullable=True))
    op.create_check_constraint(
        "ck_autonomous_trading_settings_demo_leverage",
        _TABLE,
        "(demo_leverage IS NULL) OR (demo_leverage >= 1 AND demo_leverage <= 20)",
    )
    op.create_check_constraint(
        "ck_autonomous_trading_settings_demo_margin_mode",
        _TABLE,
        "(demo_margin_mode IS NULL) OR (demo_margin_mode IN ('isolated', 'cross'))",
    )


def downgrade() -> None:
    op.drop_constraint("ck_autonomous_trading_settings_demo_margin_mode", _TABLE, type_="check")
    op.drop_constraint("ck_autonomous_trading_settings_demo_leverage", _TABLE, type_="check")
    op.drop_column(_TABLE, "demo_leverage_overrides")
    op.drop_column(_TABLE, "demo_margin_mode")
    op.drop_column(_TABLE, "demo_leverage")
