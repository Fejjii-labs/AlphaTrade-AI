"""Forward migration preserves existing immutable plans and refuses history loss."""

import importlib
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.models import TradePlanRevision
from tests.support.alembic_heads import single_alembic_head
from tests.support.phase7_postgres import postgres_plan_world
from tests.support.phase7_trade_plan import plan_command
from tests.support.postgres_persistence import (
    POSTGRES_URL,
    phase7_plan_session_factory,
    requires_postgres,
)

migration = importlib.import_module("app.db.migrations.versions.a6manualdemo001_manual_demo_origin")


def test_single_head_extends_verified_demo_lifecycle():
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/app/db/migrations")
    )
    scripts = ScriptDirectory.from_config(config)
    single_alembic_head(scripts)
    assert scripts.get_revision("a8agentcapture001").down_revision == "a7manualrecovery001"
    assert scripts.get_revision("a7manualrecovery001").down_revision == "a6manualdemo001"
    assert scripts.get_revision("a6manualdemo001").down_revision == "a5demolifecycle001"


@requires_postgres
def test_upgrade_preserves_canonical_payload_hash_and_lineage():
    factory = phase7_plan_session_factory()
    # Use an actual existing canonical plan through its authoritative fixture.
    world = postgres_plan_world(factory)
    plan = world.plans.create(plan_command(world)).plan
    with factory() as session:
        row = session.get(TradePlanRevision, plan.revision_id)
        payload, digest, candidate = row.semantic_payload.copy(), row.content_hash, row.candidate_id
        with Operations.context(
            MigrationContext.configure(
                session.connection(), opts={"target_metadata": Base.metadata}
            )
        ):
            migration.downgrade()  # Reconstruct prior constraints; no manual rows exist.
            migration.upgrade()
        session.commit()
        session.expire_all()
        row = session.get(TradePlanRevision, plan.revision_id)
        assert row.semantic_payload == payload
        assert row.content_hash == digest == plan.content_hash
        assert row.candidate_id == candidate == world.candidate.candidate_id
        assert row.canonical_candidate_id == candidate
        checks = {
            c["name"]
            for c in inspect(session.connection()).get_check_constraints("trade_plan_revisions")
        }
        assert "ck_trade_plan_revisions_ck_tpr_manual_origin" in checks


@requires_postgres
def test_recovery_migration_roundtrip_keeps_existing_command_plan_and_hash():
    recovery = importlib.import_module(
        "app.db.migrations.versions.a7manualrecovery001_manual_demo_recovery"
    )
    factory = phase7_plan_session_factory()
    world = postgres_plan_world(factory)
    plan = world.plans.create(plan_command(world)).plan
    with factory() as session:
        before = session.get(TradePlanRevision, plan.revision_id).semantic_payload.copy()
        with Operations.context(
            MigrationContext.configure(
                session.connection(), opts={"target_metadata": Base.metadata}
            )
        ):
            recovery.downgrade()
            recovery.upgrade()
        session.commit()
        session.expire_all()
        preserved = session.get(TradePlanRevision, plan.revision_id)
        assert preserved.semantic_payload == before and preserved.content_hash == plan.content_hash
        constraints = inspect(session.connection()).get_unique_constraints(
            "manual_demo_lifecycle_resolutions"
        )
        assert any(c["column_names"] == ["command_id"] for c in constraints)
        triggers = (
            session.execute(
                text(
                    "SELECT tgname FROM pg_trigger WHERE "
                    "tgrelid = 'manual_demo_lifecycle_resolutions'::regclass AND NOT tgisinternal"
                )
            )
            .scalars()
            .all()
        )
        assert "trg_manual_demo_lifecycle_resolutions_immutable" in triggers


@requires_postgres
def test_real_alembic_a6_to_head_preserves_plan_and_enables_recovery_reads(monkeypatch):
    """Run the release migration chain in an isolated real PostgreSQL schema."""
    admin = create_engine(POSTGRES_URL, poolclass=NullPool)
    schema = "pr231_migration_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    scoped_url = admin.url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    engine = create_engine(scoped_url, poolclass=NullPool)
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "src/app/db/migrations"))
    try:
        command.upgrade(config, "a6manualdemo001")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "a6manualdemo001"
            )
            assert not inspect(connection).has_table("manual_demo_lifecycle_resolutions")
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        world = postgres_plan_world(factory)
        plan = world.plans.create(plan_command(world)).plan
        with factory() as session:
            before = session.get(TradePlanRevision, plan.revision_id).semantic_payload.copy()
        command.upgrade(config, "head")
        command.upgrade(config, "head")  # API entrypoint repeats Render's pre-deploy upgrade.
        with factory() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == (
                single_alembic_head()
            )
            preserved = session.get(TradePlanRevision, plan.revision_id)
            assert preserved.semantic_payload == before
            assert preserved.content_hash == plan.content_hash
            assert inspect(session.connection()).has_table("manual_demo_lifecycle_resolutions")
            from app.services.demo_account_history import has_demo_entry_history

            assert not has_demo_entry_history(
                session, organization_id=preserved.organization_id, account_id=preserved.account_id
            )
            trigger = session.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE "
                    "tgrelid = 'manual_demo_lifecycle_resolutions'::regclass "
                    "AND tgname = 'trg_manual_demo_lifecycle_resolutions_immutable'"
                )
            )
            assert trigger == 1
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()
