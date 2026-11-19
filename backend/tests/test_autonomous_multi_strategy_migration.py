"""a30multistrat001: autonomous settings hold a strategy set; the legacy column stays readable."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from tests.support.alembic_heads import (
    alembic_ancestry,
    alembic_script_directory,
    assert_down_revision_chain,
    single_alembic_head,
)
from tests.support.postgres_persistence import POSTGRES_URL, requires_postgres

_A29 = "a29partialexit001"
_A30 = "a30multistrat001"
_TABLE = "autonomous_trading_settings"


def _config() -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config()
    config.set_main_option("script_location", str(backend_root / "src/app/db/migrations"))
    return config


def _columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(_TABLE)}


def test_a30_revises_a29_on_a_single_head() -> None:
    script = alembic_script_directory()
    assert_down_revision_chain(script, (_A30, _A29))
    ancestry = alembic_ancestry(single_alembic_head(script))
    assert _A30 in ancestry
    assert _A29 in ancestry


def _seed_legacy_rows(engine: Engine) -> tuple[str, str]:
    """One row with a saved strategy, one without; written at a29 (no set column)."""

    org, user, user_two, strategy = (str(uuid4()) for _ in range(4))
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, created_at, updated_at) "
                "VALUES (:id, 'org', now(), now())"
            ),
            {"id": org},
        )
        for user_id in (user, user_two):
            connection.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, role, risk_profile, "
                    "timezone, is_active, email_verified) VALUES "
                    "(:id, :email, 'not-a-secret', 'trader', 'moderate', 'UTC', true, false)"
                ),
                {"id": user_id, "email": f"{user_id}@test.example"},
            )
        connection.execute(
            text(
                "INSERT INTO user_strategies (id, organization_id, user_id, name, setup_type, "
                "current_version, enabled, paper_eligible, created_at, updated_at) VALUES "
                "(:id, :org, :user, 'Nested', 'HTF_TREND_PULLBACK', 1, true, false, now(), now())"
            ),
            {"id": strategy, "org": org, "user": user},
        )
        for user_id, strategy_id in ((user, strategy), (user_two, None)):
            connection.execute(
                text(
                    f"INSERT INTO {_TABLE} (id, organization_id, user_id, "
                    "daily_loss_limit_percent, max_trades_per_day, strategy_id, symbol, "
                    "created_at, updated_at) VALUES "
                    "(:id, :org, :user, 3, 5, :strategy, 'BTCUSDT', now(), now())"
                ),
                {"id": str(uuid4()), "org": org, "user": user_id, "strategy": strategy_id},
            )
    return user, strategy


def _row(engine: Engine, user: str) -> dict[str, object]:
    with engine.connect() as connection:
        row = (
            connection.execute(
                text(f"SELECT * FROM {_TABLE} WHERE user_id = :user"), {"user": user}
            )
            .mappings()
            .one()
        )
    return dict(row)


@requires_postgres
def test_a30_migrates_the_single_strategy_into_the_set(monkeypatch: pytest.MonkeyPatch) -> None:
    admin = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
    schema = "multistrat_" + uuid4().hex
    isolated = make_url(POSTGRES_URL).update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", isolated.render_as_string(hide_password=False))
    config = _config()
    script = ScriptDirectory.from_config(config)
    assert_down_revision_chain(script, (_A30, _A29))
    engine = create_engine(isolated, poolclass=NullPool, future=True)
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        command.upgrade(config, _A29)
        assert "strategy_ids" not in _columns(engine)
        user, strategy = _seed_legacy_rows(engine)

        command.upgrade(config, "head")
        assert {"strategy_id", "strategy_ids", "strategy_scope"} <= _columns(engine)
        migrated = _row(engine, user)
        ids = migrated["strategy_ids"]
        ids = json.loads(ids) if isinstance(ids, str) else ids
        assert ids == [strategy]
        assert str(migrated["strategy_id"]) == strategy, "legacy column stays readable"
        assert migrated["strategy_scope"] == "selected"
        with engine.connect() as connection:
            empty = connection.execute(
                text(f"SELECT strategy_ids, strategy_scope FROM {_TABLE} WHERE strategy_id IS NULL")
            ).one()
        assert empty.strategy_scope == "selected"
        assert empty.strategy_ids in (None, [], "[]")
        with (
            pytest.raises(IntegrityError, match="violates check constraint"),
            engine.begin() as connection,
        ):
            connection.execute(text(f"UPDATE {_TABLE} SET strategy_scope = 'everything'"))

        command.downgrade(config, _A29)
        assert "strategy_ids" not in _columns(engine)
        assert str(_row(engine, user)["strategy_id"]) == strategy

        command.upgrade(config, "head")
        again = _row(engine, user)["strategy_ids"]
        again = json.loads(again) if isinstance(again, str) else again
        assert again == [strategy]
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
