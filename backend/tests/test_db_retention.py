"""H5: retention prunes only old operational rows, in batches, and dry-run deletes nothing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.services.db_retention import retention_policies, run_retention
from app.workers.db_retention import DbRetentionCycle

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)

# SQLite stand-in only; Python 3.12 deprecates its default datetime adapter.
pytestmark = pytest.mark.filterwarnings("ignore:The default datetime adapter:DeprecationWarning")

_DDL = (
    "CREATE TABLE watcher_observability_events (event_id TEXT PRIMARY KEY, at TIMESTAMP)",
    "CREATE TABLE watcher_subscription_eval_attempts "
    "(subscription_attempt_id TEXT PRIMARY KEY, created_at TIMESTAMP)",
    "CREATE TABLE watcher_source_fetch_attempts "
    "(fetch_attempt_id TEXT PRIMARY KEY, created_at TIMESTAMP)",
    "CREATE TABLE paper_evaluation_observations (observation_id TEXT PRIMARY KEY, "
    "occurred_at TIMESTAMP, journal_trade_id TEXT, filled BOOLEAN, plan_approved BOOLEAN)",
    "CREATE TABLE blofin_demo_sync_snapshots (id TEXT PRIMARY KEY, organization_id TEXT, "
    "synced_at TIMESTAMP)",
    # A table that must never be touched.
    "CREATE TABLE journal_trades (id TEXT PRIMARY KEY, created_at TIMESTAMP)",
)


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.begin() as conn:
        for ddl in _DDL:
            conn.execute(text(ddl))
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, retention_batch_size=100, **overrides)  # type: ignore[arg-type]


def _insert(factory, sql: str, rows: list[dict[str, object]]) -> None:
    with factory() as session:
        session.execute(text(sql), rows)
        session.commit()


def _count(factory, table: str) -> int:
    with factory() as session:
        return int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())


def _seed(factory) -> None:
    old = NOW - timedelta(days=40)
    fresh = NOW - timedelta(hours=1)
    _insert(
        factory,
        "INSERT INTO watcher_observability_events VALUES (:id, :at)",
        [{"id": str(uuid4()), "at": old} for _ in range(250)]
        + [{"id": str(uuid4()), "at": fresh} for _ in range(5)],
    )
    _insert(
        factory,
        "INSERT INTO paper_evaluation_observations VALUES (:id, :at, :trade, :filled, :plan)",
        [
            {"id": "scan-old", "at": old, "trade": None, "filled": False, "plan": False},
            {"id": "trade-old", "at": old, "trade": "t1", "filled": True, "plan": True},
            {"id": "plan-old", "at": old, "trade": None, "filled": False, "plan": True},
            {"id": "scan-new", "at": fresh, "trade": None, "filled": False, "plan": False},
        ],
    )
    org_a, org_b = str(uuid4()), str(uuid4())
    _insert(
        factory,
        "INSERT INTO blofin_demo_sync_snapshots VALUES (:id, :org, :at)",
        [
            {"id": "a-old-1", "org": org_a, "at": old - timedelta(days=2)},
            {"id": "a-old-2", "org": org_a, "at": old},
            {"id": "a-new", "org": org_a, "at": fresh},
            # org B's only snapshot is old: it is the newest, so it stays.
            {"id": "b-only", "org": org_b, "at": old},
        ],
    )
    _insert(
        factory,
        "INSERT INTO journal_trades VALUES (:id, :at)",
        [{"id": "j1", "at": old - timedelta(days=400)}],
    )


def test_dry_run_counts_and_deletes_nothing(factory) -> None:
    _seed(factory)
    results = run_retention(factory, _settings(), now=NOW)  # default RETENTION_DRY_RUN=true
    by_table = {r.table: r for r in results}
    assert by_table["watcher_observability_events"].matched == 250
    assert by_table["watcher_observability_events"].deleted == 0
    assert all(r.dry_run for r in results)
    assert _count(factory, "watcher_observability_events") == 255


def test_prune_keeps_trade_evidence_newest_snapshot_and_other_tables(factory) -> None:
    _seed(factory)
    results = run_retention(factory, _settings(retention_dry_run=False), now=NOW)
    by_table = {r.table: r for r in results}
    assert by_table["watcher_observability_events"].deleted == 250
    assert by_table["watcher_observability_events"].complete is True
    assert _count(factory, "watcher_observability_events") == 5
    with factory() as session:
        kept = set(
            session.execute(text("SELECT observation_id FROM paper_evaluation_observations"))
            .scalars()
            .all()
        )
        snapshots = set(
            session.execute(text("SELECT id FROM blofin_demo_sync_snapshots")).scalars().all()
        )
    assert kept == {"trade-old", "plan-old", "scan-new"}
    assert snapshots == {"a-new", "b-only"}
    assert _count(factory, "journal_trades") == 1


def test_batches_are_bounded_per_pass(factory) -> None:
    _seed(factory)
    settings = _settings(retention_dry_run=False, retention_max_batches_per_table=2)
    results = run_retention(factory, settings, now=NOW)
    events = next(r for r in results if r.table == "watcher_observability_events")
    assert events.deleted == 200
    assert events.complete is False
    assert _count(factory, "watcher_observability_events") == 55


def test_policies_never_name_trade_audit_or_strategy_tables() -> None:
    tables = {p.table for p in retention_policies(_settings())}
    assert tables == {
        "watcher_observability_events",
        "watcher_subscription_eval_attempts",
        "watcher_source_fetch_attempts",
        "paper_evaluation_observations",
        "blofin_demo_sync_snapshots",
    }


def test_retention_is_off_and_dry_run_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.retention_enabled is False
    assert settings.retention_dry_run is True


def test_windows_are_hours_with_a_24_hour_floor() -> None:
    settings = Settings(_env_file=None)
    assert settings.retention_watcher_events_hours == 24
    assert settings.retention_watcher_error_events_hours == 72
    assert settings.retention_watcher_attempts_hours == 24
    assert settings.retention_paper_evaluation_hours == 24
    assert settings.retention_telegram_notifications_hours == 24
    windows = {p.table: p.window for p in retention_policies(settings)}
    assert windows["paper_evaluation_observations"] == timedelta(hours=24)
    assert windows["blofin_demo_sync_snapshots"] == timedelta(days=30)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, retention_watcher_events_hours=23)  # type: ignore[call-arg]


def test_cycle_waits_one_interval_then_runs_once_per_interval(factory) -> None:
    _seed(factory)
    ticks = {"now": 0.0}
    cycle = DbRetentionCycle(
        _settings(retention_dry_run=False, retention_interval_seconds=600),
        session_factory=factory,
        monotonic=lambda: ticks["now"],
    )
    assert cycle().status == "waiting"
    ticks["now"] = 600.0
    outcome = cycle()
    assert outcome.status == "pruned"
    assert outcome.delivered >= 250
    assert cycle().status == "waiting"
    cycle.request_stop()
    assert cycle().status == "stopped"
