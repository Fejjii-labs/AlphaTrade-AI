"""Template-clone fixtures hide committed rows and stay cheaper than DDL rebuilds."""

from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models import Organization
from tests.support.postgres_persistence import (
    ReusablePhase7Database,
    phase7_plan_session_factory,
    requires_postgres,
)
from tests.support.postgres_template import (
    FULL_SCHEMA_MARKER,
    worker_database_name,
)

# Fixed identity so the following test can prove it does not leak across tests.
LEAKED_ORG = UUID("00000000-0000-0000-0000-00000000c10e")
# Local clones are ~0.10s. A full create_all rebuild is ~0.65s. Stay under the rebuild.
SETUP_BUDGET_SECONDS = float(os.environ.get("ALPHATRADE_PG_FIXTURE_SETUP_BUDGET_SECONDS", "0.75"))
# Modules that are not on main were omitted: test_pr237_migration_aware_rollback.py,
# test_experiment_migration.py, and test_blofin_activity_migration.py.
MIGRATION_MODULES = (
    "test_release_wave002_migrations.py",
    "test_phase2_4_alembic_postgres.py",
    "test_phase8_learning_persistence.py",
    "test_journal_trades_alembic_empty_tenant.py",
)


def test_worker_database_name_is_per_xdist_worker() -> None:
    assert worker_database_name("alphatrade_test", "master") == "alphatrade_test"
    assert worker_database_name("alphatrade_test", "gw0") == "alphatrade_test_gw0"
    assert worker_database_name("alphatrade_test_gw0", "gw0") == "alphatrade_test_gw0"
    with pytest.raises(ValueError, match="worker id"):
        worker_database_name("alphatrade_test", "worker 1")


def test_migration_modules_do_not_use_the_template_clone() -> None:
    root = Path(__file__).resolve().parent
    for name in MIGRATION_MODULES:
        source = (root / name).read_text(encoding="utf-8")
        assert "refresh_database" not in source
        assert "clone_database" not in source


def _org_count(session: Session) -> int:
    counted = session.scalar(select(func.count()).select_from(Organization))
    return int(counted or 0)


@requires_postgres
def test_next_factory_call_hides_committed_rows() -> None:
    first = phase7_plan_session_factory()
    with first() as session:
        session.add(Organization(id=LEAKED_ORG, name="must not leak"))
        session.commit()
        assert session.get(Organization, LEAKED_ORG) is not None
    second = phase7_plan_session_factory()
    with second() as session:
        assert session.get(Organization, LEAKED_ORG) is None
        assert _org_count(session) == 0
        marker = session.scalar(
            text(
                "SELECT obj_description(oid, 'pg_namespace') "
                "FROM pg_namespace WHERE nspname = 'public'"
            )
        )
        assert marker == FULL_SCHEMA_MARKER
    assert first.kw["bind"].url.database == second.kw["bind"].url.database
    first.kw["bind"].dispose()
    second.kw["bind"].dispose()


@requires_postgres
def test_writer_commits_a_row_that_must_not_leak() -> None:
    factory = phase7_plan_session_factory()
    with factory() as session:
        session.add(Organization(id=LEAKED_ORG, name="previous test"))
        session.commit()
        assert _org_count(session) == 1
    factory.kw["bind"].dispose()


@requires_postgres
def test_reader_does_not_see_the_previous_test_row() -> None:
    factory = phase7_plan_session_factory()
    with factory() as session:
        assert session.get(Organization, LEAKED_ORG) is None
        assert _org_count(session) == 0
    factory.kw["bind"].dispose()


@requires_postgres
def test_reusable_database_hides_rows_without_replacing_the_database() -> None:
    database = ReusablePhase7Database()
    try:
        first = database.session_factory()
        with first() as session:
            session.add(Organization(id=LEAKED_ORG, name="reusable"))
            session.commit()
        second = database.session_factory()
        with second() as session:
            assert session.get(Organization, LEAKED_ORG) is None
            assert _org_count(session) == 0
        assert first.kw["bind"] is second.kw["bind"]
    finally:
        database.dispose()


@requires_postgres
def test_phase7_refresh_stays_within_setup_budget() -> None:
    warmup = phase7_plan_session_factory()
    warmup.kw["bind"].dispose()
    samples: list[float] = []
    for _ in range(3):
        started = perf_counter()
        factory = phase7_plan_session_factory()
        samples.append(perf_counter() - started)
        factory.kw["bind"].dispose()
    worst = max(samples)
    assert worst < SETUP_BUDGET_SECONDS, f"samples={samples} budget={SETUP_BUDGET_SECONDS}"
