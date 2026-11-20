"""Data retention policy: candidate summaries, retention indexes, gated candidate purge.

1. ``trade_candidate_summaries``: one compact, kept-forever row per canonical
   candidate, written by the nightly retention pass before full rows go.
2. Time indexes the retention deletes filter and order by (built CONCURRENTLY).
3. Candidate append-only triggers (canonical candidate tables and
   ``action_eligibility_evaluations``) now allow DELETE only inside a
   transaction that set ``alphatrade.retention_purge = 'canonical_candidates'``
   (``set_config(..., true)``, transaction-local). UPDATE stays forbidden, and
   every other session still gets the original append-only error. Plan roots,
   plan lineage and plan idempotency keys keep their strict triggers, so a
   candidate that became a plan can never be purged (its FK also blocks it).

Downgrade restores the original strict triggers, drops the indexes and the
summary table (summaries are derived data; downgrade loses them).

Revision ID: a29datapolicy001
Revises: a28promptsha001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a29datapolicy001"
down_revision: str | None = "a28promptsha001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUMMARY = "trade_candidate_summaries"
_GUARD = "alphatrade_canonical_candidate_retention_guard"
_CANDIDATE_FUNCTION = "alphatrade_forbid_canonical_candidate_history_mutation"
_PLAN_FUNCTION = "alphatrade_forbid_canonical_plan_history_mutation"
# (trigger, table, events, original function) exactly as 4fd8c1a90b27 / c9e2b4a1d078 made them.
_TRIGGERS = (
    ("trg_canonical_candidates_no_delete", "canonical_candidates", "DELETE", _CANDIDATE_FUNCTION),
    (
        "trg_canonical_candidate_creation_keys_append_only",
        "canonical_candidate_creation_keys",
        "UPDATE OR DELETE",
        _CANDIDATE_FUNCTION,
    ),
    (
        "trg_canonical_candidate_transitions_append_only",
        "canonical_candidate_transitions",
        "UPDATE OR DELETE",
        _CANDIDATE_FUNCTION,
    ),
    (
        "trg_canonical_candidate_transition_keys_append_only",
        "canonical_candidate_transition_keys",
        "UPDATE OR DELETE",
        _CANDIDATE_FUNCTION,
    ),
    (
        "trg_action_eligibility_evaluations_append_only",
        "action_eligibility_evaluations",
        "UPDATE OR DELETE",
        _PLAN_FUNCTION,
    ),
)
# (name, table, columns)
_INDEXES = (
    ("ix_audit_logs_event_at", "audit_logs", ["event_at"]),
    ("ix_brain_event_kind_time", "strategy_brain_setup_events", ["kind", "occurred_at"]),
    ("ix_canonical_candidates_created_at", "canonical_candidates", ["created_at"]),
    ("ix_tg_paper_notify_created_at", "telegram_paper_notifications", ["created_at"]),
    ("ix_public_observation_bar_start", "public_market_observations", ["bar_start"]),
)


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _point_triggers(function_for: dict[str, str]) -> None:
    for trigger, table, events, original in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute(
            f"CREATE TRIGGER {trigger} BEFORE {events} ON {table} "
            f"FOR EACH ROW EXECUTE PROCEDURE {function_for.get(table, original)}()"
        )


def upgrade() -> None:
    op.create_table(
        _SUMMARY,
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("symbol", sa.String(length=80), nullable=False),
        sa.Column("venue", sa.String(length=64), nullable=True),
        sa.Column("timeframe", sa.String(length=32), nullable=True),
        sa.Column("strategy_version_id", sa.Uuid(), nullable=True),
        sa.Column("setup_definition_id", sa.Uuid(), nullable=True),
        sa.Column("brain_setup_id", sa.Uuid(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("final_state", sa.String(length=32), nullable=True),
        sa.Column("planned_entry", sa.Numeric(28, 12), nullable=True),
        sa.Column("planned_stop", sa.Numeric(28, 12), nullable=True),
        sa.Column("planned_target", sa.Numeric(28, 12), nullable=True),
        sa.Column("notified", sa.Boolean(), nullable=False),
        sa.Column("taken", sa.Boolean(), nullable=False),
        sa.Column("journal_trade_id", sa.Uuid(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("outcome_source", sa.String(length=32), nullable=False),
        sa.Column("realized_net_pnl", sa.Numeric(28, 12), nullable=True),
        sa.Column("summarized_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("candidate_id", name=op.f("pk_trade_candidate_summaries")),
    )
    op.create_index(
        "ix_trade_candidate_summaries_org_detected",
        _SUMMARY,
        ["organization_id", "detected_at"],
    )
    op.create_index(
        "ix_trade_candidate_summaries_org_strategy",
        _SUMMARY,
        ["organization_id", "strategy_version_id"],
    )
    if _is_postgres():
        op.execute("SET LOCAL lock_timeout = '5s'")
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {_GUARD}()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
              IF TG_OP = 'DELETE'
                 AND current_setting('alphatrade.retention_purge', true) = 'canonical_candidates'
              THEN
                RETURN OLD;
              END IF;
              IF TG_TABLE_NAME = 'action_eligibility_evaluations' THEN
                RAISE EXCEPTION 'canonical plan and eligibility history is append-only';
              END IF;
              RAISE EXCEPTION 'canonical candidate history is append-only';
            END;
            $$;
            """
        )
        _point_triggers({table: _GUARD for _, table, _, _ in _TRIGGERS})
    # CONCURRENTLY so hot tables keep taking writes while the index builds.
    with op.get_context().autocommit_block():
        for name, table, columns in _INDEXES:
            op.create_index(
                name, table, columns, postgresql_concurrently=True, if_not_exists=True
            )


def downgrade() -> None:
    # Transactional drops (no CONCURRENTLY/autocommit block): an autocommit block
    # would commit every newer revision's pending downgrade, and these drops,
    # before an older revision's guard (a28promptsha001) refuses the rest.
    # lock_timeout bounds the ACCESS EXCLUSIVE lock a plain DROP INDEX takes.
    if _is_postgres():
        op.execute("SET LOCAL lock_timeout = '5s'")
    for name, table, _columns in reversed(_INDEXES):
        op.drop_index(name, table_name=table, if_exists=True)
    if _is_postgres():
        _point_triggers({})
        op.execute(f"DROP FUNCTION IF EXISTS {_GUARD}()")
    op.drop_index("ix_trade_candidate_summaries_org_strategy", table_name=_SUMMARY)
    op.drop_index("ix_trade_candidate_summaries_org_detected", table_name=_SUMMARY)
    op.drop_table(_SUMMARY)
