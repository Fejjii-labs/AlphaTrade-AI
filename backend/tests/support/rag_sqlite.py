"""In-memory SQLite world for RagService + indexing tests (no Postgres needed)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000030")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000031")


@contextmanager
def sqlite_rag_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        from app.db.models import Organization, User

        session.add_all(
            [
                Organization(id=ORG_ID, name="RAG sqlite org"),
                User(id=USER_ID, email="rag-sqlite@test.example", hashed_password="hash"),
            ]
        )
        session.commit()
        yield session
    engine.dispose()
