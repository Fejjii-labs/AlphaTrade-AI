"""Explicit worker advancement for deterministic ingestion/retrieval fixtures."""

from sqlalchemy.orm import sessionmaker

from app.rag.indexing import IndexingRunner


def drain_indexing(service):
    session = service._session
    session.commit()
    worker = IndexingRunner(
        sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        vector_store=service._vector_store,
        embeddings=service._embeddings,
        settings=service._settings,
    )
    outcomes = worker.run_once(max_jobs=8)
    session.expire_all()
    return outcomes
