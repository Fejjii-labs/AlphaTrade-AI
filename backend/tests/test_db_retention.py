"""H5: retention prunes only old operational rows, in batches, and dry-run deletes nothing."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.services.db_retention import (
    WATCHER_ERROR_EVENT_MARKERS,
    retention_policies,
    run_retention,
)
from app.workers.db_retention import DbRetentionCycle

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)

# SQLite stand-in only; Python 3.12 deprecates its default datetime adapter.
pytestmark = pytest.mark.filterwarnings("ignore:The default datetime adapter:DeprecationWarning")

_DDL = (
    "CREATE TABLE watcher_observability_events "
    "(event_id TEXT PRIMARY KEY, name TEXT, at TIMESTAMP, fields JSON)",
    "CREATE TABLE watcher_subscription_eval_attempts "
    "(subscription_attempt_id TEXT PRIMARY KEY, scan_attempt_id TEXT, created_at TIMESTAMP)",
    "CREATE TABLE watcher_source_fetch_attempts "
    "(fetch_attempt_id TEXT PRIMARY KEY, scan_attempt_id TEXT, created_at TIMESTAMP)",
    "CREATE TABLE watcher_scan_lineages (lineage_id TEXT PRIMARY KEY, "
    "terminal_attempt_id TEXT, terminal_status TEXT, created_at TIMESTAMP)",
    "CREATE TABLE watcher_scan_attempts (attempt_id TEXT PRIMARY KEY, lineage_id TEXT, "
    "started_at TIMESTAMP)",
    "CREATE TABLE paper_evaluation_observations (observation_id TEXT PRIMARY KEY, "
    "occurred_at TIMESTAMP, journal_trade_id TEXT, filled BOOLEAN, plan_approved BOOLEAN, "
    "stage TEXT, candidate_id TEXT)",
    "CREATE TABLE blofin_demo_sync_snapshots (id TEXT PRIMARY KEY, organization_id TEXT, "
    "synced_at TIMESTAMP, health_status TEXT DEFAULT 'ok', provenance JSON DEFAULT '{}', "
    "user_id TEXT)",
    "CREATE TABLE telegram_paper_notifications (intent_id TEXT PRIMARY KEY, created_at TIMESTAMP, "
    "candidate_id TEXT)",
    "CREATE TABLE telegram_security_outbox (id TEXT PRIMARY KEY, created_at TIMESTAMP)",
    # A table that must never be touched.
    "CREATE TABLE conversations (id TEXT PRIMARY KEY, created_at TIMESTAMP)",
)


_ORM_TABLES = (
    "controlled_runtime_status",
    "canonical_candidates",
    "canonical_candidate_creation_keys",
    "canonical_candidate_transitions",
    "canonical_candidate_transition_keys",
    "action_eligibility_evaluations",
    "canonical_trade_plan_roots",
    "canonical_trade_plan_lineage",
    "trade_plan_revisions",
    "journal_trades",
    "trade_candidate_summaries",
    "strategy_brain_setups",
    "strategy_brain_setup_events",
    "historical_candles",
    "public_market_observations",
    "user_strategy_versions",
    "audit_logs",
    "approvals",
    "governed_demo_lifecycle_resolutions",
    "manual_demo_lifecycle_resolutions",
    "watcher_scheduled_scans",
)


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.begin() as conn:
        for ddl in _DDL:
            conn.execute(text(ddl))
    # Real ORM tables for the policies the narrow stand-ins above do not model.
    from app.db.base import Base

    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in _ORM_TABLES])
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
        "INSERT INTO watcher_observability_events "
        "VALUES (:id, 'watcher_scan_scheduled', :at, '{}')",
        [{"id": str(uuid4()), "at": old} for _ in range(250)]
        + [{"id": str(uuid4()), "at": fresh} for _ in range(5)],
    )
    _insert(
        factory,
        "INSERT INTO paper_evaluation_observations "
        "VALUES (:id, :at, :trade, :filled, :plan, :stage, NULL)",
        [
            {
                "id": "scan-old",
                "at": old,
                "trade": None,
                "filled": False,
                "plan": False,
                "stage": "watcher_scan",
            },
            {
                "id": "trade-old",
                "at": old,
                "trade": "t1",
                "filled": True,
                "plan": True,
                "stage": "paper_trade",
            },
            {
                "id": "plan-old",
                "at": old,
                "trade": None,
                "filled": False,
                "plan": True,
                "stage": "paper_decision",
            },
            {
                "id": "scan-new",
                "at": fresh,
                "trade": None,
                "filled": False,
                "plan": False,
                "stage": "watcher_scan",
            },
        ],
    )
    org_a, org_b = str(uuid4()), str(uuid4())
    _insert(
        factory,
        "INSERT INTO blofin_demo_sync_snapshots (id, organization_id, synced_at) "
        "VALUES (:id, :org, :at)",
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
        "INSERT INTO conversations VALUES (:id, :at)",
        [{"id": "j1", "at": old - timedelta(days=400)}],
    )


def test_dry_run_counts_and_deletes_nothing(factory) -> None:
    _seed(factory)
    results = run_retention(factory, _settings(), now=NOW)  # default RETENTION_DRY_RUN=true
    by_table = {r.policy: r for r in results}
    assert by_table["watcher_observability_events"].matched == 250
    assert by_table["watcher_observability_events"].deleted == 0
    assert all(r.dry_run for r in results)
    assert _count(factory, "watcher_observability_events") == 255


def test_prune_keeps_trade_evidence_newest_snapshot_and_other_tables(factory) -> None:
    _seed(factory)
    results = run_retention(factory, _settings(retention_dry_run=False), now=NOW)
    by_table = {r.policy: r for r in results}
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
    assert _count(factory, "conversations") == 1


def test_batches_are_bounded_per_pass(factory) -> None:
    _seed(factory)
    settings = _settings(retention_dry_run=False, retention_max_batches_per_table=2)
    results = run_retention(factory, settings, now=NOW)
    events = next(r for r in results if r.policy == "watcher_observability_events")
    assert events.deleted == 200
    assert events.complete is False
    assert _count(factory, "watcher_observability_events") == 55


def test_policies_name_only_the_approved_tables() -> None:
    tables = {p.table for p in retention_policies(_settings())}
    assert tables == {
        "watcher_observability_events",
        "watcher_subscription_eval_attempts",
        "watcher_source_fetch_attempts",
        "watcher_scan_attempts",
        "watcher_scheduled_scans",
        "watcher_scan_lineages",
        "paper_evaluation_observations",
        "telegram_paper_notifications",
        "blofin_demo_sync_snapshots",
        "strategy_brain_setup_events",
        "public_market_observations",
        "audit_logs",
    }


def test_a_longer_error_window_keeps_error_events_and_drops_the_rest(factory) -> None:
    rows = [
        # 48 h old: past the 24 h window, inside the 72 h error window.
        ("routine-2d", "watcher_scan_scheduled", 48, {}),
        ("ok-finish-2d", "watcher_scan_finished", 48, {"evaluation_status": "succeeded"}),
        ("failed-finish-2d", "watcher_scan_finished", 48, {"evaluation_status": "failed"}),
        ("blocked-2d", "watcher_schedule_blocked", 48, {}),
        ("stale-fence-2d", "watcher_stale_fence_rejected", 48, {}),
        ("routine-1h", "watcher_lease_claimed", 1, {}),
        ("error-1h", "watcher_worker_blocked", 1, {}),
        # 80 h old: past both windows.
        ("blocked-80h", "watcher_notify_blocked", 80, {}),
        ("degraded-80h", "watcher_scan_finished", 80, {"evaluation_status": "degraded"}),
    ]
    _insert(
        factory,
        "INSERT INTO watcher_observability_events VALUES (:id, :name, :at, :fields)",
        [
            {"id": i, "name": n, "at": NOW - timedelta(hours=h), "fields": json.dumps(f)}
            for i, n, h, f in rows
        ],
    )
    settings = _settings(retention_dry_run=False, retention_watcher_error_events_hours=72)
    results = run_retention(factory, settings, now=NOW)
    by_policy = {r.policy: r for r in results}
    assert by_policy["watcher_observability_events"].deleted == 2
    assert by_policy["watcher_observability_events:error"].deleted == 2
    with factory() as session:
        kept = set(
            session.execute(text("SELECT event_id FROM watcher_observability_events"))
            .scalars()
            .all()
        )
    assert kept == {"failed-finish-2d", "blocked-2d", "stale-fence-2d", "routine-1h", "error-1h"}


def test_scan_attempts_keep_terminal_unfinished_and_recent_lineages(factory) -> None:
    old, fresh = NOW - timedelta(hours=30), NOW - timedelta(hours=1)
    _insert(
        factory,
        "INSERT INTO watcher_scan_lineages VALUES (:id, :terminal, :status, :at)",
        [
            {"id": "done", "terminal": "done-2", "status": "succeeded", "at": old},
            {"id": "failed", "terminal": "failed-1", "status": "failed", "at": old},
            {"id": "open", "terminal": None, "status": None, "at": old},
            {"id": "recent", "terminal": "recent-2", "status": "succeeded", "at": old},
            {"id": "child", "terminal": "child-2", "status": "succeeded", "at": old},
        ],
    )
    _insert(
        factory,
        "INSERT INTO watcher_scan_attempts VALUES (:id, :lineage, :at)",
        [
            {"id": "done-1", "lineage": "done", "at": old},
            {"id": "done-2", "lineage": "done", "at": old},
            {"id": "failed-1", "lineage": "failed", "at": old},
            {"id": "open-1", "lineage": "open", "at": old},
            {"id": "recent-1", "lineage": "recent", "at": old},
            {"id": "recent-2", "lineage": "recent", "at": fresh},
            {"id": "child-1", "lineage": "child", "at": old},
            {"id": "child-2", "lineage": "child", "at": old},
        ],
    )
    # A child row the 24 h child policy keeps (fresh) still pins its parent.
    _insert(
        factory,
        "INSERT INTO watcher_source_fetch_attempts VALUES (:id, :parent, :at)",
        [
            {"id": "f-old", "parent": "done-1", "at": old},
            {"id": "f-fresh", "parent": "child-1", "at": fresh},
        ],
    )
    run_retention(factory, _settings(retention_dry_run=False), now=NOW)
    with factory() as session:
        kept = set(
            session.execute(text("SELECT attempt_id FROM watcher_scan_attempts")).scalars().all()
        )
        lineages = _count(factory, "watcher_scan_lineages")
    # done-1 goes (its old child went first); terminal, failed, open, recent
    # and child-pinned attempts stay.
    assert kept == {"done-2", "failed-1", "open-1", "recent-1", "recent-2", "child-1", "child-2"}
    assert lineages == 5


def test_telegram_notifications_go_after_7_days_and_the_outbox_stays(factory) -> None:
    old, fresh = NOW - timedelta(days=7, hours=1), NOW - timedelta(days=6, hours=23)
    _insert(
        factory,
        "INSERT INTO telegram_paper_notifications VALUES (:id, :at, :cand)",
        [
            {"id": "old", "at": old, "cand": None},
            {"id": "fresh", "at": fresh, "cand": None},
            # A candidate alert waits for its kept-forever summary row.
            {"id": "unsummarized", "at": old, "cand": "c-1"},
            {"id": "summarized", "at": old, "cand": "c-2"},
        ],
    )
    _insert(
        factory,
        "INSERT INTO canonical_candidates VALUES (:id, 'o', :h, 'v1', 's', 'd', 'f', 'long', "
        "'binance', 'perp', 'BTCUSDT', '15m', :h, 'a', '{}', '{}', 'published', :at, :at, 1, "
        "'k', 'c', :h)",
        [{"id": "c-1", "at": NOW, "h": "a" * 64}],
    )
    _insert(
        factory,
        "INSERT INTO trade_candidate_summaries (candidate_id, organization_id, symbol, "
        "detected_at, direction, notified, taken, outcome_source, summarized_at) "
        "VALUES (:id, 'o', 'BTCUSDT', :at, 'long', 1, 0, 'unavailable', :at)",
        [{"id": "c-2", "at": NOW}],
    )
    _insert(
        factory,
        "INSERT INTO telegram_security_outbox VALUES (:id, :at)",
        [{"id": "sent", "at": NOW - timedelta(days=30)}],
    )
    run_retention(factory, _settings(retention_dry_run=False), now=NOW)
    with factory() as session:
        kept = set(
            session.execute(text("SELECT intent_id FROM telegram_paper_notifications"))
            .scalars()
            .all()
        )
    assert kept == {"fresh", "unsummarized"}
    assert _count(factory, "telegram_security_outbox") == 1


def test_error_markers_match_the_monitoring_panel() -> None:
    from app.services.watcher_monitoring_service import _ERROR_EVENT_MARKERS

    assert WATCHER_ERROR_EVENT_MARKERS == _ERROR_EVENT_MARKERS


def test_retention_is_off_and_dry_run_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.retention_enabled is False
    assert settings.retention_dry_run is True


def test_windows_are_hours_with_a_24_hour_floor() -> None:
    settings = Settings(_env_file=None)
    assert settings.retention_watcher_events_hours == 24
    assert settings.retention_watcher_error_events_hours == 24
    assert settings.retention_watcher_attempts_hours == 24
    assert settings.retention_paper_evaluation_hours == 24
    assert settings.retention_telegram_notifications_hours == 168
    assert settings.retention_candidate_days == 7
    assert settings.retention_audit_log_days == 90
    assert settings.retention_brain_observation_hours == 24
    assert settings.retention_public_market_hours == 24
    assert settings.retention_vacuum_analyze is False
    windows = {p.label: p.window for p in retention_policies(settings)}
    assert windows["paper_evaluation_observations"] == timedelta(hours=24)
    assert windows["paper_evaluation_observations:candidate"] == timedelta(days=7)
    assert windows["telegram_paper_notifications"] == timedelta(days=7)
    assert windows["audit_logs"] == timedelta(days=90)
    assert windows["strategy_brain_setup_events:system_observation"] == timedelta(hours=24)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, retention_watcher_events_hours=23)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "field",
    [
        "retention_watcher_events_hours",
        "retention_watcher_error_events_hours",
        "retention_watcher_attempts_hours",
        "retention_paper_evaluation_hours",
        "retention_brain_observation_hours",
        "retention_public_market_hours",
    ],
)
def test_every_hours_window_has_a_24_hour_floor(field: str) -> None:
    for too_short in (0, 23):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **{field: too_short})  # type: ignore[arg-type]
    assert getattr(Settings(_env_file=None, **{field: 24}), field) == 24  # type: ignore[arg-type]


def test_demo_snapshot_daily_rows_are_kept_forever_by_default() -> None:
    assert Settings(_env_file=None).retention_demo_snapshot_days == 0
    assert (
        Settings(_env_file=None, retention_demo_snapshot_days="").retention_demo_snapshot_days == 0
    )  # type: ignore[call-arg]
    assert (
        Settings(_env_file=None, retention_demo_snapshot_days=30).retention_demo_snapshot_days == 30
    )  # type: ignore[call-arg]
    for invalid in (-1, 6):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, retention_demo_snapshot_days=invalid)  # type: ignore[call-arg]
    policy = next(
        p
        for p in retention_policies(Settings(_env_file=None))
        if p.table == "blofin_demo_sync_snapshots"
    )
    # Intra-day rows are thinned only once they are older than 24h.
    assert policy.window == timedelta(hours=24)
    assert policy.extra_cutoffs == ()


def test_notifications_cannot_go_before_their_candidate_summary() -> None:
    with pytest.raises(ValidationError, match="RETENTION_TELEGRAM_NOTIFICATIONS_HOURS"):
        Settings(_env_file=None, retention_telegram_notifications_hours=24)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, retention_candidate_days=6)  # type: ignore[call-arg]


def test_one_failing_policy_does_not_stop_the_others(factory, monkeypatch) -> None:
    import app.services.db_retention as service

    _seed(factory)
    real = service.prune_table

    def flaky(session_factory, policy, **kwargs):
        if policy.table == "paper_evaluation_observations":
            raise RuntimeError("boom")
        return real(session_factory, policy, **kwargs)

    monkeypatch.setattr(service, "prune_table", flaky)
    results = run_retention(factory, _settings(retention_dry_run=False), now=NOW)
    assert "paper_evaluation_observations" not in {r.table for r in results}
    assert _count(factory, "watcher_observability_events") == 5
    assert _count(factory, "paper_evaluation_observations") == 4


def test_error_event_window_cannot_be_shorter_than_normal_window() -> None:
    with pytest.raises(ValidationError, match="RETENTION_WATCHER_ERROR_EVENTS_HOURS"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            retention_watcher_events_hours=96,
            retention_watcher_error_events_hours=72,
        )
    equal = Settings(
        _env_file=None,  # type: ignore[call-arg]
        retention_watcher_events_hours=72,
        retention_watcher_error_events_hours=72,
    )
    assert equal.retention_watcher_error_events_hours == equal.retention_watcher_events_hours


def test_retention_schedule_defaults_and_bounds() -> None:
    assert _settings().retention_schedule_hour_utc == 2
    for hour in (-1, 24):
        with pytest.raises(ValidationError):
            _settings(retention_schedule_hour_utc=hour)


def test_retention_cycle_disabled_does_not_open_database() -> None:
    def forbidden():
        pytest.fail("disabled retention opened the database")

    cycle = DbRetentionCycle(_settings(), session_factory=forbidden, now=lambda: NOW)
    assert cycle().status == "disabled"


@pytest.mark.parametrize("dry_run", [True, False])
def test_retention_daily_cycle_windows_and_restart(factory, dry_run) -> None:
    _seed(factory)
    settings = _settings(retention_enabled=True, retention_dry_run=dry_run)
    clock = {"now": NOW.replace(hour=1)}
    mutations = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        # A durable scheduling claim is required even for a dry run. Operational
        # retention tables must never receive DELETE/UPDATE in dry mode.
        if statement.lstrip().upper().startswith(("DELETE", "UPDATE")):
            mutations.append(statement)

    event.listen(factory.kw["bind"], "before_cursor_execute", capture)
    cycle = DbRetentionCycle(settings, session_factory=factory, now=lambda: clock["now"])
    assert cycle().status == "waiting"
    clock["now"] = NOW
    result = cycle()
    assert result.status == ("dry_run" if dry_run else "pruned")
    assert _count(factory, "watcher_observability_events") == (255 if dry_run else 5)
    assert _count(factory, "paper_evaluation_observations") == (4 if dry_run else 3)
    assert _count(factory, "blofin_demo_sync_snapshots") == (4 if dry_run else 2)
    assert _count(factory, "conversations") == 1
    if dry_run:
        assert not [s for s in mutations if "controlled_runtime_status" not in s]
    restarted = DbRetentionCycle(settings, session_factory=factory, now=lambda: clock["now"])
    assert restarted().status == "already_ran"
    clock["now"] += timedelta(days=1)
    assert restarted().status == ("dry_run" if dry_run else "pruned")
    restarted.request_stop()
    assert restarted().status == "stopped"


def test_retention_cycle_failure_isolated_and_day_consumed(factory, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("must not escape or be logged verbatim")

    monkeypatch.setattr("app.services.db_retention.run_retention", fail)
    settings = _settings(retention_enabled=True)
    cycle = DbRetentionCycle(settings, session_factory=factory, now=lambda: NOW)
    assert cycle().status == "failed"
    assert DbRetentionCycle(settings, session_factory=factory, now=lambda: NOW)().status == (
        "already_ran"
    )


def test_retention_cycle_database_failure_isolated() -> None:
    def fail():
        raise RuntimeError("connection details must not be logged")

    cycle = DbRetentionCycle(
        _settings(retention_enabled=True), session_factory=fail, now=lambda: NOW
    )
    assert cycle().status == "failed"
