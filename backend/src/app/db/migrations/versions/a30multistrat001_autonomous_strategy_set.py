"""Autonomous settings hold a set of strategies, or every eligible one.

Adds ``strategy_ids`` (JSON list) and ``strategy_scope`` and copies each saved
``strategy_id`` into the set. ``strategy_id`` stays in place and readable; the
service keeps it mirrored to the first selected strategy.

Revision ID: a30multistrat001
Revises: a29partialexit001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a30multistrat001"
down_revision: str | None = "a29partialexit001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "autonomous_trading_settings"
_SCOPE_CHECK = "ck_autonomous_trading_settings_strategy_scope"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("strategy_ids", sa.JSON(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column(
            "strategy_scope", sa.String(length=16), nullable=False, server_default="selected"
        ),
    )
    op.create_check_constraint(
        _SCOPE_CHECK, _TABLE, "strategy_scope IN ('selected', 'all_eligible')"
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            f"UPDATE {_TABLE} SET strategy_ids = json_build_array(strategy_id::text) "
            "WHERE strategy_id IS NOT NULL"
        )
    else:
        op.execute(
            f"UPDATE {_TABLE} SET strategy_ids = json_array(strategy_id) "
            "WHERE strategy_id IS NOT NULL"
        )


def downgrade() -> None:
    # strategy_id was kept mirrored, so the legacy single selection survives.
    op.drop_constraint(_SCOPE_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "strategy_scope")
    op.drop_column(_TABLE, "strategy_ids")
