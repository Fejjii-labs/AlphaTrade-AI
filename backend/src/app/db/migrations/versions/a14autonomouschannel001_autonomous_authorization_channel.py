"""Extend the approval channel CHECK to include AUTONOMOUS.

Revision ID: a14autonomouschannel001
Revises: a13voicemonitor001
Create Date: 2026-10-17 23:20:00.000000

The original authorization column was a non-native enum of WEB, API, and
TELEGRAM. Databases migrated while that enum created a CHECK still reject
AUTONOMOUS. This revision replaces any channel CHECK with one that also
allows AUTONOMOUS. Downgrade restores the three-value CHECK and refuses
while an AUTONOMOUS row is still stored. That row check runs on every
dialect. PostgreSQL replaces the CHECK from the catalog. Other dialects
reflect the channel CHECK and replace it in batch mode, including an unnamed
SQLite CHECK, which SQLite cannot alter in place.
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
    if op.get_bind().dialect.name == "postgresql":
        _drop_channel_checks()
        op.create_check_constraint(_CONSTRAINT, "approval_authorizations", _CURRENT)
        return
    _replace_channel_check(_CURRENT)


def downgrade() -> None:
    if _autonomous_rows_exist(op.get_bind()):
        raise RuntimeError(
            "AUTONOMOUS approval authorizations exist; downgrade would reject those rows."
        )
    if op.get_bind().dialect.name == "postgresql":
        _drop_channel_checks()
        op.create_check_constraint(_CONSTRAINT, "approval_authorizations", _LEGACY)
        return
    _replace_channel_check(_LEGACY)


def _autonomous_rows_exist(bind: sa.Connection) -> bool:
    """True when any stored channel is AUTONOMOUS. The SELECT is dialect-independent."""

    stored = bind.execute(
        sa.text("SELECT 1 FROM approval_authorizations WHERE channel = 'AUTONOMOUS' LIMIT 1")
    ).first()
    return stored is not None


def _replace_channel_check(predicate: str) -> None:
    """Reflect channel CHECKs and replace them. SQLite cannot ALTER a CHECK."""

    bind = op.get_bind()
    checks = [
        check
        for check in sa.inspect(bind).get_check_constraints("approval_authorizations")
        if "channel" in str(check.get("sqltext") or "").lower()
    ]
    if not checks:
        raise RuntimeError("channel check was not found")
    named = [str(check["name"]) for check in checks if check.get("name")]
    if len(named) == len(checks):
        with op.batch_alter_table("approval_authorizations") as batch:
            for name in named:
                batch.drop_constraint(name, type_="check")
            batch.create_check_constraint(_CONSTRAINT, predicate)
        return
    _rebuild_without_channel_checks(bind, predicate)


def _rebuild_without_channel_checks(bind: sa.Connection, predicate: str) -> None:
    """Copy the table, dropping unnamed channel CHECKs SQLite cannot name."""

    _rebuild_approval_authorizations(
        bind,
        drop_channel_checks=True,
        add_check=(_CONSTRAINT, predicate),
    )


def _drop_channel_checks() -> None:
    """Replace channel CHECKs from the PostgreSQL catalog.

    Production migrations run on PostgreSQL. The caller uses reflection on
    every other dialect.
    """

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


def _rebuild_approval_authorizations(
    bind: sa.Connection,
    *,
    drop_channel_checks: bool = False,
    drop_approval_request_unique: bool = False,
    add_check: tuple[str, str] | None = None,
) -> None:
    """Recreate the table from reflection, omitting constraints SQLite cannot drop.

    Batch mode can drop a constraint only when reflection gives it a name.
    An unnamed UNIQUE or CHECK is part of the CREATE TABLE statement, so the
    copy leaves that constraint behind and preserves every other constraint
    and index.
    """

    old = sa.Table("approval_authorizations", sa.MetaData(), autoload_with=bind)
    columns = []
    for column in old.columns:
        copied = column._copy()
        copied.table = None
        copied.unique = False
        copied.index = False
        copied.foreign_keys.clear()
        columns.append(copied)
    constraints: list[sa.Constraint] = []
    dropped_unique = False
    dropped_checks = 0
    for constraint in old.constraints:
        if isinstance(constraint, sa.PrimaryKeyConstraint):
            continue
        if isinstance(constraint, sa.UniqueConstraint):
            names = [item.name for item in constraint.columns]
            if drop_approval_request_unique and names == ["approval_request_id"]:
                dropped_unique = True
                continue
            constraints.append(sa.UniqueConstraint(*names, name=constraint.name))
            continue
        if isinstance(constraint, sa.CheckConstraint):
            sqltext = str(constraint.sqltext)
            if drop_channel_checks and "channel" in sqltext.lower():
                dropped_checks += 1
                continue
            constraints.append(sa.CheckConstraint(sqltext, name=constraint.name))
            continue
        if isinstance(constraint, sa.ForeignKeyConstraint):
            constraints.append(
                sa.ForeignKeyConstraint(
                    [item.name for item in constraint.columns],
                    [element.target_fullname for element in constraint.elements],
                    name=constraint.name,
                )
            )
    if drop_approval_request_unique and not dropped_unique:
        raise RuntimeError("approval_request_id uniqueness was not found")
    if drop_channel_checks and dropped_checks == 0:
        raise RuntimeError("channel check was not found")
    if add_check is not None:
        constraints.append(sa.CheckConstraint(add_check[1], name=add_check[0]))
    rebuilt = sa.MetaData()
    for constraint in constraints:
        if not isinstance(constraint, sa.ForeignKeyConstraint):
            continue
        for element in constraint.elements:
            referred = element.target_fullname.split(".")[0]
            if referred not in rebuilt.tables:
                sa.Table(referred, rebuilt, autoload_with=bind)
    temporary = sa.Table(
        "_alembic_tmp_approval_authorizations",
        rebuilt,
        *columns,
        *constraints,
    )
    temporary.create(bind)
    quoted = ", ".join(_quote_identifier(column.name) for column in old.columns)
    bind.execute(
        sa.text(
            f"INSERT INTO _alembic_tmp_approval_authorizations ({quoted}) "
            f"SELECT {quoted} FROM approval_authorizations"
        )
    )
    indexes = [
        (
            index.name,
            [column.name for column in index.columns],
            bool(index.unique),
            index.dialect_options["sqlite"].get("where"),
        )
        for index in old.indexes
        if index.name
    ]
    bind.execute(sa.text("DROP TABLE approval_authorizations"))
    bind.execute(
        sa.text(
            "ALTER TABLE _alembic_tmp_approval_authorizations RENAME TO approval_authorizations"
        )
    )
    restored = sa.Table("approval_authorizations", sa.MetaData(), autoload_with=bind)
    for name, column_names, unique, where in indexes:
        sa.Index(
            name,
            *[restored.c[column_name] for column_name in column_names],
            unique=unique,
            sqlite_where=where,
        ).create(bind)


def _quote_identifier(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise RuntimeError("unexpected approval_authorizations column name")
    return f'"{name}"'
