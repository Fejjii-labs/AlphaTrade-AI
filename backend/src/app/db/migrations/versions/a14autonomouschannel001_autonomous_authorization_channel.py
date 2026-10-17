"""Extend the approval channel CHECK to include AUTONOMOUS.

Revision ID: a14autonomouschannel001
Revises: a13voicemonitor001
Create Date: 2026-10-17 23:20:00.000000

The original authorization column was a non-native enum of WEB, API, and
TELEGRAM. Databases migrated while that enum created a CHECK still reject
AUTONOMOUS. This revision replaces any channel CHECK with one that also
allows AUTONOMOUS. Downgrade restores the three-value CHECK and refuses
while an AUTONOMOUS row is still stored.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a14autonomouschannel001"
down_revision: str | None = "a13voicemonitor001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_authorization_channel"
_LEGACY = "channel IN ('WEB', 'API', 'TELEGRAM')"
_CURRENT = "channel IN ('WEB', 'API', 'TELEGRAM', 'AUTONOMOUS')"


def upgrade() -> None:
    _drop_channel_checks()
    op.create_check_constraint(_CONSTRAINT, "approval_authorizations", _CURRENT)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        stored = bind.execute(
            sa.text("SELECT 1 FROM approval_authorizations WHERE channel = 'AUTONOMOUS' LIMIT 1")
        ).first()
        if stored is not None:
            raise RuntimeError(
                "AUTONOMOUS approval authorizations exist; downgrade would reject those rows."
            )
    _drop_channel_checks()
    op.create_check_constraint(_CONSTRAINT, "approval_authorizations", _LEGACY)


def _drop_channel_checks() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    names = bind.execute(
        sa.text(
            """
            SELECT c.conname
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = current_schema()
              AND t.relname = 'approval_authorizations'
              AND c.contype = 'c'
              AND pg_get_constraintdef(c.oid) ILIKE '%channel%'
            """
        )
    ).scalars()
    # Drop the catalog name directly. op.drop_constraint would apply the
    # naming convention again and miss the already-truncated constraint.
    for name in names:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise RuntimeError("unexpected approval channel constraint name")
        bind.execute(sa.text(f'ALTER TABLE approval_authorizations DROP CONSTRAINT "{name}"'))
