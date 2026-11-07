"""Time indexes on watcher_observability_events for retention and reads (H5, M10).

Revision ID: a27obsretention001
Revises: a26equitystop002
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a27obsretention001"
down_revision: str | None = "a26equitystop002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "watcher_observability_events"


def upgrade() -> None:
    # CONCURRENTLY so the hot table keeps taking writes while the index builds
    # (it can hold millions of rows on staging).
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_watcher_observability_events_at",
            _TABLE,
            ["at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_watcher_observability_events_org_at",
            _TABLE,
            ["organization_id", "at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_watcher_observability_events_org_at",
            table_name=_TABLE,
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "ix_watcher_observability_events_at",
            table_name=_TABLE,
            postgresql_concurrently=True,
            if_exists=True,
        )
