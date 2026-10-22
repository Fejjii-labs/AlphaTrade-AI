"""Widen the runtime component column without truncating a full org hex key."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from tests.support.alembic_heads import (
    alembic_ancestry,
    alembic_script_directory,
    assert_down_revision_chain,
    single_alembic_head,
)

_REVISION = "a21killswitchkey001"
_PARENT = "a20autonomoussettings001"
_MIGRATION = "a21killswitchkey001_widen_runtime_component.py"
_FULL_KEY = "k" + "ab" * 16


def _load():
    path = Path(__file__).resolve().parents[1] / "src/app/db/migrations/versions" / _MIGRATION
    spec = importlib.util.spec_from_file_location(_MIGRATION, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_component_widen_revises_autonomous_settings() -> None:
    script = alembic_script_directory()
    assert_down_revision_chain(script, (_REVISION, _PARENT))
    assert _REVISION in alembic_ancestry(single_alembic_head(script))
    source = (
        Path(__file__).resolve().parents[1] / "src/app/db/migrations/versions" / _MIGRATION
    ).read_text(encoding="utf-8")
    assert "longer than 32 characters" in source


def _create_narrow_table(connection) -> None:
    connection.execute(
        text("CREATE TABLE controlled_runtime_status (component VARCHAR(32) PRIMARY KEY)")
    )


def test_widen_roundtrip_refuses_a_full_hex_key_on_sqlite() -> None:
    module = _load()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    assert engine.dialect.name == "sqlite"
    with engine.begin() as connection:
        _create_narrow_table(connection)
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()
        connection.execute(
            text("INSERT INTO controlled_runtime_status (component) VALUES (:key)"),
            {"key": _FULL_KEY},
        )
        assert len(_FULL_KEY) == 33
        context = MigrationContext.configure(connection)
        with Operations.context(context), pytest.raises(RuntimeError, match="longer than 32"):
            module.downgrade()
        stored = connection.scalar(text("SELECT component FROM controlled_runtime_status"))
        assert stored == _FULL_KEY
        connection.execute(text("DELETE FROM controlled_runtime_status"))
        connection.execute(
            text("INSERT INTO controlled_runtime_status (component) VALUES ('paper_kill_switch')")
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.downgrade()
            module.upgrade()
        assert "controlled_runtime_status" in set(inspect(connection).get_table_names())
        assert (
            connection.scalar(text("SELECT component FROM controlled_runtime_status"))
            == "paper_kill_switch"
        )


def test_widen_roundtrip_on_postgres() -> None:
    raw = (
        os.environ.get("AT028_POSTGRES_URL")
        or os.environ.get("PHASE1_POSTGRES_URL")
        or os.environ.get("EXPERIMENT_TEST_POSTGRES_URL")
    )
    if not raw:
        pytest.skip("Set AT028_POSTGRES_URL to disposable loopback PostgreSQL.")
    url = make_url(raw)
    if url.get_backend_name() != "postgresql" or url.host not in {"127.0.0.1", "localhost"}:
        pytest.skip("Component widen test requires loopback PostgreSQL.")
    schema = "kill_switch_key_" + uuid4().hex
    admin = create_engine(url, poolclass=NullPool)
    isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(isolated, poolclass=NullPool)
    module = _load()
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        with engine.begin() as connection:
            _create_narrow_table(connection)
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                module.upgrade()
            length = connection.scalar(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'controlled_runtime_status' "
                    "AND column_name = 'component'"
                )
            )
            assert length == 64
            connection.execute(
                text("INSERT INTO controlled_runtime_status (component) VALUES (:key)"),
                {"key": _FULL_KEY},
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context), pytest.raises(RuntimeError, match="longer than 32"):
                module.downgrade()
            assert length == connection.scalar(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'controlled_runtime_status' "
                    "AND column_name = 'component'"
                )
            )
            connection.execute(text("DELETE FROM controlled_runtime_status"))
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                module.downgrade()
            narrowed = connection.scalar(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'controlled_runtime_status' "
                    "AND column_name = 'component'"
                )
            )
            assert narrowed == 32
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
