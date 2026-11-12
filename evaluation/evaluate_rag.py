#!/usr/bin/env python3
"""RAG retrieval evaluation: recall@k and MRR over a corpus with same-type distractors.

Ingests ``datasets/rag_corpus.json`` (several documents per source type, so the
right document must outrank topically close neighbours), runs every case in
``datasets/rag_cases.json`` through the real pipeline (RagService ingest ->
indexing outbox -> vector search -> scope/filter checks -> relevance floor) with
NO source-type pre-filter, and scores the ranked document keys.

Case kinds:
- ``retrieval``: scored by recall@1/3/5 and MRR against ``expected_document_keys``.
- ``filter``: filter-behaviour case (explicit tag filters); scored separately and
  must rank the expected document first with every hit matching the filters.
- ``no_relevant_source``: must return zero hits after the relevance floor.

Exit status is non-zero when recall@3 < ``--min-recall-at-3`` (default 0.80), MRR
< ``--min-mrr`` (default 0.70), the no-relevant-source accuracy is below
``--min-no-relevant-accuracy`` (default 1.0), or a filter case fails.

Embeddings (``--embeddings``):
- ``lexical`` (default, offline, free): deterministic feature-hashed term vectors.
  A lexical baseline that tests the retrieval pipeline, NOT semantic quality.
- ``hash``: the app's SHA-256 mock embeddings. No semantic signal; expected to FAIL.
- ``provider``: the configured embeddings provider (OpenAI). Costs money and needs
  OPENAI_API_KEY; never run in CI. Thresholds still apply.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = REPO_ROOT / "evaluation"
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))
sys.path.insert(0, str(EVAL_DIR))

from rag_metrics import mean, recall_at_k, reciprocal_rank  # noqa: E402

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
K_VALUES = (1, 3, 5)
TOP_K = 5
MIN_RECALL_AT_3 = 0.80
MIN_MRR = 0.70
MIN_NO_RELEVANT_ACCURACY = 1.0

RankFn = Callable[[dict[str, Any]], list[str]]


@dataclass(frozen=True)
class Thresholds:
    min_recall_at_3: float = MIN_RECALL_AT_3
    min_mrr: float = MIN_MRR
    min_no_relevant_accuracy: float = MIN_NO_RELEVANT_ACCURACY


@dataclass
class CaseResult:
    case_id: str
    kind: str
    ranked: list[str]
    expected: list[str]
    passed: bool
    recall: dict[int, float] = field(default_factory=dict)
    reciprocal_rank: float | None = None

    @property
    def detail(self) -> str:
        if self.kind == "no_relevant_source":
            return f"hits={len(self.ranked)} (expected 0)"
        rr = self.reciprocal_rank or 0.0
        recall = " ".join(f"r@{k}={v:.0f}" for k, v in self.recall.items())
        return f"rr={rr:.2f} {recall} top={self.ranked[:3]}"


@dataclass
class Report:
    results: list[CaseResult]
    recall_at: dict[int, float]
    mrr: float
    no_relevant_accuracy: float
    failures: list[str]

    @property
    def passed(self) -> bool:
        return not self.failures


def load_json(name: str) -> list[dict[str, Any]]:
    return json.loads((EVAL_DIR / "datasets" / name).read_text(encoding="utf-8"))


def score_case(case: dict[str, Any], ranked: list[str], corpus: dict[str, dict]) -> CaseResult:
    kind, expected = case["kind"], list(case["expected_document_keys"])
    if kind == "no_relevant_source":
        return CaseResult(case["id"], kind, ranked, expected, passed=not ranked)
    recall = {k: recall_at_k(ranked, expected, k) for k in K_VALUES}
    rr = reciprocal_rank(ranked, expected)
    if kind == "filter":
        filters = case.get("filters", {})
        in_scope = all(
            all(corpus[key].get(tag) == value for tag, value in filters.items()) for key in ranked
        )
        passed = rr == 1.0 and in_scope
    else:
        passed = recall[3] == 1.0
    return CaseResult(case["id"], kind, ranked, expected, passed, recall, rr)


def evaluate(
    cases: Sequence[dict[str, Any]],
    rank: RankFn,
    corpus: dict[str, dict],
    thresholds: Thresholds = Thresholds(),
) -> Report:
    results = [score_case(case, rank(case), corpus) for case in cases]
    retrieval = [r for r in results if r.kind == "retrieval"]
    negatives = [r for r in results if r.kind == "no_relevant_source"]
    recall_at = {k: mean([r.recall[k] for r in retrieval]) for k in K_VALUES}
    mrr = mean([r.reciprocal_rank or 0.0 for r in retrieval])
    no_relevant = mean([1.0 if r.passed else 0.0 for r in negatives]) if negatives else 1.0
    failures: list[str] = []
    if recall_at[3] < thresholds.min_recall_at_3:
        failures.append(f"recall@3 {recall_at[3]:.3f} < {thresholds.min_recall_at_3:.2f}")
    if mrr < thresholds.min_mrr:
        failures.append(f"MRR {mrr:.3f} < {thresholds.min_mrr:.2f}")
    if no_relevant < thresholds.min_no_relevant_accuracy:
        failures.append(
            f"no-relevant-source accuracy {no_relevant:.3f} < "
            f"{thresholds.min_no_relevant_accuracy:.2f}"
        )
    failures.extend(
        f"filter case {r.case_id} failed" for r in results if r.kind == "filter" and not r.passed
    )
    return Report(results, recall_at, mrr, no_relevant, failures)


def build_embeddings(mode: str) -> Any:
    if mode == "lexical":
        from lexical_embeddings import LexicalEmbeddingsProvider

        return LexicalEmbeddingsProvider()
    if mode == "hash":
        from app.providers.embeddings import MockEmbeddingsProvider

        return MockEmbeddingsProvider()
    if mode == "provider":
        from app.core.config import get_settings
        from app.providers.embeddings import MockEmbeddingsProvider
        from app.providers.factory import resolve_providers

        embeddings = resolve_providers(get_settings()).embeddings
        if isinstance(embeddings, MockEmbeddingsProvider):
            raise SystemExit("--embeddings provider resolved to mock embeddings; configure OpenAI.")
        print("WARNING: --embeddings provider calls the paid embeddings API.", file=sys.stderr)
        return embeddings
    raise ValueError(f"unknown embeddings mode: {mode}")


@contextmanager
def pipeline_rank_fn(embeddings: Any, *, min_score: float) -> Iterator[tuple[RankFn, dict]]:
    """Ingest + index the corpus into an isolated SQLite/in-memory store; yield a ranker."""
    from sqlalchemy import create_engine, event, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.core.config import Settings
    from app.db.base import Base
    from app.db.models import KnowledgeIndexingJob, Organization
    from app.providers.qdrant import InMemoryVectorStore
    from app.rag.indexing import IndexingRunner
    from app.schemas.common import DocumentSourceType
    from app.schemas.rag import IngestDocumentRequest, RagQuery
    from app.services.rag_service import RagService

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    settings = Settings(log_json=False, rag_min_relevance_score=min_score)
    corpus = {doc["key"]: doc for doc in load_json("rag_corpus.json")}
    tags = ("strategy_tag", "symbol_tag", "timeframe_tag", "risk_tag")
    try:
        with factory() as session:
            session.add(Organization(id=ORG_ID, name="Eval Org"))
            session.commit()
            store = InMemoryVectorStore()
            service = RagService(
                session, settings=settings, embeddings=embeddings, vector_store=store
            )
            key_by_document: dict[str, str] = {}
            for key, doc in corpus.items():
                response = service.ingest(
                    IngestDocumentRequest(
                        organization_id=ORG_ID,
                        source_type=DocumentSourceType(doc["source_type"]),
                        title=doc["title"],
                        text=doc["text"],
                        **{tag: doc[tag] for tag in tags if tag in doc},
                    )
                )
                key_by_document[str(response.document_id)] = key
            session.commit()
            runner = IndexingRunner(
                factory, vector_store=store, embeddings=embeddings, settings=settings
            )
            for _ in range(len(corpus) + 1):
                runner.run_once(max_jobs=8)
                session.expire_all()
                jobs = session.scalars(select(KnowledgeIndexingJob)).all()
                if all(job.status == "ready" for job in jobs):
                    break
            else:
                raise RuntimeError(
                    "Evaluation corpus did not become ready within its worker budget"
                )

            def rank(case: dict[str, Any]) -> list[str]:
                result = service.search(
                    RagQuery(
                        query=case["query"],
                        organization_id=ORG_ID,
                        top_k=TOP_K,
                        **case.get("filters", {}),
                    )
                )
                ranked: list[str] = []
                for chunk in result.chunks:
                    key = key_by_document[str(chunk.document_id)]
                    if key not in ranked:
                        ranked.append(key)
                return ranked

            yield rank, corpus
    finally:
        engine.dispose()


def run(embeddings_mode: str, *, min_score: float, thresholds: Thresholds) -> Report:
    embeddings = build_embeddings(embeddings_mode)
    with pipeline_rank_fn(embeddings, min_score=min_score) as (rank, corpus):
        return evaluate(load_json("rag_cases.json"), rank, corpus, thresholds)


def print_report(report: Report, *, label: str) -> None:
    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} [{result.kind}] {result.case_id}: {result.detail}")
    recall = "  ".join(f"recall@{k}={v:.3f}" for k, v in report.recall_at.items())
    print(
        f"\nRAG evaluation ({label}): {recall}  MRR={report.mrr:.3f}  "
        f"no-relevant-source={report.no_relevant_accuracy:.3f}"
    )
    if report.failures:
        print("FAILED: " + "; ".join(report.failures))
    else:
        print("All thresholds met.")


def main(argv: Sequence[str] | None = None) -> int:
    from app.core.config import Settings

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--embeddings", choices=("lexical", "hash", "provider"), default="lexical")
    parser.add_argument(
        "--min-score",
        type=float,
        default=Settings.model_fields["rag_min_relevance_score"].default,
        help="Relevance floor (normalized cosine); defaults to the production default.",
    )
    parser.add_argument("--min-recall-at-3", type=float, default=MIN_RECALL_AT_3)
    parser.add_argument("--min-mrr", type=float, default=MIN_MRR)
    parser.add_argument("--min-no-relevant-accuracy", type=float, default=MIN_NO_RELEVANT_ACCURACY)
    args = parser.parse_args(argv)
    thresholds = Thresholds(args.min_recall_at_3, args.min_mrr, args.min_no_relevant_accuracy)
    report = run(args.embeddings, min_score=args.min_score, thresholds=thresholds)
    print_report(report, label=f"{args.embeddings} embeddings, min_score={args.min_score}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
