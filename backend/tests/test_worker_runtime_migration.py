"""Postgres upgrade, downgrade, and re-upgrade of a22workerruntime001."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from tests.support.alembic_heads import (
    alembic_ancestry,
    alembic_script_directory,
    assert_down_revision_chain,
    single_alembic_head,
)
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres

_A21 = "a21killswitchkey001"
_PAPER_DAY = "a22papertradeday001"
_A22 = "a22workerruntime001"
_TABLES = frozenset({"worker_runtime_state", "autonomous_paper_switches"})


def _config() -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config()
    config.set_main_option("script_location", str(backend_root / "src/app/db/migrations"))
    return config


def _version(engine: object) -> str | None:
    with engine.connect() as connection:  # type: ignore[attr-defined]
        value = connection.scalar(text("SELECT version_num FROM alembic_version"))
    return value if isinstance(value, str) else None


def _tables(engine: object) -> set[str]:
    return set(inspect(engine).get_table_names())  # type: ignore[arg-type]


def test_worker_runtime_revises_paper_trade_day_on_a_single_head() -> None:
    script = alembic_script_directory()
    assert_down_revision_chain(script, (_A22, _PAPER_DAY, _A21))
    head = single_alembic_head(script)
    assert script.get_heads() == [head]
    ancestry = alembic_ancestry(head)
    assert _A22 in ancestry
    assert _PAPER_DAY in ancestry


@requires_postgres
def test_a22_upgrade_downgrade_reupgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    """Head creates both tables, the paper-day parent drops them, and head creates them again."""

    admin = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    schema = "worker_runtime_" + uuid4().hex
    isolated = make_url(POSTGRES_URL).update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", isolated.render_as_string(hide_password=False))
    config = _config()
    script = ScriptDirectory.from_config(config)
    assert_down_revision_chain(script, (_A22, _PAPER_DAY, _A21))
    engine = create_engine(isolated, poolclass=NullPool, future=True)
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(config, "head")
        head = single_alembic_head(script)
        assert _version(engine) == head
        assert _tables(engine) >= _TABLES
        command.downgrade(config, _PAPER_DAY)
        assert _version(engine) == _PAPER_DAY
        assert _TABLES.isdisjoint(_tables(engine))
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("account_risk_accounting_states")
        }
        assert "trade_slot_business_date" in columns
        command.upgrade(config, "head")
        assert _version(engine) == head
        assert _tables(engine) >= _TABLES
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
