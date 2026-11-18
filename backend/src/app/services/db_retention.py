"""Bounded retention for the approved data policy (H5 + 2026-11 data policy).

Kept forever (never named here): strategies and versions, setup definitions,
Brain setup projections, conversations, journal, trades/orders/fills/plans,
paper_decision and paper_trade_* setup events, knowledge documents, accounts,
organizations, settings, watchlists, alerts, auth, the Telegram outbox and
``trade_candidate_summaries`` (``KEEP_FOREVER_TABLES`` lists them for tests).

Pruned:
- 7 days: canonical candidates that never became a plan or trade (summary row
  written first, in the same transaction), candidate/eligibility-stage paper
  evaluation rows, and Telegram setup notifications.
- 90 days: audit_logs not referenced by an approval or lifecycle resolution.
- 24 hours: watcher observability events, watcher attempts, scan-stage paper
  evaluation rows, Brain ``system_observation`` events of finished setups and
  public market receipts older than the longest SFP read horizon.
- Terminal watcher lineages after ``retention_watcher_lineage_hours`` (8 days).
- BloFin demo snapshots: intra-day rows after 24h. Kept: the newest and the
  newest successful per (org, user), and the newest per UTC day forever
  (``retention_demo_snapshot_days`` = 0; a positive value caps the daily rows).

Each policy deletes in small batches by primary key so no statement holds a
long lock on a hot table. Dry-run counts what would go and writes nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

import structlog
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.config import Settings

logger = structlog.get_logger(__name__)

# watcher_observability_events has no severity column. An event counts as an
# error when its name carries one of the markers the monitoring "recent errors"
# panel uses (watcher_monitoring_service._ERROR_EVENT_MARKERS; a test keeps the
# two in sync) or when a finished scan reports a bad evaluation status.
WATCHER_ERROR_EVENT_MARKERS = ("fail", "error", "blocked", "degraded", "stale", "reject")
_WATCHER_ERROR_EVALUATION_STATUSES = ("failed", "degraded", "blocked")

# Never pruned. Not exhaustive of the schema: anything not in a policy is kept.
KEEP_FOREVER_TABLES = frozenset(
    {
        "organizations",
        "users",
        "memberships",
        "refresh_tokens",
        "user_strategies",
        "user_strategy_versions",
        "strategy_lifecycle_events",
        "setup_definitions",
        "compiled_setup_definitions",
        "strategy_brain_setups",
        "conversations",
        "conversation_messages",
        "agent_saved_entries",
        "journals",
        "journal_trades",
        "journal_lifecycle_events",
        "journal_trade_observations",
        "paper_trades",
        "paper_trade_events",
        "orders",
        "positions",
        "exchange_orders",
        "exchange_fills",
        "execution_fill_facts",
        "trade_plan_revisions",
        "canonical_trade_plan_roots",
        "canonical_trade_plan_lineage",
        "canonical_trade_plan_idempotency_keys",
        "documents",
        "chunks",
        "watcher_watchlists",
        "user_risk_settings",
        "autonomous_trading_settings",
        "user_notification_preferences",
        "telegram_security_outbox",
        "telegram_paper_threads",
        "telegram_paper_messages",
        "trade_candidate_summaries",
    }
)

# Candidate rows removed together, children first. Postgres triggers allow the
# DELETE only while alphatrade.retention_purge is set (a29datapolicy001).
CANDIDATE_TABLES = (
    "canonical_candidate_transition_keys",
    "canonical_candidate_transitions",
    "canonical_candidate_creation_keys",
    "action_eligibility_evaluations",
    "canonical_candidates",
)
_PURGE_SETTING = "alphatrade.retention_purge"
_TERMINAL_SETUP_STATES = "('INVALIDATED', 'EXPIRED', 'COMPLETED')"
_SUCCESSFUL_SNAPSHOT = "('ok', 'degraded', 'stale')"
_TRADE_LINKED = "(t.journal_trade_id IS NULL AND t.filled = false AND t.plan_approved = false)"
_SFP_KIND = "swing_failure_pattern/v1"


def _watcher_error_event_sql() -> str:
    names = " OR ".join(
        f"lower(t.name) LIKE '%{marker}%'" for marker in WATCHER_ERROR_EVENT_MARKERS
    )
    statuses = ", ".join(f"'{status}'" for status in _WATCHER_ERROR_EVALUATION_STATUSES)
    return f"({names} OR COALESCE(t.fields ->> 'evaluation_status', '') IN ({statuses}))"


def _utc_day(column: str, dialect: str) -> str:
    if dialect == "postgresql":
        return f"CAST(({column} AT TIME ZONE 'UTC') AS date)"
    return f"date({column})"


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """One table's rule. Identifiers are code constants, never user input."""

    table: str
    primary_key: str
    timestamp: str
    window: timedelta
    # Extra SQL predicate on the same row (alias ``t``). Constant text, or a
    # function of the SQL dialect name for dialect-specific expressions.
    keep_predicate: str | Callable[[str], str] = ""
    # Distinguishes two policies on one table; defaults to the table name.
    name: str = ""
    # Extra named cutoffs (``:name`` = now - delta) the predicate may use.
    extra_cutoffs: tuple[tuple[str, timedelta], ...] = ()
    # Widens ``window`` from live data (e.g. the longest SFP read horizon).
    window_for: Callable[[Session], timedelta] | None = None

    @property
    def label(self) -> str:
        return self.name or self.table

    @property
    def key_columns(self) -> tuple[str, ...]:
        return tuple(column.strip() for column in self.primary_key.split(","))


