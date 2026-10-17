"""Opt-in knowledge component of the supported supervised paper-worker process.

One thread, two jobs and one document reconciliation per cycle, 32 embeddings per
batch, 2,048 chunks per document, five attempts and 300-second claim leases.
No trading runtime is built here. Provider calls have their configured timeouts;
SIGTERM finishes the current bounded cycle and stops further cycles.
"""

from __future__ import annotations

import threading

from app.core.config import Settings
from app.db.session import get_session_factory
from app.providers.factory import resolve_providers
from app.providers.qdrant import QdrantVectorStore
from app.rag.indexing import IndexingRunner
from app.workers.paper_worker import CycleOutcome


class KnowledgeIndexingCycle:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runner: IndexingRunner | None = None
        self._stop = threading.Event()

    def __call__(self) -> CycleOutcome:
        if self.runner is None:
            factory = get_session_factory()
            if factory.kw["bind"].dialect.name != "postgresql":
                raise RuntimeError("knowledge_worker_requires_postgresql")
            providers = resolve_providers(self.settings)
            self.runner = IndexingRunner(
                factory,
                embeddings=providers.embeddings,
                vector_store=providers.vector_store,
                settings=self.settings,
                stopping=self._stop.is_set,
            )
        if isinstance(self.runner.store, QdrantVectorStore):
            self.runner.store.reconnect()
        outcomes = self.runner.run_once(max_jobs=2)
        repaired = self.runner.reconcile_once(limit=1) if not self._stop.is_set() else 0
        counts = self.runner.queue_counts()
        if counts.get("failed") or any(outcome in {"failed", "pending"} for outcome in outcomes):
            return CycleOutcome("degraded", error="knowledge_indexing_retry_or_failure")
        if counts.get("pending") or counts.get("processing"):
            return CycleOutcome("pending")
        return CycleOutcome("success" if outcomes or repaired else "idle")

    def close(self) -> None:
        if self.runner is not None:
            client = getattr(self.runner.store, "_client", None)
            if client is not None:
                client.close()

    def request_stop(self) -> None:
        self._stop.set()
