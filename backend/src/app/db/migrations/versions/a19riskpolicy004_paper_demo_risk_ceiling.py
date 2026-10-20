"""Paper and demo per-trade ceiling of 5% (AT-ADR-110).

Revision ID: a19riskpolicy004
Revises: a18riskpolicy003
Create Date: 2026-10-18 12:30:00.000000

Adds the audited profile name, default leverage, and paper/demo data-collection
flag. Replaces the flat 2% per-trade checks with kind-aware checks: internal
paper and demo may reach 5%; every other kind stays at 2%. Existing rows are
at or below 2% with the new columns at their defaults, so this upgrade does
not rewrite a version. Downgrade refuses when a row would not fit the old
2% checks or when the new audited fields are in use.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a19riskpolicy004"
down_revision: str | None = "a18riskpolicy003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PAPER = "kind IN ('internal_paper', 'demo')"
_OTHER = "kind NOT IN ('internal_paper', 'demo')"
_RISK_COLUMNS = (
    "risk_percent_a_plus",
    "risk_percent_a",
    "risk_percent_b_plus",
    "risk_percent_b",
)
_OLD_RISK_CHECKS = tuple(f"{column} > 0 AND {column} <= 2" for column in _RISK_COLUMNS)
_DEFAULT_LEVERAGE_CHECK = (
    "default_leverage >= 1 AND default_leverage <= max_leverage AND default_leverage <= 20"
)
_DATA_COLLECTION_CHECK = (
    "(paper_demo_data_collection = false OR kind IN ('internal_paper', 'demo'))"
)


def _kind_risk_check(column: str) -> str:
    return (
        f"(({_PAPER} AND {column} > 0 AND {column} <= 5) OR "
        f"({_OTHER} AND {column} > 0 AND {column} <= 2))"
    )


def _replace_risk_checks(statements: tuple[str, ...]) -> None:
    with op.batch_alter_table("risk_policy_versions") as batch:
        for index in range(4):
            batch.drop_constraint(f"ck_risk_policy_versions_{index}", type_="check")
        for index, text in enumerate(statements):
            batch.create_check_constraint(f"ck_risk_policy_versions_{index}", text)


def upgrade() -> None:
    op.add_column(
        "risk_policy_versions",
        sa.Column("profile_name", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "risk_policy_versions",
        sa.Column("default_leverage", sa.Numeric(20, 8), nullable=False, server_default="1"),
    )
    op.add_column(
        "risk_policy_versions",
        sa.Column(
            "paper_demo_data_collection",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    _replace_risk_checks(tuple(_kind_risk_check(column) for column in _RISK_COLUMNS))
    with op.batch_alter_table("risk_policy_versions") as batch:
        batch.create_check_constraint(
            "ck_risk_policy_versions_default_leverage",
            _DEFAULT_LEVERAGE_CHECK,
        )
        batch.create_check_constraint(
            "ck_risk_policy_versions_data_collection",
            _DATA_COLLECTION_CHECK,
        )


def downgrade() -> None:
    bind = op.get_bind()
    blocked = bind.execute(
        sa.text(
            "SELECT 1 FROM risk_policy_versions WHERE "
            "risk_percent_a_plus > 2 OR risk_percent_a > 2 OR "
            "risk_percent_b_plus > 2 OR risk_percent_b > 2 OR "
            "paper_demo_data_collection = true OR default_leverage <> 1 OR "
            "profile_name <> '' LIMIT 1"
        )
    ).first()
    if blocked is not None:
        raise RuntimeError(
            "Paper/demo ceiling rows exist; downgrade would hide an audited profile."
        )
    with op.batch_alter_table("risk_policy_versions") as batch:
        batch.drop_constraint("ck_risk_policy_versions_data_collection", type_="check")
        batch.drop_constraint("ck_risk_policy_versions_default_leverage", type_="check")
    _replace_risk_checks(_OLD_RISK_CHECKS)
    op.drop_column("risk_policy_versions", "paper_demo_data_collection")
    op.drop_column("risk_policy_versions", "default_leverage")
    op.drop_column("risk_policy_versions", "profile_name")