@dataclass(frozen=True, slots=True)
class RetentionResult:
    policy: str
    table: str
    cutoff: datetime
    matched: int
    deleted: int
    dry_run: bool
    complete: bool
    summarized: int = 0


def _lineage_done(alias: str) -> str:
    """A terminal lineage whose every attempt is older than the lineage window."""

    return (
        f"{alias}.terminal_status IS NOT NULL AND {alias}.created_at < :cutoff "
        "AND NOT EXISTS (SELECT 1 FROM watcher_scan_attempts AS recent "
        f"WHERE recent.lineage_id = {alias}.lineage_id AND recent.started_at >= :cutoff)"
    )


def _blofin_thinning(dialect: str, *, horizon: bool = False) -> str:
    same_day = f"{_utc_day('n.synced_at', dialect)} = {_utc_day('t.synced_at', dialect)}"
    same_user = (
        "n.user_id IS NOT DISTINCT FROM t.user_id"
        if dialect == "postgresql"
        else ("n.user_id IS t.user_id")
    )
    # Latest readers select per org and per (org, user); keep the newest of both.
    newer = (
        "SELECT 1 FROM blofin_demo_sync_snapshots AS n "
        f"WHERE n.organization_id = t.organization_id AND {same_user} "
        "AND n.synced_at > t.synced_at"
    )
    return (
        # Never the newest row (latest readers, staleness checks).
        f"EXISTS ({newer}) "
        # Never the newest successful row (successful_only readers).
        f"AND NOT (COALESCE(t.health_status, 'ok') IN {_SUCCESSFUL_SNAPSHOT} "
        f"AND NOT EXISTS ({newer} AND COALESCE(n.health_status, 'ok') IN {_SUCCESSFUL_SNAPSHOT})) "
    ) + (
        # Not the newest row of its UTC day; with a horizon, also older daily rows.
        f"AND (t.synced_at < :horizon OR EXISTS ({newer} AND {same_day}))"
        if horizon
        else f"AND EXISTS ({newer} AND {same_day})"
    )


