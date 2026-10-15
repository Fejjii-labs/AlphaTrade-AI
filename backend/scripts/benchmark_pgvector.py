"""Disposable loopback experiment; never registered as a deployed vector backend.

Run with PGVECTOR_PROTOTYPE_URL pointing to the dedicated `agent3_vectors` DB.
The deterministic vectors measure mechanics and filtered recall, not semantics.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import resource
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import NAMESPACE_URL, UUID, uuid5

import numpy as np
import psycopg
from psycopg.types.json import Jsonb
from qdrant_client import QdrantClient
from sqlalchemy.engine import make_url

from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.providers.qdrant import (
    QdrantVectorStore,
    VectorPoint,
    VectorSearchFilters,
    VectorSearchHit,
    _matches_filters,
    _normalize_score,
)


class PgvectorPrototype:
    name = "pgvector-prototype"

    def __init__(self, dsn: str, dimensions: int):
        url = make_url(dsn)
        if url.host not in {"127.0.0.1", "localhost"} or url.database != "agent3_vectors":
            raise ValueError("Prototype requires the disposable loopback agent3_vectors DB.")
        self.connection = psycopg.connect(dsn, autocommit=True)
        self.connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self.connection.execute("DROP TABLE IF EXISTS prototype_points")
        self.connection.execute(
            f"CREATE TABLE prototype_points (id uuid PRIMARY KEY, document_id uuid "
            f"NOT NULL, organization_id uuid, user_id uuid, source_type text, "
            f"payload jsonb NOT NULL, embedding vector({dimensions}) NOT NULL)"
        )

    def upsert(self, collection: str, points: list[VectorPoint]) -> None:
        with self.connection.transaction(), self.connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO prototype_points VALUES (%s,%s,%s,%s,%s,%s,%s::vector) ON "
                "CONFLICT(id) DO UPDATE SET "
                "payload=excluded.payload,embedding=excluded.embedding",
                [
                    (
                        point.point_id,
                        point.payload["document_id"],
                        point.payload["organization_id"],
                        point.payload["user_id"],
                        point.payload["source_type"],
                        Jsonb(point.payload),
                        str(point.vector),
                    )
                    for point in points
                ],
            )

    def search(
        self, collection: str, vector: list[float], *, filters: VectorSearchFilters, top_k: int
    ) -> list[VectorSearchHit]:
        clauses, values = [], [str(vector)]
        if filters.organization_id is not None:
            clauses.append("organization_id=%s")
            values.append(str(filters.organization_id))
        if filters.user_id is not None:
            clauses.append(
                "(user_id=%s OR user_id IS NULL)" if filters.include_shared else "user_id=%s"
            )
            values.append(str(filters.user_id))
        if filters.source_types:
            clauses.append("source_type=ANY(%s)")
            values.append(list(filters.source_types))
        for field in ("strategy_tag", "symbol_tag", "timeframe_tag", "risk_tag"):
            value = getattr(filters, field)
            if value is not None:
                clauses.append(f"payload->>'{field}'=%s")
                values.append(value)
        where = " AND ".join(clauses) if clauses else "true"
        values.extend([str(vector), top_k])
        rows = self.connection.execute(
            f"SELECT id,1-(embedding <=> %s::vector),payload FROM prototype_points "
            f"WHERE {where} ORDER BY embedding <=> %s::vector LIMIT %s",
            values,
        ).fetchall()
        return [
            VectorSearchHit(str(row[0]), _normalize_score(float(row[1])), row[2]) for row in rows
        ]

    def document_points(
        self,
        collection: str,
        *,
        document_id: UUID,
        organization_id: UUID | None,
        user_id: UUID | None,
    ) -> set[str]:
        rows = self.connection.execute(
            "SELECT id FROM prototype_points WHERE document_id=%s AND "
            "organization_id IS NOT DISTINCT FROM %s::uuid AND user_id IS NOT "
            "DISTINCT FROM %s::uuid",
            (document_id, organization_id, user_id),
        ).fetchall()
        return {str(row[0]) for row in rows}

    def delete_document_points(
        self,
        collection: str,
        *,
        document_id: UUID,
        organization_id: UUID | None,
        user_id: UUID | None,
        keep_ids: set[str],
    ) -> None:
        self.connection.execute(
            "DELETE FROM prototype_points WHERE document_id=%s AND organization_id "
            "IS NOT DISTINCT FROM %s::uuid AND user_id IS NOT DISTINCT FROM "
            "%s::uuid AND NOT(id=ANY(%s::uuid[]))",
            (document_id, organization_id, user_id, list(keep_ids)),
        )

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name, kind=ProviderKind.VECTOR, health=ProviderHealth.HEALTHY
        )


def identity(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"synthetic-pgvector/{label}")


def measured(action):
    start = time.perf_counter()
    result = action()
    return result, (time.perf_counter() - start) * 1000


def run(dsn: str, *, count: int, dimensions: int, queries: int) -> dict[str, Any]:
    rng = np.random.default_rng(20261009)
    matrix = rng.random((count, dimensions), dtype=np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    points = [
        VectorPoint(
            str(identity(f"point-{i}")),
            matrix[i].tolist(),
            {
                "chunk_id": str(identity(f"point-{i}")),
                "document_id": str(identity(f"doc-{i // 4}")),
                "organization_id": str(identity(f"org-{(i // 4) % 4}")),
                "user_id": None if (i // 4) % 5 == 0 else str(identity(f"user-{(i // 4) % 8}")),
                "source_type": "risk_policy" if (i // 4) % 3 else "strategy_template",
                "strategy_tag": "fixture",
                "symbol_tag": "BTCUSDT",
                "timeframe_tag": "1h",
                "risk_tag": None,
            },
        )
        for i in range(count)
    ]
    filters = VectorSearchFilters(
        organization_id=identity("org-1"),
        user_id=identity("user-1"),
        include_shared=True,
        source_types=("risk_policy",),
        strategy_tag="fixture",
    )
    eligible = [i for i, point in enumerate(points) if _matches_filters(point.payload, filters)]
    local = QdrantClient(location=":memory:")
    with patch.object(QdrantVectorStore, "_connect"):
        qdrant = QdrantVectorStore(url="local-prototype", vector_size=dimensions, fail_closed=True)
    qdrant._client, qdrant._using_qdrant = local, True
    pg = PgvectorPrototype(dsn, dimensions)
    report: dict[str, Any] = {
        "synthetic": True,
        "seed": 20261009,
        "count": count,
        "dimensions": dimensions,
        "documents": len({point.payload["document_id"] for point in points}),
        "queries": queries,
        "filtered_candidates": len(eligible),
        "postgres_version": pg.connection.execute("SELECT version()").fetchone()[0],
        "pgvector_version": pg.connection.execute(
            "SELECT extversion FROM pg_extension WHERE extname='vector'"
        ).fetchone()[0],
        "qdrant_mode": "QdrantClient local in-memory, exhaustive (not hosted Qdrant)",
        "results": {},
    }
    for store in (qdrant, pg):
        _, insert_ms = measured(lambda store=store: store.upsert("prototype", points))
        if store is pg:
            _, index_ms = measured(
                lambda: pg.connection.execute(
                    "CREATE INDEX prototype_hnsw ON prototype_points USING hnsw (embedding "
                    "vector_cosine_ops)"
                )
            )
            pg.connection.execute(
                "CREATE INDEX prototype_scope ON "
                "prototype_points(organization_id,user_id,source_type)"
            )
            pg.connection.execute("ANALYZE prototype_points")
        times, recalls = [], []
        for i in range(queries + 3):
            vector = matrix[(i * 71 + 1) % count]
            truth = {
                points[eligible[k]].point_id for k in np.argsort(matrix[eligible] @ vector)[-5:]
            }
            hits, latency = measured(
                lambda store=store, vector=vector: store.search(
                    "prototype", vector.tolist(), filters=filters, top_k=5
                )
            )
            assert all(_matches_filters(hit.payload, filters) for hit in hits)
            if i >= 3:
                times.append(latency)
                recalls.append(len(truth.intersection(hit.point_id for hit in hits)) / 5)
        report["results"][store.name] = {
            "upsert_ms": round(insert_ms, 2),
            "p50_ms": round(float(np.percentile(times, 50)), 3),
            "p95_ms": round(float(np.percentile(times, 95)), 3),
            "filtered_recall_at_5": round(float(np.mean(recalls)), 3),
            "scope_violations": 0,
        }
        scope = {
            "document_id": UUID(points[eligible[0]].payload["document_id"]),
            "organization_id": filters.organization_id,
            "user_id": filters.user_id,
        }
        before = store.document_points("prototype", **scope)
        assert before
        _, deletion_ms = measured(
            lambda store=store, scope=scope: store.delete_document_points(
                "prototype", **scope, keep_ids=set()
            )
        )
        assert not store.document_points("prototype", **scope)
        report["results"][store.name]["scoped_delete_ms"] = round(deletion_ms, 3)
    report["pg_hnsw_build_ms"] = round(index_ms, 2)
    report["pg_relation_bytes"] = pg.connection.execute(
        "SELECT pg_total_relation_size('prototype_points')"
    ).fetchone()[0]
    report["pg_filtered_plan"] = [
        row[0]
        for row in pg.connection.execute(
            "EXPLAIN SELECT id FROM prototype_points WHERE organization_id=%s AND "
            "(user_id=%s OR user_id IS NULL) AND source_type='risk_policy' ORDER BY "
            "embedding <=> %s::vector LIMIT 5",
            (str(filters.organization_id), str(filters.user_id), str(matrix[1].tolist())),
        )
    ]
    try:
        with pg.connection.transaction():
            pg.connection.execute("DELETE FROM prototype_points")
            raise RuntimeError("rollback injection")
    except RuntimeError:
        pass
    report["pg_filtered_plan"] = [
        re.sub(r"'\[[^']+\]'::vector", "'<synthetic query vector>'::vector", row)
        for row in report["pg_filtered_plan"]
    ]
    report["pg_delete_rollback_preserved_rows"] = pg.connection.execute(
        "SELECT count(*) FROM prototype_points"
    ).fetchone()[0] == count - len(before)
    report["python_peak_rss_kib_combined_clients"] = resource.getrusage(
        resource.RUSAGE_SELF
    ).ru_maxrss
    pg.connection.close()
    local.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=2000)
    parser.add_argument("--dimensions", type=int, default=1536)
    parser.add_argument("--queries", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        os.environ["PGVECTOR_PROTOTYPE_URL"],
        count=args.count,
        dimensions=args.dimensions,
        queries=args.queries,
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
