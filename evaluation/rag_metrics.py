"""Ranking metrics for the RAG evaluation (pure functions, no app imports)."""

from __future__ import annotations

from collections.abc import Sequence


def recall_at_k(ranked: Sequence[str], expected: Sequence[str], k: int) -> float:
    """Fraction of ``expected`` keys that appear in the first ``k`` ranked keys."""
    if k < 1:
        raise ValueError("k must be >= 1")
    if not expected:
        raise ValueError("recall needs at least one expected key")
    top = set(ranked[:k])
    return sum(1 for key in set(expected) if key in top) / len(set(expected))


def reciprocal_rank(ranked: Sequence[str], expected: Sequence[str]) -> float:
    """1 / rank of the first expected key in ``ranked`` (0.0 when absent)."""
    if not expected:
        raise ValueError("reciprocal rank needs at least one expected key")
    wanted = set(expected)
    for index, key in enumerate(ranked, start=1):
        if key in wanted:
            return 1.0 / index
    return 0.0


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
