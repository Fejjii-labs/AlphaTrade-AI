"""Allow a fresh authorization after a revoked or expired one.

Revision ID: a15reissueauth001
Revises: a14autonomouschannel001
Create Date: 2026-10-18 00:30:00.000000

``approval_request_id`` was unique, so a REVOKED or EXPIRED row permanently
occupied the approval. This revision drops that uniqueness and adds a partial
unique index: one AVAILABLE or CONSUMED row per approval. Historical rows
stay. Downgrade refuses on every dialect when one approval has more than one
authorization. The constraint lookup uses the PostgreSQL catalog because that
is the migrated production database.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a15reissueauth001"
down_revision: str | None = "a14autonomouschannel001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REUSABLE_INDEX = "uq_reusable_approval_authorization"
_REQUEST_UNIQUE = "uq_approval_authorizations_approval_request_id"
_REUSABLE_PREDICATE = "state IN ('AVAILABLE', 'CONSUMED')"


def upgrade() -> None:
    _drop_approval_request_unique()
    op.create_index(
        _REUSABLE_INDEX,
        "approval_authorizations",
        ["approval_request_id"],
        unique=True,
        postgresql_where=sa.text(_REUSABLE_PREDICATE),
        sqlite_where=sa.text(_REUSABLE_PREDICATE),
    )


def downgrade() -> None:
    if _duplicate_approval_authorizations(op.get_bind()):
        raise RuntimeError(
            "Multiple approval authorizations share an approval; downgrade would reject those rows."
        )
    op.drop_index(_REUSABLE_INDEX, table_name="approval_authorizations")
    op.create_unique_constraint(
        _REQUEST_UNIQUE,
        "approval_authorizations",
        ["approval_request_id"],
    )


def _duplicate_approval_authorizations(bind: sa.Connection) -> bool:
    """True when one approval has more than one authorization. Dialect-independent."""

    stored = bind.execute(
        sa.text(
            """
            SELECT approval_request_id
            FROM approval_authorizations
            GROUP BY approval_request_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    return stored is not None


def _drop_approval_request_unique() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        op.drop_constraint(_REQUEST_UNIQUE, "approval_authorizations", type_="unique")
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
              AND c.contype = 'u'
              AND pg_get_constraintdef(c.oid) ILIKE '%approval_request_id%'
              AND pg_get_constraintdef(c.oid) NOT ILIKE '%,%'
            """
        )
    ).scalars()
    found = False
    for name in names:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise RuntimeError("unexpected approval_request_id constraint name")
        bind.execute(sa.text(f'ALTER TABLE approval_authorizations DROP CONSTRAINT "{name}"'))
        found = True
    if not found:
        raise RuntimeError("approval_request_id uniqueness was not found")
