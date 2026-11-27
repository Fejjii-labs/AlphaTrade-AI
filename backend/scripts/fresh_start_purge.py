"""Fresh-start purge of old trading history on the Render staging Postgres.

Owner approval: Sofien, 2026-11-22 (erase all trade history; keep users/orgs/auth,
strategy definitions and versions, bot/autonomy/risk settings, exchange
connections, schema, configuration and reference data).

Dry-run by default: prints exact row counts for every PURGE and KEEP table in a
read-only transaction, plus any live table this script does not classify.
``--execute --confirm FRESH-START-2026-11-22`` truncates the PURGE tables in a
single transaction (one ``TRUNCATE ... RESTRICT``, never ``CASCADE``). KEEP
tables are locked ``IN SHARE MODE`` first. The transaction rolls back if any
KEEP count changes or any PURGE table is not empty afterwards.

``--execute`` also refuses while ``positions`` or ``journal_trades`` (where
BloFin demo trades are marked open) has an open row, unless
``--allow-open-positions`` is given. Dry-run prints those counts.

TRUNCATE bypasses the row-level append-only DELETE guards on canonical
candidates, plan revisions and demo lifecycle resolutions. That is
intentional for this one owner-approved reset. Stop the API and workers before
``--execute``.

Reads the URL from ``ALPHATRADE_STAGING_PG_URL`` and never prints it. Refuses
any host other than the staging database.

    uv run python scripts/fresh_start_purge.py                 # dry-run
    uv run python scripts/fresh_start_purge.py --execute --confirm FRESH-START-2026-11-22
        [--allow-open-positions]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

URL_ENV = "ALPHATRADE_STAGING_PG_URL"
STAGING_HOST = "dpg-d8fuok57vvec739lm0u0-a"
CONFIRM_TOKEN = "FRESH-START-2026-11-22"

# Children first: every table precedes the PURGE tables it references.
PURGE_TABLES: dict[str, str] = {
    "paper_validation_session_results": "results of old paper run sessions",
    "paper_validation_session_observations": "observations of old paper run sessions",
    "paper_validation_run_sessions": "run sessions from old paper plans",
    "paper_signal_orchestration_decisions": "orchestration decisions for old signals",
    "venue_submit_effects": "old venue submit effects",
    "tradingview_signals": "old inbound TradingView alert signals",
    "risk_reservations": "exposure reservations of old commands",
    "plan_entry_execution_claims": "old one-entry-per-plan claims",
    "paper_validation_run_plans": "run plans from old paper candidates",
    "learning_attribution_events": "events of old learning attributions",
    "governed_demo_lifecycle_resolutions": "old governed demo exit proofs",
    "execution_transitions": "old execution receipt transitions",
    "execution_projections": "old execution receipt projections",
    "execution_fill_facts": "old venue fill facts",
    "paper_validation_candidates": "candidates from old paper drafts",
    "market_watcher_bridge_decisions": "watcher to paper bridge decisions",
    "manual_demo_lifecycle_resolutions": "old manual demo exit proofs",
    "learning_attribution_records": "learning attribution of old journal trades",
    "journal_trade_venue_corrections": "journal venue corrections",
    "journal_trade_rule_checks": "journal rule checks",
    "journal_trade_observations": "journal observations",
    "journal_trade_evidence": "journal evidence",
    "journal_trade_attachments": "journal attachments",
    "journal_projection_receipts": "journal projector receipts",
    "journal_lifecycle_events": "journal lifecycle events",
    "execution_receipts": "old execution receipts",
    "exchange_fills": "old BloFin demo fills",
    "paper_validation_drafts": "drafts from old paper alerts",
    "market_watcher_observations": "watcher observations (evidence)",
    "journal_trades": "journal trades (P&L, calendar)",
    "execution_idempotency_bindings": "old execution idempotency bindings",
    "execution_commands": "old execute-plan commands",
    "exchange_orders": "old orders mirrored to BloFin demo",
    "paper_validation_metric_snapshots": "metric snapshots of old paper trades",
    "paper_validation_alerts": "paper validation alerts",
    "paper_trade_events": "paper trade lifecycle events",
    "orders": "old orders",
    "approval_authorizations": "authorizations for old plans",
    "paper_trades": "simulated paper trades",
    "lesson_candidates": "lessons derived from old journals",
    "canonical_trade_plan_lineage": "old plan lineage",
    "canonical_trade_plan_idempotency_keys": "old plan idempotency keys",
    "approvals": "approval requests for old plans",
    "trade_plan_revisions": "old trade plan revisions",
    "strategy_brain_setup_events": "event history of old brain setups",
    "paper_validation_sample_windows": "sample windows of old paper runs",
    "paper_validation_runtime_history": "runtime history of old paper runs",
    "paper_validation_observability_events": "observability events of old paper runs",
    "paper_signals": "paper validation detected setups",
    "journals": "trade journal rows",
    "strategy_brain_setups": "old live setups tracked by strategy brain",
    "risk_policy_trade_records": "per-trade effective-risk records",
    "positions": "old positions",
    "paper_validation_runs": "paper validation runs",
    "canonical_trade_plan_roots": "old canonical plan roots",
    "watcher_subscription_eval_attempts": "watcher subscription evaluation attempts",
    "watcher_source_fetch_attempts": "watcher source fetch attempts",
    "trade_proposals": "old trade proposals",
    "setup_detections": "worker setup detections (alerts)",
    "canonical_candidate_transition_keys": "old candidate transition keys",
    "blofin_activity_facts": "imported BloFin demo activity",
    "watcher_scheduled_scans": "watcher scheduled scans",
    "watcher_scan_attempts": "watcher scan attempts",
    "telegram_paper_messages": "messages in old Telegram trade threads",
    "telegram_paper_confirmations": "confirmations of old trade actions",
    "strategy_signals": "old strategy signals",
    "strategy_performance_daily": "per-strategy daily P&L rollup",
    "setup_performance": "per-setup win/loss/P&L stats",
    "setup_lifetime_pins": "trigger pins of old setup lifetimes",
    "risk_events": "risk-rule trigger history",
    "position_exit_events": "old position exit events",
    "performance_snapshots": "account performance aggregates",
    "market_watcher_scan_records": "watcher scan summaries",
    "market_scan_runs": "worker scan cycles",
    "journal_import_batches": "journal import batches",
    "canonical_candidate_transitions": "old candidate transitions",
    "canonical_candidate_creation_keys": "old candidate creation keys",
    "blofin_demo_sync_snapshots": "BloFin demo account/position snapshots",
    "action_eligibility_evaluations": "eligibility verdicts on old candidates",
    "daily_risk_states": "daily P&L plus lock flag",
    "account_risk_accounting_states": "exposure ledger, demo equity baseline",
    "account_safety_epochs": "account safety epochs",
    "risk_policy_breaker_states": "drawdown/loss breaker latches",
    "risk_policy_breaker_intervals": "breaker window history",
    "risk_policy_breaker_resets": "human breaker resets",
    "risk_policy_calibration_samples": "past-trade R samples for grader",
    "risk_policy_grade_bindings": "first grade per setup",
    "watcher_symbol_status": "latest per-symbol watcher status",
    "market_monitor_snapshots": "latest monitor state",
    "watcher_scan_lineages": "watcher scan lineages",
    "watcher_observability_events": "watcher observability events",
    "watcher_health_snapshots": "watcher health snapshots",
    "trade_candidate_summaries": "retention summaries of old candidates",
    "telegram_security_outbox": "Telegram delivery outbox",
    "telegram_paper_threads": "Telegram threads about old trades",
    "telegram_paper_notifications": "Telegram trade alert notifications",
    "paper_evaluation_observations": "per-candidate evaluation outcomes and P&L",
    "canonical_candidates": "old trade candidates",
    "action_eligibility_identity_bindings": "eligibility identity bindings",
}

KEEP_TABLES: dict[str, str] = {
    "alembic_version": "schema version",
    "organizations": "orgs",
    "users": "users",
    "memberships": "org membership",
    "organization_invitations": "invitations",
    "organization_quotas": "per-org limits",
    "refresh_tokens": "auth sessions",
    "password_reset_tokens": "auth",
    "email_verification_tokens": "auth",
    "user_two_factor": "2FA enrolment (encrypted TOTP secret)",
    "two_factor_recovery_codes": "2FA recovery codes (hashed)",
    "two_factor_trusted_devices": "2FA remembered devices",
    "telegram_security_bindings": "Telegram account binding",
    "telegram_security_enrollment_challenges": "Telegram enrollment",
    "telegram_security_action_nonces": "Telegram replay protection",
    "telegram_security_action_receipts": "Telegram security receipts",
    "telegram_security_authorization_intents": "Telegram authorization intents",
    "telegram_security_audit_events": "Telegram security audit",
    "telegram_activation_send_ledger": "Telegram activation idempotency",
    "telegram_activation_inbound_cursors": "Telegram update cursor",
    "setup_definitions": "setup definitions",
    "global_setup_templates": "setup templates",
    "compiled_setup_definitions": "compiled strategy artifacts",
    "setup_migration_runs": "setup migration reports",
    "user_strategies": "strategy library",
    "user_strategy_versions": "strategy versions",
    "strategy_lifecycle_events": "strategy approval history",
    "strategy_conversation_proposals": "strategy rule drafts",
    "strategy_version_conversation_links": "strategy version provenance",
    "bot_risk_settings": "bot risk settings",
    "user_risk_settings": "user risk settings",
    "autonomous_trading_settings": "autonomy settings",
    "autonomous_paper_switches": "autonomy switch",
    "kill_switch_states": "kill switch",
    "paper_validation_scheduler_configs": "scheduler config",
    "user_notification_preferences": "notification settings",
    "risk_policy_proposals": "risk policy requests",
    "risk_policy_versions": "applied risk profiles",
    "risk_policy_version_cancellations": "risk profile cancellations",
    "risk_policy_promotion_reports": "grader promotion reports",
    "watcher_policy_versions": "watcher policy config",
    "watcher_strategy_assignments": "watcher assignments",
    "watcher_assignment_receipts": "assignment idempotency",
    "watcher_watchlists": "watcher watchlists",
    "watchlist_items": "user watchlist",
    "manual_chart_levels": "user chart levels",
    "manual_level_revisions": "user chart level history",
    "exchange_accounts": "exchange connections",
    "execution_accounts": "paper/demo account identity",
    "blofin_activity_accounts": "BloFin account binding",
    "historical_candles": "market data",
    "market_snapshots": "market data",
    "indicator_snapshots": "market data",
    "public_market_observations": "public OHLCV",
    "backtest_datasets": "backtest datasets",
    "documents": "knowledge base",
    "chunks": "knowledge base",
    "knowledge_indexing_jobs": "knowledge base",
    "worker_heartbeats": "worker liveness",
    "worker_runtime_state": "worker runtime",
    "controlled_runtime_status": "runtime status",
    "binance_rest_coordination": "REST coordination",
    "watcher_heartbeats": "watcher liveness",
    "watcher_worker_leases": "watcher leases",
    "billing_customers": "billing",
    "billing_events": "billing",
    "subscriptions": "billing",
    "webhook_events": "billing webhooks",
    "usage_events": "usage metering",
    "usage_export_batches": "usage metering",
    "model_call_attempts": "LLM telemetry",
    "agent_saved_entries": "user notes",
    "agent_capture_sources": "capture dedupe",
    "voice_session_reservations": "voice quota",
    # Trade-derived, but kept by owner decision (2026-11-22): audit, import
    # cursors, research and chat.
    "audit_logs": "audit trail includes old trade audit rows",
    "blofin_activity_cursors": "import cursors (stop re-import)",
    "backtest_runs": "backtest research",
    "backtest_trades": "simulated backtest trades",
    "experiments": "experiment definitions",
    "experiment_versions": "experiment configs",
    "experiment_events": "experiment history",
    "experiment_samples": "experiment evidence samples",
    "trendpulse_screening_runs": "research screening evidence",
    "conversations": "agent chat",
    "conversation_messages": "agent chat",
}

# PURGE tables that track open paper/demo positions. Enums are stored by name.
OPEN_POSITION_TABLES: tuple[str, ...] = ("positions", "journal_trades")

# Live foreign keys between public tables: (child, parent).
_FK_SQL = """
SELECT DISTINCT c.relname, p.relname
FROM pg_constraint k
JOIN pg_class c ON c.oid = k.conrelid
JOIN pg_class p ON p.oid = k.confrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE k.contype = 'f' AND n.nspname = 'public'
"""

_TABLES_SQL = """
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
"""


class PurgeRefusedError(RuntimeError):
    """A guard refused the run. The message never contains the URL."""


def check_staging_url(raw_url: str | None) -> str:
    """Return a psycopg SQLAlchemy URL for the staging host, or refuse."""
    from sqlalchemy.engine import make_url

    if not raw_url:
        raise PurgeRefusedError(f"{URL_ENV} is not set")
    try:
        url = make_url(raw_url.strip())
    except Exception:  # parser errors can echo the URL
        raise PurgeRefusedError(f"{URL_ENV} is not a valid database URL") from None
    if url.get_backend_name() not in {"postgres", "postgresql"}:
        raise PurgeRefusedError(f"{URL_ENV} is not a PostgreSQL URL")
    host = (url.host or "").lower()
    if host != STAGING_HOST and not host.startswith(STAGING_HOST + "."):
        raise PurgeRefusedError(f"{URL_ENV} host is not the Render staging database")
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def classification_errors(
    live_tables: Sequence[str], fk_edges: Sequence[tuple[str, str]]
) -> list[str]:
    """Problems that make ``--execute`` unsafe. Empty means safe to truncate."""
    errors: list[str] = []
    overlap = sorted(set(PURGE_TABLES) & set(KEEP_TABLES))
    if overlap:
        errors.append(f"tables in both PURGE and KEEP: {overlap}")
    live = set(live_tables)
    unknown = sorted(live - set(PURGE_TABLES) - set(KEEP_TABLES))
    if unknown:
        errors.append(f"unclassified live tables: {unknown}")
    missing = sorted(set(PURGE_TABLES) - live)
    if missing:
        errors.append(f"PURGE tables missing from database: {missing}")
    for child, parent in sorted(set(fk_edges)):
        if parent in PURGE_TABLES and child not in PURGE_TABLES:
            errors.append(f"KEEP/unclassified table {child} references PURGE table {parent}")
    return errors


def truncate_sql(tables: Sequence[str]) -> str:
    for name in tables:
        if name not in PURGE_TABLES or not name.replace("_", "").isalnum():
            raise PurgeRefusedError(f"refusing to truncate non-PURGE table {name!r}")
    return "TRUNCATE TABLE " + ", ".join(f'"{t}"' for t in tables) + " RESTRICT"


def lock_keep_sql(tables: Sequence[str]) -> str:
    """Block writes to KEEP tables so the before/after comparison is exact."""
    for name in tables:
        if name not in KEEP_TABLES or not name.replace("_", "").isalnum():
            raise PurgeRefusedError(f"refusing to lock non-KEEP table {name!r}")
    return "LOCK TABLE " + ", ".join(f'"{t}"' for t in tables) + " IN SHARE MODE"


def open_positions_sql(table: str) -> str:
    if table not in OPEN_POSITION_TABLES:
        raise PurgeRefusedError(f"not an open-position table: {table!r}")
    return f"SELECT count(*) FROM \"{table}\" WHERE upper(status::text) = 'OPEN'"


def open_position_errors(open_counts: Mapping[str, int], *, allow: bool) -> list[str]:
    """Refuse ``--execute`` while positions are open, unless overridden."""
    if allow:
        return []
    return [
        f"{t} has {n} open row(s); close them or pass --allow-open-positions"
        for t, n in open_counts.items()
        if n
    ]


def count_errors(
    before: Mapping[str, int], after: Mapping[str, int], keep: Sequence[str]
) -> list[str]:
    errors = [
        f"KEEP table {t} changed: {before.get(t)} -> {after.get(t)}"
        for t in keep
        if before.get(t) != after.get(t)
    ]
    errors += [f"PURGE table {t} not empty: {after.get(t)}" for t in PURGE_TABLES if after.get(t)]
    return errors


def _counts(conn, tables: Sequence[str]) -> dict[str, int]:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    return {t: int(conn.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one()) for t in tables}


def _print_counts(label: str, tables: Sequence[str], before, after=None) -> None:  # type: ignore[no-untyped-def]
    total = 0
    print(f"== {label} ({len(tables)} tables) ==")
    for t in tables:
        total += before[t]
        reason = PURGE_TABLES.get(t) or KEEP_TABLES.get(t, "")
        tail = f" after={after[t]}" if after is not None else ""
        print(f"{t:<42} rows={before[t]:>9}{tail}  # {reason}")
    print(f"{label} total rows={total}")


def run(*, execute: bool, allow_open_positions: bool = False) -> int:
    from sqlalchemy import create_engine, text

    url = check_staging_url(os.environ.get(URL_ENV))
    options = "-c statement_timeout=300000 -c lock_timeout=10000"
    if not execute:
        options += " -c default_transaction_read_only=on"
    engine = create_engine(url, connect_args={"options": options}, pool_pre_ping=False)
    with engine.connect() as conn:
        with conn.begin() as tx:
            print(f"database={conn.execute(text('SELECT current_database()')).scalar_one()}")
            print(f"mode={'EXECUTE' if execute else 'DRY-RUN (read-only transaction)'}")
            live = [r[0] for r in conn.execute(text(_TABLES_SQL))]
            edges = [(r[0], r[1]) for r in conn.execute(text(_FK_SQL))]
            errors = classification_errors(live, edges)
            purge = [t for t in PURGE_TABLES if t in live]
            keep = [t for t in KEEP_TABLES if t in live]
            unknown = sorted(set(live) - set(PURGE_TABLES) - set(KEEP_TABLES))
            absent_keep = sorted(set(KEEP_TABLES) - set(live))
            if execute and not errors:
                conn.execute(text(lock_keep_sql(keep)))
            before = _counts(conn, purge + keep + unknown)
            open_counts = {
                t: int(conn.execute(text(open_positions_sql(t))).scalar_one())
                for t in OPEN_POSITION_TABLES
                if t in live
            }
            print(
                "open positions: "
                + (" ".join(f"{t}={n}" for t, n in open_counts.items()) or "none tracked")
                + (" (override: --allow-open-positions)" if allow_open_positions else "")
            )
            if not execute:
                _print_counts("PURGE", purge, before)
                _print_counts("KEEP", keep, before)
                if unknown:
                    _print_counts("UNCLASSIFIED", unknown, before)
                if absent_keep:
                    print(f"KEEP tables not present in database: {absent_keep}")
                print("guard errors:" if errors else "guard errors: none")
                for e in errors:
                    print(f"  - {e}")
                tx.rollback()
                return 1 if errors else 0
            errors += open_position_errors(open_counts, allow=allow_open_positions)
            if errors:
                for e in errors:
                    print(f"REFUSED: {e}")
                tx.rollback()
                return 2
            conn.execute(text(truncate_sql(purge)))
            after = _counts(conn, purge + keep)
            problems = count_errors(before, after, keep)
            _print_counts("PURGE", purge, before, after)
            _print_counts("KEEP", keep, before, after)
            if problems:
                for p in problems:
                    print(f"REFUSED: {p}")
                tx.rollback()
                print("rolled back; nothing changed")
                return 3
        print("committed")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="count rows only (default)")
    mode.add_argument("--execute", action="store_true", help="truncate PURGE tables")
    parser.add_argument("--confirm", default="", help=f"must equal {CONFIRM_TOKEN} with --execute")
    parser.add_argument(
        "--allow-open-positions",
        action="store_true",
        help="with --execute, truncate even if positions/journal_trades have open rows",
    )
    args = parser.parse_args(argv)
    if args.execute and args.confirm != CONFIRM_TOKEN:
        print(f"REFUSED: --execute requires --confirm {CONFIRM_TOKEN}")
        return 2
    try:
        return run(execute=args.execute, allow_open_positions=args.allow_open_positions)
    except PurgeRefusedError as exc:
        print(f"REFUSED: {exc}")
        return 2
    except Exception as exc:  # driver messages may include host details
        print(f"FAILED: {type(exc).__name__} (details suppressed; nothing committed)")
        return 4


if __name__ == "__main__":
    sys.exit(main())
