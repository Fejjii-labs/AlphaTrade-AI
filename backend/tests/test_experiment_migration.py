"""The additive branch migrates after the integrated a10 milestone, in one chain."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

ADDED = {"experiments", "experiment_versions", "experiment_events", "experiment_samples"}


def test_single_additive_migration_chain():
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["a13voicemonitor001"]
    assert script.get_revision("a13voicemonitor001").down_revision == "a12trendpulsescreen001"
    assert script.get_revision("a12trendpulsescreen001").down_revision == "a11experiments001"
    assert script.get_revision("a11experiments001").down_revision == "a10blofinactivity001"
    assert len(script.get_bases()) == 1
    assert [r.revision for r in script.iterate_revisions("heads", "a10blofinactivity001")] == [
        "a13voicemonitor001",
        "a12trendpulsescreen001",
        "a11experiments001",
    ]


def test_upgrade_downgrade_reupgrade_preserves_native_history(monkeypatch):
    url = os.getenv("EXPERIMENT_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires explicit disposable PostgreSQL")
    schema = "experiment_migration_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = make_url(url).update_query_dict({"options": "-csearch_path=" + schema})
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", isolated.render_as_string(hide_password=False))
    cfg = Config("alembic.ini")
    engine = create_engine(isolated)
    try:
        command.upgrade(cfg, "a10blofinactivity001")
        organization = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO organizations (id,name) VALUES (:id,'historical-fixture')"),
                {"id": organization},
            )
            connection.execute(
                text(
                    "INSERT INTO blofin_activity_accounts "
                    "(organization_id,environment,account_uid,credential_binding,"
                    "identity_verified_at) "
                    "VALUES (:id,'demo','historical-uid',:binding,now())"
                ),
                {"id": organization, "binding": "a" * 64},
            )
        for destination in ("head", "a10blofinactivity001", "head"):
            if destination == "head":
                command.upgrade(cfg, destination)
            else:
                command.downgrade(cfg, destination)
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT account_uid FROM blofin_activity_accounts"))
                    == "historical-uid"
                )
                tables = set(inspect(connection).get_table_names())
                if destination == "head":
                    assert tables >= ADDED
                    guards = (
                        connection.execute(
                            text(
                                "SELECT tgname FROM pg_trigger WHERE tgrelid IN "
                                "('experiments'::regclass,'experiment_versions'::regclass,"
                                "'experiment_events'::regclass,'experiment_samples'::regclass) "
                                "AND NOT tgisinternal"
                            )
                        )
                        .scalars()
                        .all()
                    )
                    assert set(guards) == {t + "_guard" for t in ADDED}
                else:
                    assert ADDED.isdisjoint(tables)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
