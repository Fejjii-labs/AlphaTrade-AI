"""Isolated SQLite/mock browser fixture with an asynchronous outbox consumer.

Never used by production services or the PostgreSQL worker.
"""

import asyncio
from contextlib import asynccontextmanager, suppress

import uvicorn

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.main import app
from app.rag.indexing import IndexingRunner
from app.services.rag_service import build_rag_service

settings = get_settings()
if not settings.database_url.startswith("sqlite") or settings.provider_mode != "mock":
    raise RuntimeError("Browser fixture requires isolated SQLite and mock providers")
SessionLocal = get_session_factory()
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def fixture_lifespan(application):
    async with original_lifespan(application):
        with SessionLocal() as session:
            service = build_rag_service(session=session)
            runner = IndexingRunner(
                SessionLocal,
                vector_store=service._vector_store,
                embeddings=service._embeddings,
                settings=settings,
            )

        async def consume():
            while True:
                # SQLite fixture work advances on this event loop between requests.
                runner.run_once(max_jobs=8)
                await asyncio.sleep(0.1)

        worker = asyncio.create_task(consume())
        try:
            yield
        finally:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker


app.router.lifespan_context = fixture_lifespan
if __name__ == "__main__":
    uvicorn.run(app, port=8000, host="127.0.0.1")
