"""Allow a fresh authorization after a revoked or expired one.

Revision ID: a15reissueauth001
Revises: a14autonomouschannel001
Create Date: 2026-10-18 00:30:00.000000

``approval_request_id`` was unique, so a REVOKED or EXPIRED row permanently
occupied the approval. This revision drops that uniqueness and adds a partial
unique index: one AVAILABLE or CONSUMED row per approval. Historical rows
stay. Downgrade refuses on every dialect when one approval has more than one
authorization. PostgreSQL finds the unique constraint in the catalog. Other
dialects reflect it and drop that name with ``batch_alter_table``. An unnamed
SQLite UNIQUE has no name to drop, so the table is rebuilt from reflection
without that constraint.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Sequence
from pathlib import Path

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
    if op.get_bind().dialect.name == "postgresql":
        op.create_unique_constraint(
            _REQUEST_UNIQUE,
            "approval_authorizations",
            ["approval_request_id"],
        )
        return
    with op.batch_alter_table("approval_authorizations") as batch:
        batch.create_unique_constraint(_REQUEST_UNIQUE, ["approval_request_id"])


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
        _drop_reflected_approval_request_unique(bind)
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


def _drop_reflected_approval_request_unique(bind: sa.Connection) -> None:
    """Drop the single-column unique using the name reflection actually reports."""

    matches = [
        constraint
        for constraint in sa.inspect(bind).get_unique_constraints("approval_authorizations")
        if list(constraint.get("column_names") or []) == ["approval_request_id"]
    ]
    if not matches:
        raise RuntimeError("approval_request_id uniqueness was not found")
    named = [str(constraint["name"]) for constraint in matches if constraint.get("name")]
    if len(named) == len(matches):
        with op.batch_alter_table("approval_authorizations") as batch:
            for name in named:
                batch.drop_constraint(name, type_="unique")
        return
    _channel_revision()._rebuild_approval_authorizations(
        bind,
        drop_approval_request_unique=True,
    )


def _channel_revision() -> object:
    """Load a14. Migrations are not a package, so this is a path import."""

    path = Path(__file__).with_name("a14autonomouschannel001_autonomous_authorization_channel.py")
    spec = importlib.util.spec_from_file_location("a14autonomouschannel001", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("a14 revision could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
