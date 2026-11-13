"""Prompt provenance on model_call_attempts (review item 5).

Adds three nullable columns: SHA-256 of the exact system prompt sent, and the
versioned prompt file id/version. Existing rows stay NULL; no backfill.

Revision ID: a28promptsha001
Revises: a27obsretention001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a28promptsha001"
down_revision: str | None = "a27obsretention001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "model_call_attempts"


def _set_lock_timeout(value: str) -> None:
    # ADD/DROP COLUMN takes an ACCESS EXCLUSIVE lock. Without a bound, a long
    # transaction holding model_call_attempts would stall this DDL and queue every
    # later reader behind it. env.py runs migrations in one transaction (PostgreSQL
    # DDL is transactional), so SET LOCAL ends with it at the latest. Resetting at
    # the end keeps the bound off later revisions in the same run.
    if op.get_context().dialect.name == "postgresql":
        op.execute(f"SET LOCAL lock_timeout = {value}")


def upgrade() -> None:
    _set_lock_timeout("'5s'")
    # Nullable without a default: a catalog-only change in PostgreSQL, no table rewrite.
    op.add_column(_TABLE, sa.Column("system_prompt_sha256", sa.String(length=64), nullable=True))
    op.add_column(_TABLE, sa.Column("prompt_id", sa.String(length=80), nullable=True))
    op.add_column(_TABLE, sa.Column("prompt_version", sa.Integer(), nullable=True))
    _set_lock_timeout("DEFAULT")


def downgrade() -> None:
    # Recorded provenance cannot be erased by an application rollback (retained
    # schema rollback instead). Refusing here, before any DDL, also keeps a longer
    # downgrade atomic: a27obsretention001's autocommit block would otherwise
    # commit these drops before an older revision's guard refuses the rest.
    # Empty disposable databases still support a roundtrip.
    if not op.get_context().as_sql and op.get_bind().scalar(
        sa.text(
            f"SELECT EXISTS (SELECT 1 FROM {_TABLE} WHERE system_prompt_sha256 IS NOT NULL"
            " OR prompt_id IS NOT NULL OR prompt_version IS NOT NULL)"
        )
    ):
        raise RuntimeError("Populated prompt provenance requires retained schema rollback.")
    _set_lock_timeout("'5s'")
    op.drop_column(_TABLE, "prompt_version")
    op.drop_column(_TABLE, "prompt_id")
    op.drop_column(_TABLE, "system_prompt_sha256")
    _set_lock_timeout("DEFAULT")
