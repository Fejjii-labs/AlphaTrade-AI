"""Start-of-day balance for the account daily equity stop (AT-ADR-152, D1).

Revision ID: a26equitystop002
Revises: a25equitystop001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a26equitystop002"
down_revision: str | None = "a25equitystop001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "risk_policy_breaker_states"


def upgrade() -> None:
    # Nullable with no backfill. A NULL row reads the current balance until
    # its next snapshot, or until a balance edit records the pre-edit one.
    op.add_column(_TABLE, sa.Column("day_start_balance", sa.Numeric(20, 8), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "day_start_balance")
