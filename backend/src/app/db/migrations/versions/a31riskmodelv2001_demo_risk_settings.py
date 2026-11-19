"""Seed demo bot risk settings and track the demo loss ledger UTC day."""

import sqlalchemy as sa
from alembic import op

revision = "a31riskmodelv2001"
down_revision = "a30multistrat001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_risk_settings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("risk_per_trade_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("daily_stop_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("grade_risk_overrides", sa.JSON, nullable=False),
        sa.Column("grades_gate_entries", sa.Boolean, nullable=False),
        sa.Column("leverage", sa.Integer, nullable=False),
        sa.Column("margin_mode", sa.String(16), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_bot_risk_singleton"),
        sa.CheckConstraint(
            "risk_per_trade_pct > 0 AND risk_per_trade_pct <= daily_stop_pct "
            "AND daily_stop_pct <= 5",
            name="ck_bot_risk_percentages",
        ),
        sa.CheckConstraint("leverage BETWEEN 1 AND 20", name="ck_bot_risk_leverage"),
        sa.CheckConstraint("margin_mode = 'isolated'", name="ck_bot_risk_margin_mode"),
    )
    op.execute(
        "INSERT INTO bot_risk_settings "
        "(id, risk_per_trade_pct, daily_stop_pct, grade_risk_overrides, grades_gate_entries, "
        "leverage, margin_mode) VALUES (1, 1, 5, '{}', false, 10, 'isolated')"
    )
    for name in ("demo_loss_utc_date", "demo_equity_baseline_date", "demo_equity_stop_tripped_on"):
        op.add_column("account_risk_accounting_states", sa.Column(name, sa.Date))
    op.add_column(
        "account_risk_accounting_states",
        sa.Column(
            "demo_reserved_daily_loss", sa.Numeric(20, 8), nullable=False, server_default="0"
        ),
    )
    op.alter_column(
        "account_risk_accounting_states", "demo_reserved_daily_loss", server_default=None
    )
    op.add_column(
        "account_risk_accounting_states",
        sa.Column("demo_equity_baseline", sa.Numeric(20, 8)),
    )


def downgrade() -> None:
    for name in (
        "demo_reserved_daily_loss",
        "demo_equity_baseline",
        "demo_equity_stop_tripped_on",
        "demo_equity_baseline_date",
        "demo_loss_utc_date",
    ):
        op.drop_column("account_risk_accounting_states", name)
    op.drop_table("bot_risk_settings")
