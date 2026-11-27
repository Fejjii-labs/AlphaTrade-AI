"""Audit M-7: every PostgreSQL connection carries connect, statement, lock and
idle-in-transaction limits; SQLite keeps its own flag."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

from app.core.config import Settings
from app.db.session import _connect_args


def test_sqlite_keeps_its_flag_and_postgres_gets_limits() -> None:
    settings = Settings(_env_file=None)
    assert _connect_args("sqlite:///x.db", settings) == {"check_same_thread": False}
    args = _connect_args("postgresql+psycopg://u:p@h/db", settings)
    assert args["connect_timeout"] == 10
    assert args["options"] == (
        "-c statement_timeout=300000 -c lock_timeout=60000 "
        "-c idle_in_transaction_session_timeout=600000"
    )
    off = Settings(_env_file=None, db_statement_timeout_seconds=0)
    assert "-c statement_timeout=0 " in str(_connect_args("postgresql://h/db", off)["options"])


def test_limits_apply_on_a_real_connection() -> None:
    url = os.getenv("PHASE1_POSTGRES_URL") or os.getenv("AT028_POSTGRES_URL")
    if not url:
        pytest.skip("Requires a disposable PHASE1_POSTGRES_URL or AT028_POSTGRES_URL")
    settings = Settings(_env_file=None, db_statement_timeout_seconds=2)
    engine = create_engine(url, connect_args=_connect_args(url, settings))
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SHOW statement_timeout")).scalar() == "2s"
            assert connection.execute(text("SHOW lock_timeout")).scalar() == "1min"
            with pytest.raises(Exception, match="statement timeout"):
                connection.execute(text("SELECT pg_sleep(3)"))
    finally:
        engine.dispose()
