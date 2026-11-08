"""L9: the engine pool is sized explicitly for server databases."""

from __future__ import annotations

from app.core.config import Settings
from app.db.session import _pool_args


def test_postgres_engine_gets_explicit_pool_arguments() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        db_pool_size=4,
        db_max_overflow=2,
    )
    assert _pool_args(settings) == {
        "pool_size": 4,
        "max_overflow": 2,
        "pool_timeout": 30,
        "pool_recycle": 1800,
    }


def test_sqlite_keeps_its_default_pool() -> None:
    assert _pool_args(Settings(_env_file=None, database_url="sqlite://")) == {}
