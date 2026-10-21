"""Widen controlled_runtime_status.component so a kill-switch key fits.

Revision ID: a21killswitchkey001
Revises: a20autonomoussettings001
Create Date: 2026-10-20 20:00:00.000000

Pending kill-switch alerts are keyed ``k`` plus the full organization UUID
hex (33 characters). The column was ``varchar(32)``, which forced the key
to drop the last hex digit. Widening to 64 is metadata-only on PostgreSQL.
Downgrade refuses while any component is longer than 32 characters, so a
full-hex key is not truncated.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a21killswitchkey001"
down_revision: str | None = "a20autonomoussettings001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NARROW = sa.String(length=32)
_WIDE = sa.String(length=64)


def upgrade() -> None:
    _set_component_type(_WIDE, existing=_NARROW)


def downgrade() -> None:
    if _component_longer_than(32):
        raise RuntimeError(
            "controlled_runtime_status.component is longer than 32 characters; "
            "downgrade would truncate a kill-switch key."
        )
    _set_component_type(_NARROW, existing=_WIDE)


def _set_component_type(type_: sa.String, *, existing: sa.String) -> None:
    with op.batch_alter_table("controlled_runtime_status") as batch:
        batch.alter_column(
            "component",
            existing_type=existing,
            type_=type_,
            existing_nullable=False,
        )


def _component_longer_than(limit: int) -> bool:
    statement = sa.text(
        "SELECT 1 FROM controlled_runtime_status WHERE LENGTH(component) > :limit LIMIT 1"
    ).bindparams(limit=limit)
    return op.get_bind().execute(statement).first() is not None
