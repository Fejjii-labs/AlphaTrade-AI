"""Database engine and session management.

PostgreSQL is the production target. A synchronous engine keeps the persistence
layer simple and easy to test; FastAPI runs sync DB work in a threadpool. The
engine is created lazily from settings so importing this module has no side
effects (important for tests that use their own SQLite engine).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.persistence_firewall import install_persistence_firewall

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _connect_args(database_url: str) -> dict[str, bool]:
    # SQLite (tests) needs this flag when shared across threads.
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def _pool_args(settings: Settings) -> dict[str, int]:
    """Explicit pool sizing for server databases; SQLite keeps its own pool."""

    if settings.database_url.startswith("sqlite"):
        return {}
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout_seconds,
        "pool_recycle": settings.db_pool_recycle_seconds,
    }


def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
            connect_args=_connect_args(settings.database_url),
            **_pool_args(settings),
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a session and ensuring cleanup."""
    install_persistence_firewall()
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def _dbapi_connection_in_transaction(session: Session) -> bool | None:
    """Return DBAPI in-transaction flag when available, else None."""
    try:
        dbapi = session.connection().connection.dbapi_connection
    except Exception:
        return None
    flag = getattr(dbapi, "in_transaction", None)
    if callable(flag):
        return bool(flag())
    if isinstance(flag, bool):
        return flag
    return None


def run_in_savepoint_when_active[T](session: Session, work: Callable[[], T]) -> T:
    """Run ``work`` under a nested savepoint when safe; otherwise run directly.

    Nested savepoints isolate flush failures so fail-open audit/usage cannot wipe
    already-flushed business rows. On SQLite/pysqlite, ``RELEASE`` of a SAVEPOINT
    that *started* the DB transaction commits it — so we only nest when the DBAPI
    connection already has an open transaction (typically after prior DML/flush
    in this unit-of-work). PostgreSQL and other dialects nest whenever the
    Session already has a transaction.
    """
    dbapi_active = _dbapi_connection_in_transaction(session)
    if dbapi_active is True:
        with session.begin_nested():
            return work()
    if dbapi_active is False:
        return work()
    if session.in_transaction():
        with session.begin_nested():
            return work()
    return work()
