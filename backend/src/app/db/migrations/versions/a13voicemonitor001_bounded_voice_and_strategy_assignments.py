"""bounded interactive voice and version pinned monitoring

Revision ID: a13voicemonitor001
Revises: a12trendpulsescreen001
Create Date: 2026-10-11 15:38:42.523935
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a13voicemonitor001"
down_revision: str | None = "a12trendpulsescreen001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watcher_assignment_receipts",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("configuration_revision", sa.Integer(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "configuration_revision >= 0",
            name=op.f("ck_watcher_assignment_receipts_ck_watcher_assignment_receipt_revision"),
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 64",
            name=op.f("ck_watcher_assignment_receipts_ck_watcher_assignment_receipt_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_watcher_assignment_receipts_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_watcher_assignment_receipts_user_id_users")
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "request_key", name=op.f("pk_watcher_assignment_receipts")
        ),
    )
    op.create_index(
        "ix_watcher_assignment_receipts_org_user",
        "watcher_assignment_receipts",
        ["organization_id", "user_id"],
        unique=False,
    )
    op.create_table(
        "voice_session_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("budget_reference", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reserved_cost", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("accounted_cost", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("response_count", sa.Integer(), nullable=False),
        sa.Column("ledger", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','active','closed','uncertain')",
            name=op.f("ck_voice_session_reservations_voice_status"),
        ),
        sa.CheckConstraint(
            "accounted_cost IS NULL OR accounted_cost >= 0",
            name=op.f("ck_voice_session_reservations_voice_accounted_cost"),
        ),
        sa.CheckConstraint(
            "reserved_cost >= 0", name=op.f("ck_voice_session_reservations_voice_reserved_cost")
        ),
        sa.CheckConstraint(
            "response_count >= 0", name=op.f("ck_voice_session_reservations_voice_response_count")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_voice_session_reservations_conversation_id_conversations"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_voice_session_reservations_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_voice_session_reservations_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voice_session_reservations")),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "reserved_tokens >= 0", name=op.f("ck_voice_session_reservations_voice_reserved_tokens")
        ),
    )
    op.create_index(
        "ix_voice_session_budget",
        "voice_session_reservations",
        ["organization_id", "budget_reference"],
        unique=False,
    )
    op.create_table(
        "watcher_strategy_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_id", sa.Uuid(), nullable=False),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("version_content_hash", sa.String(length=64), nullable=False),
        sa.Column("timeframes", sa.JSON(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("monitoring_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(version_content_hash) = 64",
            name=op.f("ck_watcher_strategy_assignments_ck_watcher_assignment_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_watcher_strategy_assignments_organization_id_organizations"),
        ),
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["user_strategies.id"],
            name=op.f("fk_watcher_strategy_assignments_strategy_id_user_strategies"),
        ),
        sa.ForeignKeyConstraint(
            ["strategy_version_id"],
            ["user_strategy_versions.id"],
            name=op.f("fk_watcher_strategy_assignments_strategy_version_id_user_strategy_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_watcher_strategy_assignments_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_watcher_strategy_assignments")),
        sa.UniqueConstraint(
            "organization_id", "symbol", "strategy_version_id", name="uq_watcher_assignment_version"
        ),
    )
    op.create_index(
        "ix_watcher_assignments_org_user",
        "watcher_strategy_assignments",
        ["organization_id", "user_id"],
        unique=False,
    )


def downgrade() -> None:
    # Pending identities and potentially billed sessions cannot be erased by an
    # application rollback. Empty disposable databases still support a roundtrip.
    for table in (
        "watcher_strategy_assignments",
        "watcher_assignment_receipts",
        "voice_session_reservations",
    ):
        if op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError(
                "Populated voice/monitoring recovery requires retained schema rollback."
            )
    op.drop_index("ix_watcher_assignments_org_user", table_name="watcher_strategy_assignments")
    op.drop_table("watcher_strategy_assignments")
    op.drop_index("ix_voice_session_budget", table_name="voice_session_reservations")
    op.drop_table("voice_session_reservations")
    op.drop_index(
        "ix_watcher_assignment_receipts_org_user", table_name="watcher_assignment_receipts"
    )
    op.drop_table("watcher_assignment_receipts")
