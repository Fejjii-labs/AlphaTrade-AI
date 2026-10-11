"""The additive metadata revision preserves legacy documents and owned schemas."""

import importlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import JSON, Column, MetaData, String, Table, create_engine, inspect, select, text
from sqlalchemy.engine import make_url


def test_knowledge_metadata_revision_precedes_the_single_migration_head():
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "src/app/db/migrations"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["a13voicemonitor001"]
    assert script.get_revision("a13voicemonitor001").down_revision == "a12trendpulsescreen001"
    assert script.get_revision("a12trendpulsescreen001").down_revision == "a11experiments001"
    assert script.get_revision("a11experiments001").down_revision == "a10blofinactivity001"
    assert script.get_revision("a10blofinactivity001").down_revision == "a9knowledgeoutbox001"
    assert script.get_revision("a9knowledgeoutbox001").down_revision == "a8agentcapture001"
    assert script.get_revision("a8agentcapture001").down_revision == "a7manualrecovery001"
    assert script.get_revision("a7manualrecovery001").down_revision == "a6manualdemo001"
    assert script.get_revision("a6manualdemo001").down_revision == "a5demolifecycle001"
    assert script.get_revision("a5demolifecycle001").down_revision == "a4knowledge001"
    assert script.get_revision("a4knowledge001").down_revision == "a3release002"


def test_knowledge_metadata_postgres_upgrade_and_downgrade_preserve_legacy_document():
    raw = os.environ.get("KNOWLEDGE_POSTGRES_URL") or os.environ.get("AT028_POSTGRES_URL")
    if not raw:
        pytest.skip(
            "Set KNOWLEDGE_POSTGRES_URL or AT028_POSTGRES_URL to a disposable test database"
        )
    url = make_url(raw)
    assert url.get_backend_name() == "postgresql"
    assert url.database in {"alphatrade_knowledge_test", "alphatrade_test"}, (
        "Never migrate a shared/product database"
    )
    schema = "knowledge_import_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url.update_query_dict({"options": f"-csearch_path={schema}"}))
    revision = importlib.import_module(
        "app.db.migrations.versions.a4knowledge001_file_ingestion_metadata"
    )
    legacy = Table(
        "documents", MetaData(), Column("id", String, primary_key=True), Column("title", String)
    )
    try:
        legacy.create(engine)
        with engine.begin() as connection:
            connection.execute(legacy.insert().values(id="legacy", title="Existing pasted note"))
            with Operations.context(MigrationContext.configure(connection)):
                revision.upgrade()
            columns = {
                column["name"]: column for column in inspect(connection).get_columns("documents")
            }
            assert columns["ingestion_metadata"]["nullable"]
            assert isinstance(columns["ingestion_metadata"]["type"], JSON)
            upgraded = Table("documents", MetaData(), autoload_with=connection)
            assert connection.scalar(select(upgraded.c.ingestion_metadata)) is None
            metadata = {"file": {"filename": "synthetic.txt", "raw_content_hash": "a" * 64}}
            connection.execute(
                upgraded.insert().values(
                    id="new", title="Synthetic import", ingestion_metadata=metadata
                )
            )
            assert (
                connection.scalar(
                    select(upgraded.c.ingestion_metadata).where(upgraded.c.id == "new")
                )
                == metadata
            )
            with Operations.context(MigrationContext.configure(connection)):
                revision.downgrade()
            assert {column["name"] for column in inspect(connection).get_columns("documents")} == {
                "id",
                "title",
            }
            assert dict(connection.execute(select(legacy.c.id, legacy.c.title)).all()) == {
                "legacy": "Existing pasted note",
                "new": "Synthetic import",
            }
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
