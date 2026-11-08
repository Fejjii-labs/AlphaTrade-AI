"""H5 retention on PostgreSQL: the real SQL (JSON ->>, EXISTS) and the real FKs."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.services.db_retention import run_retention
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres

pytestmark = requires_postgres

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
ORG = uuid4()


@pytest.fixture
def pg_factory() -> Iterator[sessionmaker[Session]]:
    from tests.support.postgres_template import clone_database, drop_clone

    engine = clone_database(POSTGRES_URL)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        drop_clone(engine)


def _exec(factory: sessionmaker[Session], sql: str, rows: list[dict[str, object]]) -> None:
    with factory() as session:
        session.execute(text(sql), rows)
        session.commit()


def _ids(factory: sessionmaker[Session], sql: str) -> set[str]:
    with factory() as session:
        return {str(value) for value in session.execute(text(sql)).scalars().all()}


def _event(name: str, hours: int, **fields: str) -> dict[str, object]:
    return {
        "id": uuid4(),
        "name": name,
        "at": NOW - timedelta(hours=hours),
        "org": ORG,
        "fields": json.dumps(fields),
    }


def _lineage(lineage_id: UUID, terminal: UUID | None, status: str | None) -> dict[str, object]:
    return {
        "id": lineage_id,
        "org": ORG,
        "hash": "a" * 64,
        "policy": uuid4(),
        "at": NOW - timedelta(hours=40),
        "terminal": terminal,
        "status": status,
    }


def _attempt(attempt_id: UUID, lineage_id: UUID, number: int, hours: int) -> dict[str, object]:
    at = NOW - timedelta(hours=hours)
    return {"id": attempt_id, "lineage": lineage_id, "org": ORG, "n": number, "at": at}


def test_postgres_pass_applies_every_rule_and_leaves_guarded_tables(pg_factory) -> None:
    events = [
        _event("watcher_scan_scheduled", 30),
        _event("watcher_scan_finished", 30, evaluation_status="succeeded"),
        _event("watcher_scan_finished", 30, evaluation_status="failed"),
        _event("watcher_schedule_blocked", 30),
        _event("watcher_notify_blocked", 80),
        _event("watcher_lease_claimed", 1),
    ]
    _exec(
        pg_factory,
        "INSERT INTO watcher_observability_events "
        "(event_id, name, at, organization_id, fields) "
        "VALUES (:id, :name, :at, :org, CAST(:fields AS json))",
        events,
    )

    done, failed = uuid4(), uuid4()
    done_1, done_2, failed_1 = uuid4(), uuid4(), uuid4()
    _exec(
        pg_factory,
        "INSERT INTO watcher_scan_lineages (lineage_id, organization_id, scan_scope, "
        "request_hash, policy_id, policy_version, policy_content_hash, trigger_created_by, "
        "created_at) VALUES (:id, :org, 'scope', :hash, :policy, 1, :hash, 'worker', :at)",
        [_lineage(done, None, None), _lineage(failed, None, None)],
    )
    _exec(
        pg_factory,
        "INSERT INTO watcher_scan_attempts (attempt_id, lineage_id, organization_id, "
        "scan_scope, attempt_number, trigger, mode, status, started_at, heartbeat_at, "
        "recovery_disposition) VALUES (:id, :lineage, :org, 'scope', :n, 'schedule', "
        "'worker', 'failed', :at, :at, 'none')",
        [
            _attempt(done_1, done, 1, 30),
            _attempt(done_2, done, 2, 29),
            _attempt(failed_1, failed, 1, 30),
        ],
    )
    _exec(
        pg_factory,
        "UPDATE watcher_scan_lineages SET terminal_attempt_id = :terminal, "
        "terminal_status = :status WHERE lineage_id = :id",
        [
            {"id": done, "terminal": done_2, "status": "succeeded"},
            {"id": failed, "terminal": failed_1, "status": "failed"},
        ],
    )
    # Old child rows reference both attempts of the succeeded lineage (FK).
    _exec(
        pg_factory,
        "INSERT INTO watcher_source_fetch_attempts (fetch_attempt_id, scan_attempt_id, "
        "lineage_id, organization_id, scan_scope, subject_id, status, created_at) "
        "VALUES (:id, :parent, :lineage, :org, 'scope', :subject, 'succeeded', :at)",
        [
            {
                "id": uuid4(),
                "parent": parent,
                "lineage": done,
                "org": ORG,
                "subject": uuid4(),
                "at": NOW - timedelta(hours=30),
            }
            for parent in (done_1, done_2)
        ],
    )

    old_note, fresh_note = uuid4(), uuid4()
    _exec(
        pg_factory,
        "INSERT INTO telegram_paper_notifications (intent_id, identity_hash, "
        "organization_id, user_id, account_id, kind, resource_type, resource_id, "
        "content_hash, telegram_revision_id, text, created_at) VALUES (:id, :hash, :org, "
        ":org, :org, 'candidate_active', 'candidate', :org, :hash, :org, 'x', :at)",
        [
            {"id": old_note, "hash": "b" * 64, "org": ORG, "at": NOW - timedelta(hours=25)},
            {"id": fresh_note, "hash": "c" * 64, "org": ORG, "at": NOW - timedelta(hours=2)},
        ],
    )

    settings = Settings(_env_file=None, retention_dry_run=False, retention_batch_size=100)  # type: ignore[call-arg]
    results = run_retention(pg_factory, settings, now=NOW)
    by_policy = {r.policy: r for r in results}

    # Every policy ran on Postgres; none failed and was skipped.
    assert set(by_policy) == {
        "watcher_observability_events",
        "watcher_observability_events:error",
        "watcher_subscription_eval_attempts",
        "watcher_source_fetch_attempts",
        "watcher_scan_attempts",
        "paper_evaluation_observations",
        "telegram_paper_notifications",
        "blofin_demo_sync_snapshots",
    }
    kept_events = _ids(
        pg_factory,
        "SELECT name || ':' || COALESCE(fields ->> 'evaluation_status', '') "
        "FROM watcher_observability_events",
    )
    assert kept_events == {
        "watcher_scan_finished:failed",
        "watcher_schedule_blocked:",
        "watcher_lease_claimed:",
    }
    assert _ids(pg_factory, "SELECT attempt_id FROM watcher_scan_attempts") == {
        str(done_2),
        str(failed_1),
    }
    assert _ids(pg_factory, "SELECT lineage_id FROM watcher_scan_lineages") == {
        str(done),
        str(failed),
    }
    assert _ids(pg_factory, "SELECT intent_id FROM telegram_paper_notifications") == {
        str(fresh_note)
    }
