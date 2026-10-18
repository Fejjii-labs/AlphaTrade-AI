"""Grade-based risk policy tables.

Revision ID: a16riskpolicy001
Revises: a15reissueauth001
Create Date: 2026-10-18 01:30:00.000000

Applied profile versions are immutable application rows. The database ceilings
match the code ceilings: 2% per trade, 6% daily, 6% aggregate open risk, 20x
leverage, and a human-only apply channel. Downgrade refuses while a trade
record exists, because that row is the audit of a risk decision.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a16riskpolicy001"
down_revision: str | None = "a15reissueauth001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = sa.Numeric(20, 8)
_VERSION_CHECKS = (
    "risk_percent_a_plus > 0 AND risk_percent_a_plus <= 2",
    "risk_percent_a > 0 AND risk_percent_a <= 2",
    "risk_percent_b_plus > 0 AND risk_percent_b_plus <= 2",
    "risk_percent_b > 0 AND risk_percent_b <= 2",
    "risk_percent_c = 0",
    "max_leverage >= 1 AND max_leverage <= 20",
    "daily_loss_limit_percent > 0 AND daily_loss_limit_percent <= 6",
    "max_aggregate_open_risk_percent > 0 AND max_aggregate_open_risk_percent <= 6",
    "max_aggregate_notional_percent > max_notional_per_trade_percent",
    "max_cost_share_of_r > 0 AND max_cost_share_of_r <= 0.25",
    "actor_channel = 'human'",
    "change_class IN ('initial', 'tightening', 'loosening')",
)


def upgrade() -> None:
    op.create_table(
        "risk_policy_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("actor_channel", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor_channel IN ('human', 'agent', 'bot')",
            name="ck_risk_policy_proposals_actor_channel",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_risk_policy_proposals_org_kind",
        "risk_policy_proposals",
        ["organization_id", "kind"],
    )
    version_checks = [
        sa.CheckConstraint(text, name=f"ck_risk_policy_versions_{index}")
        for index, text in enumerate(_VERSION_CHECKS)
    ]
    op.create_table(
        "risk_policy_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("risk_percent_a_plus", _MONEY, nullable=False),
        sa.Column("risk_percent_a", _MONEY, nullable=False),
        sa.Column("risk_percent_b_plus", _MONEY, nullable=False),
        sa.Column("risk_percent_b", _MONEY, nullable=False),
        sa.Column("risk_percent_c", _MONEY, nullable=False),
        sa.Column("max_leverage", _MONEY, nullable=False),
        sa.Column("daily_loss_limit_percent", _MONEY, nullable=False),
        sa.Column("max_aggregate_open_risk_percent", _MONEY, nullable=False),
        sa.Column("max_open_positions", sa.Integer(), nullable=False),
        sa.Column("max_notional_per_trade_percent", _MONEY, nullable=False),
        sa.Column("max_aggregate_notional_percent", _MONEY, nullable=False),
        sa.Column("min_stop_atr_multiple", _MONEY, nullable=False),
        sa.Column("min_stop_absolute_floor", _MONEY, nullable=False),
        sa.Column("max_cost_share_of_r", _MONEY, nullable=False),
        sa.Column("drawdown_breaker_percent", _MONEY, nullable=False),
        sa.Column("consecutive_loss_limit", sa.Integer(), nullable=False),
        sa.Column("autonomous_internal_paper_enabled", sa.Boolean(), nullable=False),
        sa.Column("autonomous_blofin_demo_enabled", sa.Boolean(), nullable=False),
        sa.Column("change_class", sa.String(16), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("actor_channel", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("diff", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *version_checks,
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["risk_policy_proposals.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", name="uq_risk_policy_versions_proposal"),
        sa.UniqueConstraint(
            "organization_id",
            "kind",
            "version_number",
            name="uq_risk_policy_versions_number",
        ),
    )
    op.create_index(
        "ix_risk_policy_versions_effective",
        "risk_policy_versions",
        ["organization_id", "kind", "effective_at"],
    )
    op.create_table(
        "risk_policy_grade_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("setup_id", sa.String(64), nullable=False),
        sa.Column("grade", sa.String(8), nullable=False),
        sa.Column("grader_version", sa.String(64), nullable=False),
        sa.Column("trend_score", sa.String(32), nullable=False),
        sa.Column("momentum_score", sa.String(32), nullable=False),
        sa.Column("refusal", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "grade IN ('A+', 'A', 'B+', 'B', 'C')",
            name="ck_risk_policy_grade_bindings_grade",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "setup_id",
            name="uq_risk_policy_grade_binding_setup",
        ),
    )
    op.create_table(
        "risk_policy_trade_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("trade_id", sa.String(64), nullable=False),
        sa.Column("profile_version_id", sa.Uuid(), nullable=False),
        sa.Column("grade", sa.String(8), nullable=False),
        sa.Column("grader_version", sa.String(64), nullable=False),
        sa.Column("adjusters", sa.JSON(), nullable=False),
        sa.Column("calculation", sa.JSON(), nullable=False),
        sa.Column("ai_explanation", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["profile_version_id"], ["risk_policy_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "trade_id",
            name="uq_risk_policy_trade_record",
        ),
    )
    op.create_table(
        "risk_policy_breaker_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("equity_peak", _MONEY, nullable=False),
        sa.Column("drawdown_latched", sa.Boolean(), nullable=False),
        sa.Column("consecutive_losses", sa.Integer(), nullable=False),
        sa.Column("daily_paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "account_id",
            name="uq_risk_policy_breaker_account",
        ),
    )
    op.create_table(
        "risk_policy_calibration_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("grade", sa.String(8), nullable=False),
        sa.Column("grader_version", sa.String(64), nullable=False),
        sa.Column("r_multiple", _MONEY, nullable=False),
        sa.Column("in_sample", sa.Boolean(), nullable=False),
        sa.Column("walk_forward_fold", sa.Integer(), nullable=False),
        sa.Column("slippage_r", _MONEY, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_has_rows(bind, "risk_policy_trade_records"):
        raise RuntimeError("Risk-policy trade records exist; downgrade would delete them.")
    op.drop_table("risk_policy_calibration_samples")
    op.drop_table("risk_policy_breaker_states")
    op.drop_table("risk_policy_trade_records")
    op.drop_table("risk_policy_grade_bindings")
    op.drop_index("ix_risk_policy_versions_effective", table_name="risk_policy_versions")
    op.drop_table("risk_policy_versions")
    op.drop_index("ix_risk_policy_proposals_org_kind", table_name="risk_policy_proposals")
    op.drop_table("risk_policy_proposals")


def _table_has_rows(bind: sa.Connection, table: str) -> bool:
    if table != "risk_policy_trade_records":
        raise RuntimeError("Unexpected risk-policy table.")
    stored = bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first()
    return stored is not None
