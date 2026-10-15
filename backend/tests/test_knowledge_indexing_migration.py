"""Upgrade from the reviewed head, roundtrip, and preserve legacy content."""

import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text


def test_indexing_migration_from_reviewed_head_and_roundtrip(monkeypatch):
    url = (
        os.environ.get("INDEXING_MIGRATION_POSTGRES_URL")
        or os.environ.get("PHASE1_POSTGRES_URL")
        or os.environ.get("AT028_POSTGRES_URL")
    )
    if not url:
        import pytest

        pytest.skip("Set INDEXING_MIGRATION_POSTGRES_URL to a disposable loopback database.")
    engine = create_engine(url)
    assert engine.url.host in {"localhost", "127.0.0.1"}
    assert engine.url.database in {"agent3_migrations", "alphatrade_test"}
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "src/app/db/migrations"))
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["a13voicemonitor001"]
    assert scripts.get_revision("a13voicemonitor001").down_revision == "a12trendpulsescreen001"
    assert scripts.get_revision("a12trendpulsescreen001").down_revision == "a11experiments001"
    assert scripts.get_revision("a11experiments001").down_revision == "a10blofinactivity001"
    assert scripts.get_revision("a10blofinactivity001").down_revision == "a9knowledgeoutbox001"
    assert scripts.get_revision("a9knowledgeoutbox001").down_revision == "a8agentcapture001"
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    command.upgrade(config, "a8agentcapture001")
    identifier = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO documents (id,source_type,title,version,tags) VALUES "
                "(:id,'general_note','Legacy',1,'[]')"
            ),
            {"id": identifier},
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "a13voicemonitor001"
        )
        assert (
            connection.scalar(text("SELECT title FROM documents WHERE id=:id"), {"id": identifier})
            == "Legacy"
        )
        assert (
            connection.scalar(
                text("SELECT indexing_generation FROM documents WHERE id=:id"), {"id": identifier}
            )
            is None
        )
        assert "knowledge_indexing_jobs" in inspect(connection).get_table_names()
    command.downgrade(config, "a8agentcapture001")
    with engine.connect() as connection:
        assert "knowledge_indexing_jobs" not in inspect(connection).get_table_names()
        assert (
            connection.scalar(text("SELECT title FROM documents WHERE id=:id"), {"id": identifier})
            == "Legacy"
        )
    command.upgrade(config, "head")
    engine.dispose()
