"""report worker risk model v2 flag

Revision ID: a32workerriskv2flag001
Revises: a31riskmodelv2001
Create Date: 2026-11-20 10:08:28.402548
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a32workerriskv2flag001"
down_revision: str | None = "a31riskmodelv2001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "worker_runtime_state", sa.Column("risk_model_v2_enabled", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("worker_runtime_state", "risk_model_v2_enabled")
