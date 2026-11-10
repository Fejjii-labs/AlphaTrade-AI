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


def upgrade() -> None:
    # Nullable without a default: a catalog-only change in PostgreSQL, no table rewrite.
    op.add_column(_TABLE, sa.Column("system_prompt_sha256", sa.String(length=64), nullable=True))
    op.add_column(_TABLE, sa.Column("prompt_id", sa.String(length=80), nullable=True))
    op.add_column(_TABLE, sa.Column("prompt_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "prompt_version")
    op.drop_column(_TABLE, "prompt_id")
    op.drop_column(_TABLE, "system_prompt_sha256")
