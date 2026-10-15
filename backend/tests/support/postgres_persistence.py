"""PostgreSQL helpers for watcher/telegram persistence tests."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.db.base import Base
from app.db.canonical_candidates import CanonicalCandidateRow  # noqa: F401
from app.db.canonical_eligibility import ActionEligibilityEvaluationRow  # noqa: F401
from app.db.canonical_trade_plans import CanonicalTradePlanRootRow  # noqa: F401
from app.db.models import Organization  # noqa: F401  — register metadata
from app.db.paper_evaluation import PaperEvaluationObservationRow  # noqa: F401
from app.db.telegram_paper_agent import TelegramPaperNotificationRow  # noqa: F401
from app.db.telegram_security import TelegramBindingRow  # noqa: F401
from app.db.watcher_orchestration import WatcherWorkerLeaseRow  # noqa: F401
from tests.support.postgres_template import (
    FULL_SCHEMA_MARKER,
    PERSISTENCE_SCHEMA_MARKER,
    refresh_database,
    register_template,
)

POSTGRES_URL = os.environ.get(
    "PHASE1_POSTGRES_URL",
    os.environ.get(
        "AT028_POSTGRES_URL",
        "postgresql+psycopg://alphatrade:alphatrade@localhost:5432/alphatrade_test",
    ),
)


def postgres_available() -> bool:
    try:
        engine = create_engine(POSTGRES_URL, poolclass=NullPool)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not postgres_available(),
    reason=f"PostgreSQL not reachable at {POSTGRES_URL}",
)


def _persistence_tables() -> list[object]:
    return [
        table
        for table in Base.metadata.sorted_tables
        if table.name in {"organizations", "users", "user_notification_preferences"}
        or table.name.startswith(
            ("watcher_", "telegram_", "canonical_candidate", "action_eligibility", "controlled_")
        )
    ]


def _mark_template(engine: Engine, marker: str) -> None:
    if "'" in marker:
        raise ValueError("template marker must not contain quotes")
    with engine.begin() as conn:
        conn.execute(text(f"COMMENT ON SCHEMA public IS '{marker}'"))


def _prepare_persistence_template(engine: Engine) -> None:
    Base.metadata.create_all(engine, tables=_persistence_tables())
    _mark_template(engine, PERSISTENCE_SCHEMA_MARKER)


def _prepare_full_template(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _mark_template(engine, FULL_SCHEMA_MARKER)


def persistence_session_factory() -> sessionmaker[Session]:
    """Narrow watcher/telegram schema cloned from the session template."""
    engine = refresh_database(POSTGRES_URL, kind="persistence")
    return sessionmaker(bind=engine, expire_on_commit=False)


def phase7_plan_session_factory() -> sessionmaker[Session]:
    """Full schema for canonical TradePlanRevision PostgreSQL tests."""
    engine = refresh_database(POSTGRES_URL, kind="full")
    return sessionmaker(bind=engine, expire_on_commit=False)


def _plan_schema_state(engine: Engine) -> str:
    """Read actual DDL identity; another fixture's reset cannot leave stale state."""
    with engine.connect() as conn:
        return conn.scalar(
            text("""
                WITH relations AS (
                    SELECT c.* FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public'
                ), objects AS (
                    SELECT 'schema' kind, oid::text identity, nspname::text definition
                    FROM pg_namespace WHERE nspname='public'
                    UNION ALL
                    SELECT 'relation', oid::text,
                           concat_ws(':',relname,relkind,relpersistence,
                                     relrowsecurity,relforcerowsecurity)
                    FROM relations
                    UNION ALL
                    SELECT 'column', concat_ws(':',a.attrelid,a.attnum), to_jsonb(a)::text
                    FROM pg_attribute a JOIN relations r ON r.oid=a.attrelid
                    WHERE a.attnum>0
                    UNION ALL
                    SELECT 'default', a.oid::text, pg_get_expr(a.adbin,a.adrelid)
                    FROM pg_attrdef a JOIN relations r ON r.oid=a.adrelid
                    UNION ALL
                    SELECT 'constraint', c.oid::text, to_jsonb(c)::text
                    FROM pg_constraint c JOIN relations r ON r.oid=c.conrelid
                    UNION ALL
                    SELECT 'index', i.indexrelid::text, pg_get_indexdef(i.indexrelid)
                    FROM pg_index i JOIN relations r ON r.oid=i.indrelid
                    UNION ALL
                    SELECT 'trigger', t.oid::text,
                           concat_ws(':',t.tgenabled,pg_get_triggerdef(t.oid))
                    FROM pg_trigger t JOIN relations r ON r.oid=t.tgrelid
                    UNION ALL
                    SELECT 'function', p.oid::text, pg_get_functiondef(p.oid)
                    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname='public' AND p.prokind IN ('f','p')
                    UNION ALL
                    SELECT 'sequence', s.seqrelid::text, to_jsonb(s)::text
                    FROM pg_sequence s JOIN relations r ON r.oid=s.seqrelid
                )
                SELECT md5(coalesce(jsonb_agg(to_jsonb(objects)
                    ORDER BY kind,identity)::text,'')) FROM objects
            """)
        )


class ReusablePhase7Database:
    """Module-owned full schema, with committed data cleared for every test.

    The first use clones the session template into the fixture database. Later
    uses TRUNCATE when the catalog still matches, so object identity stays stable.
    Migration fixtures do not use this class. Any external reset or DDL change
    clones the template again. No process-global schema cache.
    """

    def __init__(self) -> None:
        self.engine = create_engine(POSTGRES_URL, poolclass=NullPool, future=True)
        self._schema_state: str | None = None

    def session_factory(self) -> sessionmaker[Session]:
        if self._schema_state is None or self._schema_state != self._read_schema_state():
            self._rebuild_from_template()
        else:
            self._truncate()
        # TRUNCATE changes data/sequence values, not the checked DDL. Keep the
        # verified state and check it again before the next test's reuse.
        return sessionmaker(bind=self.engine, expire_on_commit=False)

    def _read_schema_state(self) -> str | None:
        try:
            return _plan_schema_state(self.engine)
        except Exception:
            # The fixture database was replaced (or connections were terminated).
            return None

    def _rebuild_from_template(self) -> None:
        self.engine.dispose()
        self.engine = refresh_database(POSTGRES_URL, kind="full")
        self._schema_state = _plan_schema_state(self.engine)

    def _truncate(self) -> None:
        tables = ", ".join(
            self.engine.dialect.identifier_preparer.format_table(table)
            for table in Base.metadata.sorted_tables
        )
        with self.engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    def dispose(self) -> None:
        self.engine.dispose()


register_template("full", _prepare_full_template)
register_template("persistence", _prepare_persistence_template)
