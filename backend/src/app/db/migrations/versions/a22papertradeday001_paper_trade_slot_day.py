"""Record the local day the paper trade-slot counters belong to.

Revision ID: a22papertradeday001
Revises: a21killswitchkey001
Create Date: 2026-10-22 21:30:00.000000

``max_trade_slots`` was copied once and ``actual_trade_count`` never moved
back to zero, so the daily cap behaved as a lifetime count. The nullable
date lets the paper claim path roll those counters on the first locked
access of a new trading day. Existing rows stay NULL until that access.
Downgrade drops the date and leaves the counters as they are.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a22papertradeday001"
down_revision: str | None = "a21killswitchkey001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "account_risk_accounting_states",
        sa.Column("trade_slot_business_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("account_risk_accounting_states", "trade_slot_business_date")
