"""Upgrade from the reviewed head, roundtrip, and preserve legacy content.

The roundtrip uses a private schema on the disposable database. It must not
drop ``public`` on the shared ``alphatrade_test`` database.
"""

import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


def test_indexing_migration_from_reviewed_head_and_roundtrip(monkeypatch):
    url = (
        os.environ.get("INDEXING_MIGRATION_POSTGRES_URL")
        or os.environ.get("PHASE1_POSTGRES_URL")
        or os.environ.get("AT028_POSTGRES_URL")
    )
    if not url:
        import pytest

        pytest.skip("Set INDEXING_MIGRATION_POSTGRES_URL to a disposable loopback database.")
    admin = create_engine(url)
    assert admin.url.host in {"localhost", "127.0.0.1"}
    assert admin.url.database in {"agent3_migrations", "alphatrade_test"}
    schema = "knowledge_indexing_" + uuid4().hex
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "src/app/db/migrations"))
    isolated = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", isolated.render_as_string(hide_password=False))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["a19riskpolicy004"]
    assert scripts.get_revision("a19riskpolicy004").down_revision == "a18riskpolicy003"
    assert scripts.get_revision("a18riskpolicy003").down_revision == "a17riskpolicy002"
    assert scripts.get_revision("a17riskpolicy002").down_revision == "a16riskpolicy001"
    assert scripts.get_revision("a16riskpolicy001").down_revision == "a15reissueauth001"
    assert scripts.get_revision("a15reissueauth001").down_revision == "a14autonomouschannel001"
    assert scripts.get_revision("a14autonomouschannel001").down_revision == "a13voicemonitor001"
    assert scripts.get_revision("a13voicemonitor001").down_revision == "a12trendpulsescreen001"
    assert scripts.get_revision("a12trendpulsescreen001").down_revision == "a11experiments001"
    assert scripts.get_revision("a11experiments001").down_revision == "a10blofinactivity001"
    assert scripts.get_revision("a10blofinactivity001").down_revision == "a9knowledgeoutbox001"
    assert scripts.get_revision("a9knowledgeoutbox001").down_revision == "a8agentcapture001"
    engine = create_engine(isolated)
    with admin.connect() as connection:
        public_version = (
            connection.scalar(text("SELECT version_num FROM public.alembic_version"))
            if connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
            else None
        )
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
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
                == "a19riskpolicy004"
            )
            assert (
                connection.scalar(
                    text("SELECT title FROM documents WHERE id=:id"), {"id": identifier}
                )
                == "Legacy"
            )
            assert (
                connection.scalar(
                    text("SELECT indexing_generation FROM documents WHERE id=:id"),
                    {"id": identifier},
                )
                is None
            )
            assert "knowledge_indexing_jobs" in inspect(connection).get_table_names()
        command.downgrade(config, "a8agentcapture001")
        with engine.connect() as connection:
            assert "knowledge_indexing_jobs" not in inspect(connection).get_table_names()
            assert (
                connection.scalar(
                    text("SELECT title FROM documents WHERE id=:id"), {"id": identifier}
                )
                == "Legacy"
            )
        command.upgrade(config, "head")
        with admin.connect() as connection:
            after = (
                connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                if connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
                else None
            )
        assert after == public_version
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
