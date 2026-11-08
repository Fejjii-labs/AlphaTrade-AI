"""Bounded retention for append-only runtime tables (H5).

Only operational logs are pruned. Trades, journal, orders, fills, plans,
candidates, strategies, audit logs, P&L and notes are never touched. Plans
and candidates have no DELETE triggers in Postgres; they are protected only by
not being in the static allowlist (``retention_policies``), so adding them
needs the owner's OK first. Each
policy deletes in small batches by primary key so no statement holds a long
lock on a hot table. Dry-run counts what would go and deletes nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings

logger = structlog.get_logger(__name__)

# watcher_observability_events has no severity column. An event counts as an
# error when its name carries one of the markers the monitoring "recent errors"
# panel uses (watcher_monitoring_service._ERROR_EVENT_MARKERS; a test keeps the
# two in sync) or when a finished scan reports a bad evaluation status.
WATCHER_ERROR_EVENT_MARKERS = ("fail", "error", "blocked", "degraded", "stale", "reject")
_WATCHER_ERROR_EVALUATION_STATUSES = ("failed", "degraded", "blocked")


def _watcher_error_event_sql() -> str:
    names = " OR ".join(
        f"lower(t.name) LIKE '%{marker}%'" for marker in WATCHER_ERROR_EVENT_MARKERS
    )
    statuses = ", ".join(f"'{status}'" for status in _WATCHER_ERROR_EVALUATION_STATUSES)
    return f"({names} OR COALESCE(t.fields ->> 'evaluation_status', '') IN ({statuses}))"


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """One table's rule. Identifiers are code constants, never user input."""

    table: str
    primary_key: str
    timestamp: str
    window: timedelta
    # Extra SQL predicate on the same row (alias ``t``). Constant text only.
    keep_predicate: str = ""
    # Always keep the newest row per organization (latest-row readers).
    keep_newest_per_org: bool = False
    # Distinguishes two policies on one table; defaults to the table name.
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or self.table


@dataclass(frozen=True, slots=True)
class RetentionResult:
    policy: str
    table: str
    cutoff: datetime
    matched: int
    deleted: int
    dry_run: bool
    complete: bool


def retention_policies(settings: Settings) -> tuple[RetentionPolicy, ...]:
    """The pruned tables and their windows. Anything not listed is kept forever."""

    error_event = _watcher_error_event_sql()
    return (
        RetentionPolicy(
            table="watcher_observability_events",
            primary_key="event_id",
            timestamp="at",
            window=timedelta(hours=settings.retention_watcher_events_hours),
            keep_predicate=f"NOT {error_event}",
        ),
        # Errors stay longer so a failure is still visible after a weekend.
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
            window=timedelta(hours=settings.retention_watcher_attempts_hours),
        ),
        RetentionPolicy(
            table="watcher_source_fetch_attempts",
            primary_key="fetch_attempt_id",
            timestamp="created_at",
            window=timedelta(hours=settings.retention_watcher_attempts_hours),
        ),
        # Parent scan attempts. Replay of a succeeded lineage reads its
        # terminal attempt, and attempt_number is len(attempts) + 1, so only
        # the non-terminal attempts of a succeeded lineage whose attempts are
        # all past the window may go. Lineages are never pruned.
        RetentionPolicy(
            table="watcher_scan_attempts",
            primary_key="attempt_id",
            timestamp="started_at",
            window=timedelta(hours=settings.retention_watcher_attempts_hours),
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
        # Scan-stage observations only. Anything tied to a plan, a fill or a
        # journal trade is learning evidence and is kept.
        RetentionPolicy(
            table="paper_evaluation_observations",
            primary_key="observation_id",
            timestamp="occurred_at",
            window=timedelta(hours=settings.retention_paper_evaluation_hours),
            keep_predicate=(
                "t.journal_trade_id IS NULL AND t.filled = false AND t.plan_approved = false"
            ),
        ),
        # Paper Telegram notification identity rows. Resend safety lives on
        # telegram_security_outbox idempotency keys, which are never pruned, and
        # the intent id is a uuid5 of the identity, so a re-projection re-creates
        # the same id. Threads, messages and confirmations are kept.
        RetentionPolicy(
            table="telegram_paper_notifications",
            primary_key="intent_id",
            timestamp="created_at",
            window=timedelta(hours=settings.retention_telegram_notifications_hours),
        ),
        RetentionPolicy(
            table="blofin_demo_sync_snapshots",
            primary_key="id",
            timestamp="synced_at",
            window=timedelta(days=settings.retention_demo_snapshot_days),
            keep_newest_per_org=True,
        ),
    )


def _where(policy: RetentionPolicy) -> str:
    clauses = [f"t.{policy.timestamp} < :cutoff"]
    if policy.keep_predicate:
        clauses.append(f"({policy.keep_predicate})")
    if policy.keep_newest_per_org:
        clauses.append(
            f"EXISTS (SELECT 1 FROM {policy.table} AS newer "
            "WHERE newer.organization_id = t.organization_id "
            f"AND newer.{policy.timestamp} > t.{policy.timestamp})"
        )
    return " AND ".join(clauses)


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

    cutoff = now - policy.window
    where = _where(policy)
    session = session_factory()
    try:
        matched = int(
            session.execute(
                text(f"SELECT count(*) FROM {policy.table} AS t WHERE {where}"),
                {"cutoff": cutoff},
            ).scalar_one()
        )
        session.commit()
        if dry_run or matched == 0:
            return RetentionResult(
                policy.label, policy.table, cutoff, matched, 0, dry_run, matched == 0
            )
        deleted = 0
        statement = text(
            f"DELETE FROM {policy.table} WHERE {policy.primary_key} IN ("
            f"SELECT t.{policy.primary_key} FROM {policy.table} AS t WHERE {where} "
            f"ORDER BY t.{policy.timestamp} LIMIT :batch)"
        )
        for _ in range(max_batches):
            if should_stop():
                break
            outcome = session.execute(statement, {"cutoff": cutoff, "batch": batch_size})
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
    """Run every policy once. One table failing does not stop the others."""

    effective_dry_run = settings.retention_dry_run if dry_run is None else dry_run
    results: list[RetentionResult] = []
    for policy in retention_policies(settings):
        if should_stop():
            break
        try:
            result = prune_table(
                session_factory,
                policy,
                now=now,
                dry_run=effective_dry_run,
                batch_size=settings.retention_batch_size,
                max_batches=settings.retention_max_batches_per_table,
                should_stop=should_stop,
            )
        except Exception as exc:
            logger.error(
                "db_retention_table_failed",
                policy=policy.label,
                table=policy.table,
                error=type(exc).__name__,
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
            dry_run=result.dry_run,
            complete=result.complete,
        )
    return tuple(results)