def sfp_read_horizon(session: Session) -> timedelta:
    """Longest public-market history any stored SFP version re-reads (assembly.py).

    SFP assembly re-reads ``level_lookback + expiry_bars + quality_lookback +
    2 * pivot_width`` closed bars of stored receipts to keep original receipt
    clocks. Pruning inside that window would change detector input. Any spec
    that cannot be read raises, so the policy fails closed (keeps everything).
    """

    from app.market_contracts.identity import interval_timedelta
    from app.schemas.common import Timeframe

    horizon = timedelta(0)
    specs = session.execute(
        text("SELECT pattern_spec FROM user_strategy_versions WHERE pattern_spec IS NOT NULL")
    ).scalars()
    for spec in specs:
        if not isinstance(spec, dict) or spec.get("kind") != _SFP_KIND:
            continue
        p = spec["parameters"]
        bars = (
            int(p["level_lookback"])
            + int(p["expiry_bars"])
            + int(p["quality_lookback"])
            + 2 * int(p["pivot_width"])
            + 1  # the bar in progress
        )
        horizon = max(horizon, interval_timedelta(Timeframe(spec["trigger_timeframe"])) * bars)
    return horizon


def retention_policies(settings: Settings) -> tuple[RetentionPolicy, ...]:
    """The pruned tables and their windows. Anything not listed is kept forever."""

    error_event = _watcher_error_event_sql()
    demo_days = settings.retention_demo_snapshot_days
    attempts = timedelta(hours=settings.retention_watcher_attempts_hours)
    lineages = timedelta(hours=settings.retention_watcher_lineage_hours)
    candidates = timedelta(days=settings.retention_candidate_days)
    return (
        RetentionPolicy(
            table="watcher_observability_events",
            primary_key="event_id",
            timestamp="at",
            window=timedelta(hours=settings.retention_watcher_events_hours),
            keep_predicate=f"NOT {error_event}",
        ),
        RetentionPolicy(
            table="watcher_observability_events",
            primary_key="event_id",
            timestamp="at",
            window=timedelta(hours=settings.retention_watcher_error_events_hours),
            keep_predicate=error_event,
            name="watcher_observability_events:error",
        ),
        # Child rows of scan attempts; nothing references them. They run
        # before the parent policy so its FK check sees them gone.
        RetentionPolicy(
            table="watcher_subscription_eval_attempts",
            primary_key="subscription_attempt_id",
            timestamp="created_at",
            window=attempts,
        ),
        RetentionPolicy(
            table="watcher_source_fetch_attempts",
            primary_key="fetch_attempt_id",
            timestamp="created_at",
            window=attempts,
        ),
        # Non-terminal attempts of a succeeded lineage whose attempts are all
        # past the window. Replay reads the terminal attempt and numbers the
        # next one as len(attempts) + 1, so the terminal attempt stays here.
        RetentionPolicy(
            table="watcher_scan_attempts",
            primary_key="attempt_id",
            timestamp="started_at",
            window=attempts,
            keep_predicate=(
                "EXISTS (SELECT 1 FROM watcher_scan_lineages AS l "
                "WHERE l.lineage_id = t.lineage_id AND l.terminal_status = 'succeeded' "
                "AND l.terminal_attempt_id IS NOT NULL "
                "AND l.terminal_attempt_id <> t.attempt_id) "
                "AND NOT EXISTS (SELECT 1 FROM watcher_scan_attempts AS n "
                "WHERE n.lineage_id = t.lineage_id AND n.started_at >= :cutoff) "
                "AND NOT EXISTS (SELECT 1 FROM watcher_source_fetch_attempts AS f "
                "WHERE f.scan_attempt_id = t.attempt_id) "
                "AND NOT EXISTS (SELECT 1 FROM watcher_subscription_eval_attempts AS e "
                "WHERE e.scan_attempt_id = t.attempt_id)"
            ),
        ),
        # Whole terminal lineages once their scheduled-scan key can no longer
        # recur (keys are bucketed by the closed bar). Children first.
        RetentionPolicy(
            table="watcher_scan_attempts",
            primary_key="attempt_id",
            timestamp="started_at",
            window=lineages,
            keep_predicate=(
                "EXISTS (SELECT 1 FROM watcher_scan_lineages AS l "
                f"WHERE l.lineage_id = t.lineage_id AND {_lineage_done('l')}) "
                "AND NOT EXISTS (SELECT 1 FROM watcher_source_fetch_attempts AS f "
                "WHERE f.scan_attempt_id = t.attempt_id) "
                "AND NOT EXISTS (SELECT 1 FROM watcher_subscription_eval_attempts AS e "
                "WHERE e.scan_attempt_id = t.attempt_id)"
            ),
            name="watcher_scan_attempts:lineage",
        ),
        RetentionPolicy(
            table="watcher_scheduled_scans",
            primary_key="scheduled_scan_id",
            timestamp="created_at",
            window=lineages,
            keep_predicate=(
                "EXISTS (SELECT 1 FROM watcher_scan_lineages AS l "
                f"WHERE l.lineage_id = t.lineage_id AND {_lineage_done('l')}) "
                "AND NOT EXISTS (SELECT 1 FROM watcher_scan_attempts AS a "
                "WHERE a.lineage_id = t.lineage_id)"
            ),
        ),
        RetentionPolicy(
            table="watcher_scan_lineages",
            primary_key="lineage_id",
            timestamp="created_at",
            window=lineages,
            keep_predicate=(
                f"{_lineage_done('t')} "
                "AND NOT EXISTS (SELECT 1 FROM watcher_scan_attempts AS a "
                "WHERE a.lineage_id = t.lineage_id) "
                "AND NOT EXISTS (SELECT 1 FROM watcher_scheduled_scans AS s "
                "WHERE s.lineage_id = t.lineage_id)"
            ),
        ),
        # Scan-stage measurement noise. Anything tied to a plan, a fill or a
        # journal trade is learning evidence and is kept.
        RetentionPolicy(
            table="paper_evaluation_observations",
            primary_key="observation_id",
            timestamp="occurred_at",
            window=timedelta(hours=settings.retention_paper_evaluation_hours),
            keep_predicate=f"t.stage IN ('watcher_scan', 'setup_assessment') AND {_TRADE_LINKED}",
        ),
        # Candidate-stage rows follow the candidates (7 days). Decision, trade,
        # outcome, journal and attribution stages are kept.
        RetentionPolicy(
            table="paper_evaluation_observations",
            primary_key="observation_id",
            timestamp="occurred_at",
            window=candidates,
            keep_predicate=f"t.stage IN ('candidate', 'eligibility') AND {_TRADE_LINKED}",
            name="paper_evaluation_observations:candidate",
        ),
        # Paper Telegram setup notifications. Resend safety lives on
        # telegram_security_outbox idempotency keys, which are never pruned.
        # A candidate's alert waits until its summary row (``notified``) exists.
        RetentionPolicy(
            table="telegram_paper_notifications",
            primary_key="intent_id",
            timestamp="created_at",
            window=timedelta(hours=settings.retention_telegram_notifications_hours),
            keep_predicate=(
                "(t.candidate_id IS NULL "
                "OR EXISTS (SELECT 1 FROM trade_candidate_summaries AS s "
                "WHERE s.candidate_id = t.candidate_id) "
                "OR NOT EXISTS (SELECT 1 FROM canonical_candidates AS c "
                "WHERE c.candidate_id = t.candidate_id))"
            ),
        ),
        # Detector observations of finished (or expired) setups. Decisions and
        # paper_trade_* events are kept; a live setup keeps its observations
        # because SFP notifications re-read them while still fresh.
        RetentionPolicy(
            table="strategy_brain_setup_events",
            primary_key="id",
            timestamp="occurred_at",
            window=timedelta(hours=settings.retention_brain_observation_hours),
            keep_predicate=(
                "t.kind = 'system_observation' AND EXISTS (SELECT 1 FROM strategy_brain_setups "
                f"AS s WHERE s.id = t.setup_id AND (s.state IN {_TERMINAL_SETUP_STATES} "
                "OR s.expires_at < :cutoff))"
            ),
            name="strategy_brain_setup_events:system_observation",
        ),
        RetentionPolicy(
            table="public_market_observations",
            primary_key="identity_hash, observation_id",
            timestamp="bar_start",
            window=timedelta(hours=settings.retention_public_market_hours),
            window_for=sfp_read_horizon,
        ),
        RetentionPolicy(
            table="audit_logs",
            primary_key="id",
            timestamp="event_at",
            window=timedelta(days=settings.retention_audit_log_days),
            keep_predicate=(
                "NOT EXISTS (SELECT 1 FROM approvals AS a WHERE a.audit_event_id = t.id) "
                "AND NOT EXISTS (SELECT 1 FROM governed_demo_lifecycle_resolutions AS g "
                "WHERE g.audit_event_id = t.id) "
                "AND NOT EXISTS (SELECT 1 FROM manual_demo_lifecycle_resolutions AS m "
                "WHERE m.audit_event_id = t.id)"
            ),
        ),
        # Intra-day rows go after 24h; the newest row of each UTC day is kept
        # forever (dashboard daily P&L calendar) unless a horizon is set.
        RetentionPolicy(
            table="blofin_demo_sync_snapshots",
            primary_key="id",
            timestamp="synced_at",
            window=timedelta(hours=24),
            keep_predicate=partial(_blofin_thinning, horizon=demo_days > 0),
            extra_cutoffs=(("horizon", timedelta(days=demo_days)),) if demo_days > 0 else (),
        ),
    )


