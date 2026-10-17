#!/usr/bin/env python3
"""Deterministic RAG evaluation runner (Slice 12 foundation).

Loads ``evaluation/datasets/rag_cases.json``, ingests a fixed corpus, runs
retrieval for each case, and reports pass/fail against expected metadata.
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from app.providers.qdrant import reset_process_vector_store  # noqa: E402
from app.schemas.common import DocumentSourceType  # noqa: E402
from app.schemas.rag import IngestDocumentRequest, RagQuery  # noqa: E402
from app.services.rag_service import build_rag_service  # noqa: E402

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")

CORPUS: list[IngestDocumentRequest] = [
    IngestDocumentRequest(
        organization_id=ORG_ID,
        source_type=DocumentSourceType.RISK_POLICY,
        title="Risk Policy",
        text=(
            "Capital preservation first. Every trade requires a stop loss. "
            "No position without defined invalidation."
        ),
        risk_tag="stop_loss",
    ),
    IngestDocumentRequest(
        organization_id=ORG_ID,
        source_type=DocumentSourceType.TRADING_PLAYBOOK,
        title="Trading Playbook",
        text=(
            "Human approval is required before any executable order. "
            "The agent must not bypass approval or risk checks."
        ),
    ),
    IngestDocumentRequest(
        organization_id=ORG_ID,
        source_type=DocumentSourceType.TRADE_JOURNAL,
        title="Trade Journal — Revenge Trade",
        text=(
            "Lesson: after a revenge trade, enforce a cooldown period. "
            "Emotional trading violates the playbook."
        ),
    ),
    IngestDocumentRequest(
        organization_id=ORG_ID,
        source_type=DocumentSourceType.STRATEGY_TEMPLATE,
        title="HTF Trend Pullback Template",
        text=(
            "Pullback entry rules: trade only with higher timeframe trend alignment. "
            "Wait for pullback into value on BTC setups."
        ),
        strategy_tag="htf_trend_pullback",
        symbol_tag="BTCUSDT",
    ),
    IngestDocumentRequest(
        organization_id=ORG_ID,
        source_type=DocumentSourceType.MISTAKES_DATABASE,
        title="Mistakes Database",
        text=(
            "Common mistake: chasing green candles due to FOMO. "
            "Apply green day guard before adding risk."
        ),
    ),
]


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    detail: str


def _load_cases() -> list[dict]:
    path = REPO_ROOT / "evaluation" / "datasets" / "rag_cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _ingest_corpus(service: object) -> None:
    for doc in CORPUS:
        service.ingest(doc)  # type: ignore[attr-defined]


def _evaluate_case(service: object, case: dict) -> EvalResult:
    query = RagQuery(
        query=case["query"],
        organization_id=ORG_ID,
        source_types=[DocumentSourceType(case["expected_source_type"])],
        top_k=3,
    )
    if case["id"] == "strategy_tag_filter":
        query = query.model_copy(update={"strategy_tag": "htf_trend_pullback"})

    result = service.search(query)  # type: ignore[attr-defined]
    if not result.chunks:
        return EvalResult(case["id"], False, "no chunks retrieved")

    top = result.chunks[0]
    title_ok = case["expected_document_title"].lower() in (top.title or "").lower()
    type_ok = top.source_type.value == case["expected_source_type"]
    content_lower = top.content.lower()
    points_hit = sum(1 for p in case["expected_answer_points"] if p.lower() in content_lower)
    passed = title_ok and type_ok and points_hit >= 1
    detail = (
        f"title_ok={title_ok} type_ok={type_ok} points_hit={points_hit}/{len(case['expected_answer_points'])}"
    )
    return EvalResult(case["id"], passed, detail)


def main() -> int:
    reset_process_vector_store()
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base

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
        from app.db.models import Organization

        session.add(Organization(id=ORG_ID, name="Eval Org"))
        session.commit()
        service = build_rag_service(session=session)
        _ingest_corpus(service)
        session.commit()

        from app.rag.indexing import IndexingRunner
        from app.db.models import KnowledgeIndexingJob
        from sqlalchemy import select
        runner = IndexingRunner(factory, vector_store=service._vector_store,
                                embeddings=service._embeddings, settings=service._settings)
        # Advance the real outbox in this isolated deterministic fixture. Search
        # assertions require a ready ack; ingestion stays asynchronous.
        for _ in range(len(CORPUS) + 1):
            runner.run_once(max_jobs=len(CORPUS))
            session.expire_all()
            if all(job.status == "ready" for job in session.scalars(select(KnowledgeIndexingJob))):
                break
        else:
            raise RuntimeError("Evaluation corpus did not become ready within its bounded worker budget")

        results = [_evaluate_case(service, case) for case in _load_cases()]

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.case_id}: {result.detail}")

    print(f"\nRAG evaluation: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
