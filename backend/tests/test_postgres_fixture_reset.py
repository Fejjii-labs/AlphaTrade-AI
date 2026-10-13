"""Guarded data reset keeps committed sessions and real PostgreSQL DDL isolation."""

import pytest
from sqlalchemy import func, inspect, select, text, union_all
from sqlalchemy.exc import DBAPIError

from app.db.base import Base
from app.db.models import Organization
from tests.support.phase7_postgres import postgres_plan_world
from tests.support.postgres_persistence import (
    ReusablePhase7Database,
    persistence_session_factory,
    requires_postgres,
)

pytestmark = requires_postgres


def schema_objects(engine):
    with engine.connect() as conn:
        return conn.execute(
            text("""
                SELECT 'schema', oid, nspname FROM pg_namespace WHERE nspname='public'
                UNION ALL
                SELECT 'function', p.oid, p.proname FROM pg_proc p
                JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
                UNION ALL
                SELECT 'trigger', t.oid, t.tgname FROM pg_trigger t
                JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
                ORDER BY 1,2
            """)
        ).all()


def assert_empty(engine):
    with engine.connect() as conn:
        counts = conn.scalars(
            union_all(*(select(func.count()).select_from(t) for t in Base.metadata.sorted_tables))
        ).all()
        assert sum(counts) == 0


def assert_history_protected(engine):
    with (
        pytest.raises(DBAPIError, match="alphatrade_immutable_history"),
        engine.begin() as conn,
    ):
        conn.execute(text("UPDATE compiled_setup_definitions SET compiler_version='tampered'"))


def test_reuse_clears_committed_data_and_preserves_history_guards():
    database = ReusablePhase7Database()
    try:
        first = database.session_factory()
        postgres_plan_world(first)  # Seed commits through independent sessions.
        with first() as reader:
            assert reader.scalar(select(func.count()).select_from(Organization)) == 1
        assert_history_protected(database.engine)
        objects = schema_objects(database.engine)
        assert any(row[0] == "function" for row in objects)
        assert any(row[0] == "trigger" for row in objects)
        second = database.session_factory()
        assert schema_objects(database.engine) == objects  # Reuse, not DROP/CREATE.
        assert_empty(database.engine)
        postgres_plan_world(second)  # Fixed seed identities can be committed again.
        with first() as restarted_reader:
            assert restarted_reader.scalar(select(func.count()).select_from(Organization)) == 1
        assert_history_protected(database.engine)
        third = database.session_factory()
        assert schema_objects(database.engine) == objects
        assert_empty(database.engine)
        postgres_plan_world(third)
        assert_history_protected(database.engine)
    finally:
        database.dispose()


@pytest.mark.parametrize(
    "change", ["schema-reset", "alter-column", "drop-trigger", "alter-function"]
)
def test_schema_reset_and_changed_table_force_rebuild(change):
    database = ReusablePhase7Database()
    try:
        factory = database.session_factory()
        postgres_plan_world(factory)
        objects = schema_objects(database.engine)
        if change == "schema-reset":
            other = persistence_session_factory()  # Drops public, installs a narrower schema.
            other.kw["bind"].dispose()
        else:
            commands = {
                "alter-column": "ALTER TABLE users ADD COLUMN fixture_extra integer",
                "drop-trigger": (
                    "DROP TRIGGER trg_compiled_setup_definitions_immutable "
                    "ON compiled_setup_definitions"
                ),
                "alter-function": """
                    CREATE OR REPLACE FUNCTION alphatrade_forbid_historical_mutation()
                    RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN OLD; END; $$
                """,
            }
            with database.engine.begin() as conn:
                conn.execute(text(commands[change]))
        fresh = database.session_factory()
        assert schema_objects(database.engine) != objects
        assert set(inspect(database.engine).get_table_names()) == set(Base.metadata.tables)
        assert "fixture_extra" not in {
            c["name"] for c in inspect(database.engine).get_columns("users")
        }
        assert_empty(database.engine)
        postgres_plan_world(fresh)
        assert_history_protected(database.engine)
    finally:
        database.dispose()
