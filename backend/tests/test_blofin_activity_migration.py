"""Upgrade/downgrade the generated addition in a disposable migrated database."""

import logging
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


def test_additive_migration_and_rollback_preserve_existing_organization(monkeypatch):
    url = os.environ.get("BLOFIN_ACTIVITY_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set BLOFIN_ACTIVITY_TEST_POSTGRES_URL to disposable PostgreSQL.")
    admin = create_engine(url)
    schema = "activity_migration_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", isolated_url.render_as_string(hide_password=False))
    # Exercise the real migrations without applying the CLI's process-wide
    # fileConfig, which disables existing loggers and breaks later log checks.
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "src/app/db/migrations")
    )
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["a13voicemonitor001"]
    assert script.get_revision("a13voicemonitor001").down_revision == "a12trendpulsescreen001"
    assert script.get_revision("a12trendpulsescreen001").down_revision == "a11experiments001"
    assert script.get_revision("a11experiments001").down_revision == "a10blofinactivity001"
    assert script.get_revision("a10blofinactivity001").down_revision == "a9knowledgeoutbox001"
    assert script.get_revision("a9knowledgeoutbox001").nextrev == frozenset(
        {"a10blofinactivity001"}
    )
    engine = create_engine(isolated_url)
    try:
        loggers = [logging.getLogger(name) for name in ("", "httpx", "httpcore")]
        logging_before = [
            (logger.disabled, logger.level, tuple(logger.handlers)) for logger in loggers
        ]
        _verify_roundtrip(config, engine)
        assert [
            (logger.disabled, logger.level, tuple(logger.handlers)) for logger in loggers
        ] == logging_before
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _verify_roundtrip(config, engine):
    command.upgrade(config, "head")
    sentinel = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, created_at, updated_at) "
                "VALUES (:id, 'Activity migration sentinel', now(), now())"
            ),
            {"id": sentinel},
        )
    added = {"blofin_activity_accounts", "blofin_activity_facts", "blofin_activity_cursors"}
    assert added <= set(inspect(engine).get_table_names())
    command.downgrade(config, "a9knowledgeoutbox001")
    assert not added.intersection(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT name FROM organizations WHERE id=:id"), {"id": sentinel})
            == "Activity migration sentinel"
        )
    command.upgrade(config, "a10blofinactivity001")
    assert added <= set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM organizations WHERE id=:id"), {"id": sentinel})
