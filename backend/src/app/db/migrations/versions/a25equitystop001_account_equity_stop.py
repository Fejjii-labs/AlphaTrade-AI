"""Account daily equity stop baseline on the breaker state (AT-ADR-152).

Revision ID: a25equitystop001
Revises: a24demoleverage001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a25equitystop001"
down_revision: str | None = "a24demoleverage001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "risk_policy_breaker_states"


def upgrade() -> None:
    # Nullable with no backfill. An existing row has no baseline until the
    # next 00:00 UTC snapshot; outside the grace window the stop fails closed.
    op.add_column(_TABLE, sa.Column("day_start_equity", sa.Numeric(20, 8), nullable=True))
    op.add_column(_TABLE, sa.Column("day_start_on", sa.Date(), nullable=True))
    op.add_column(_TABLE, sa.Column("equity_stop_tripped_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "equity_stop_tripped_on")
    op.drop_column(_TABLE, "day_start_on")
    op.drop_column(_TABLE, "day_start_equity")
