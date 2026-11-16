"""Partial-exit records: reduction fills and protective stop moves.

Creates ``position_exit_events``, append-only and keyed per position and
venue, so a partial reduction or a breakeven/trailing stop move is written
once and survives a worker restart. New table only; no existing row changes.

Revision ID: a29partialexit001
Revises: a28promptsha001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a29partialexit001"
down_revision: str | None = "a28promptsha001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "position_exit_events"
_NUMERIC = sa.Numeric(36, 18)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("execution_lifecycle_id", sa.Uuid(), nullable=False),
        sa.Column("venue", sa.String(length=40), nullable=False),
        sa.Column("event_key", sa.String(length=160), nullable=False),
        sa.Column("event_kind", sa.String(length=16), nullable=False),
        sa.Column("fill_kind", sa.String(length=16), nullable=True),
        sa.Column("leg_order", sa.Integer(), nullable=True),
        sa.Column("quantity", _NUMERIC, nullable=True),
        sa.Column("price", _NUMERIC, nullable=True),
        sa.Column("stop_reason", sa.String(length=16), nullable=True),
        sa.Column("previous_stop", _NUMERIC, nullable=True),
        sa.Column("new_stop", _NUMERIC, nullable=True),
        sa.Column("tightened", sa.Boolean(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "organization_id",
            "execution_lifecycle_id",
            "venue",
            "event_key",
            name="uq_position_exit_events_key",
        ),
        sa.CheckConstraint(
            "venue IN ('PAPER_INTERNAL', 'BLOFIN_DEMO')",
            name="position_exit_events_venue",
        ),
        sa.CheckConstraint(
            "(event_kind = 'fill' AND fill_kind IN ('target', 'stop', 'runner_stop')"
            " AND quantity > 0 AND price > 0)"
            " OR (event_kind = 'stop_move' AND stop_reason IN ('breakeven', 'trail')"
            " AND previous_stop > 0 AND new_stop > 0 AND tightened IS NOT NULL)",
            name="position_exit_events_shape",
        ),
    )
    op.create_index(
        "ix_position_exit_events_position",
        _TABLE,
        ["organization_id", "execution_lifecycle_id", "venue"],
    )


def downgrade() -> None:
    # Recorded reductions are trade history. Refuse to drop them; an empty
    # disposable database still supports a roundtrip.
    if not op.get_context().as_sql and op.get_bind().scalar(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {_TABLE})")
    ):
        raise RuntimeError("Populated partial-exit records require retained schema rollback.")
    op.drop_index("ix_position_exit_events_position", table_name=_TABLE)
    op.drop_table(_TABLE)