def pruned_tables(settings: Settings) -> frozenset[str]:
    return frozenset(p.table for p in retention_policies(settings)) | frozenset(CANDIDATE_TABLES)


def _where(policy: RetentionPolicy, dialect: str) -> str:
    clauses = [f"t.{policy.timestamp} < :cutoff"]
    predicate = policy.keep_predicate
    if callable(predicate):
        predicate = predicate(dialect)
    if predicate:
        clauses.append(f"({predicate})")
    return " AND ".join(clauses)


def _bound_statement(session: Session) -> None:
    """Bound waits and execution time; SET LOCAL expires at each batch commit."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SET LOCAL lock_timeout = '1s'"))
        session.execute(text("SET LOCAL statement_timeout = '60s'"))


def prune_table(
    session_factory: Callable[[], Session],
    policy: RetentionPolicy,
    *,
    now: datetime,
    dry_run: bool,
    batch_size: int,
    max_batches: int,
    should_stop: Callable[[], bool] = lambda: False,
) -> RetentionResult:
    """Delete (or count) rows older than the policy window, one batch per commit."""

    session = session_factory()
    try:
        window = policy.window
        if policy.window_for is not None:
            window = max(window, policy.window_for(session))
        cutoff = now - window
        where = _where(policy, session.get_bind().dialect.name)
        params: dict[str, object] = {"cutoff": cutoff}
        params.update({name: now - delta for name, delta in policy.extra_cutoffs})
        _bound_statement(session)
        matched = int(
            session.execute(
                text(f"SELECT count(*) FROM {policy.table} AS t WHERE {where}"), params
            ).scalar_one()
        )
        session.commit()
        if dry_run or matched == 0:
            return RetentionResult(
                policy.label, policy.table, cutoff, matched, 0, dry_run, matched == 0
            )
        deleted = 0
        keys = ", ".join(policy.key_columns)
        selected = ", ".join(f"t.{column}" for column in policy.key_columns)
        statement = text(
            f"DELETE FROM {policy.table} WHERE ({keys}) IN ("
            f"SELECT {selected} FROM {policy.table} AS t WHERE {where} "
            f"ORDER BY t.{policy.timestamp} LIMIT :batch)"
        )
        for _ in range(max_batches):
            if should_stop():
                break
            _bound_statement(session)
            outcome = session.execute(statement, {**params, "batch": batch_size})
            count = getattr(outcome, "rowcount", 0)
            session.commit()
            deleted += max(int(count or 0), 0)
            if not count or count < batch_size:
                return RetentionResult(
                    policy.label, policy.table, cutoff, matched, deleted, False, True
                )
        return RetentionResult(policy.label, policy.table, cutoff, matched, deleted, False, False)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_retention(
    session_factory: Callable[[], Session],
    settings: Settings,
    *,
    now: datetime,
    dry_run: bool | None = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> tuple[RetentionResult, ...]:
    """Run every policy once. One table failing does not stop the others.

    Candidates run first so their summaries exist before the notification
    policy, which waits for them, runs.
    """

    from app.services.candidate_retention import purge_candidates

    effective_dry_run = settings.retention_dry_run if dry_run is None else dry_run
    results: list[RetentionResult] = []
    steps: list[tuple[str, str, Callable[[], RetentionResult]]] = [
        (
            "canonical_candidates",
            "canonical_candidates",
            lambda: purge_candidates(
                session_factory,
                settings,
                now=now,
                dry_run=effective_dry_run,
                should_stop=should_stop,
            ),
        )
    ]
    for policy in retention_policies(settings):
        steps.append(
            (
                policy.label,
                policy.table,
                lambda policy=policy: prune_table(  # type: ignore[misc]
                    session_factory,
                    policy,
                    now=now,
                    dry_run=effective_dry_run,
                    batch_size=settings.retention_batch_size,
                    max_batches=settings.retention_max_batches_per_table,
                    should_stop=should_stop,
                ),
            )
        )
    for label, table, step in steps:
        if should_stop():
            break
        try:
            result = step()
        except Exception as exc:
            # Never log exception text: DB errors can carry SQL and values.
            logger.error(
                "db_retention_table_failed", policy=label, table=table, error=type(exc).__name__
            )
            continue
        results.append(result)
        logger.info(
            "db_retention_table",
            policy=result.policy,
            table=result.table,
            cutoff=result.cutoff.isoformat(),
            matched=result.matched,
            deleted=result.deleted,
            summarized=result.summarized,
            dry_run=result.dry_run,
            complete=result.complete,
        )
    return tuple(results)


def tables_with_deletes(results: Iterable[RetentionResult]) -> tuple[str, ...]:
    """Tables a pass deleted rows from, in a stable order (candidate children included)."""

    tables: dict[str, None] = {}
    for result in results:
        if result.deleted <= 0:
            continue
        if result.table == "canonical_candidates":
            tables.update(dict.fromkeys(CANDIDATE_TABLES))
        else:
            tables[result.table] = None
    return tuple(tables)


def vacuum_tables(
    session_factory: Callable[[], Session],
    settings: Settings,
    tables: Iterable[str],
    *,
    full: bool = False,
) -> dict[str, str]:
    """``VACUUM (ANALYZE)`` (or ``VACUUM FULL`` for ``--reclaim``) each table; Postgres only.

    VACUUM cannot run inside a transaction, so it uses an autocommit
    connection. FULL rewrites the table under an ACCESS EXCLUSIVE lock; use it
    once, for the first big cleanup, in a quiet window. Only retention tables
    are accepted. One table failing does not stop the others.
    """

    allowed = pruned_tables(settings)
    outcome: dict[str, str] = {}
    session = session_factory()
    try:
        bind = session.get_bind()
    finally:
        session.close()
    engine = bind if isinstance(bind, Engine) else bind.engine
    if engine.dialect.name != "postgresql":
        return outcome
    command = "VACUUM (FULL, ANALYZE)" if full else "VACUUM (ANALYZE)"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("SET lock_timeout = '10s'"))
        for table in tables:
            if table not in allowed:
                raise ValueError(f"not a retention table: {table}")
            try:
                connection.execute(text(f"{command} {table}"))
                outcome[table] = "ok"
            except Exception as exc:
                outcome[table] = type(exc).__name__
            logger.info("db_retention_vacuum", table=table, full=full, result=outcome[table])
    return outcome
