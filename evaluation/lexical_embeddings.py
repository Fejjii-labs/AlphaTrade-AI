"""Deterministic lexical baseline embeddings for the offline RAG evaluation.

Feature-hashed, sublinear term-frequency vectors over unigrams and adjacent
bigrams, L2-normalized. This is a *lexical* baseline: it lets the offline eval
exercise the real retrieval pipeline (ingest -> index -> filter -> rank ->
relevance floor -> metrics) with a signal that can rank documents. It says
nothing about OpenAI semantic quality; use ``--embeddings provider`` for that.

Not used by the application. No network, no cost.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from itertools import pairwise

from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.providers.embeddings import EmbeddingResult

LEXICAL_MODEL = "lexical-baseline-v1"
LEXICAL_DIMENSIONS = 2048

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "before",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "should",
        "the",
        "this",
        "to",
        "what",
        "when",
        "where",
        "which",
        "with",
        "you",
        "your",
        "after",
        "any",
        "every",
        "into",
        "only",
        "than",
        "then",
        "there",
        "these",
        "they",
        "was",
        "were",
        "will",
    ]
)


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def lexical_terms(text: str) -> list[str]:
    tokens = [_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]
    return tokens + [f"{a}_{b}" for a, b in pairwise(tokens)]


def _bucket(term: str, dimensions: int) -> int:
    return int.from_bytes(hashlib.sha256(term.encode()).digest()[:4], "big") % dimensions


def lexical_vector(text: str, *, dimensions: int = LEXICAL_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    for term, count in Counter(lexical_terms(text)).items():
        vector[_bucket(term, dimensions)] += 1.0 + math.log(count)
    norm = math.sqrt(sum(v * v for v in vector))
    # An all-stopword text gets a zero vector: cosine 0 to everything.
    return [v / norm for v in vector] if norm else vector


class LexicalEmbeddingsProvider:
    """EmbeddingsProvider-compatible lexical baseline (evaluation only)."""

    name = "lexical-baseline"
    kind = ProviderKind.EMBEDDINGS

    def __init__(self, *, dimensions: int = LEXICAL_DIMENSIONS) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.embed_with_metadata(texts).vectors

    def embed_with_metadata(self, texts: list[str]) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[lexical_vector(text, dimensions=self._dimensions) for text in texts],
            model=LEXICAL_MODEL,
            provider=self.name,
            input_tokens=max(sum(len(t) for t in texts) // 4, len(texts)),
            latency_ms=0.0,
            fallback_used=False,
            dimensions=self._dimensions,
        )

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            kind=self.kind,
            health=ProviderHealth.HEALTHY,
            is_mock=True,
            using_fallback=False,
            detail="Lexical baseline embeddings for the offline RAG evaluation.",
        )
