"""Structured citation verification: cited chunk ids must come from retrieval."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

CITATION_NOT_IN_RETRIEVED_SET = "citation_not_in_retrieved_set"


@dataclass(frozen=True)
class CitationCheck:
    valid: tuple[str, ...]
    unknown: tuple[str, ...]

    @property
    def grounded(self) -> bool:
        """At least one cited id, and every cited id was retrieved this turn."""
        return bool(self.valid) and not self.unknown


def verify_cited_chunk_ids(cited: Iterable[object], retrieved: Iterable[object]) -> CitationCheck:
    """Split cited ids into retrieved (kept, order preserved, deduped) and unknown."""
    allowed = {str(item).strip().lower() for item in retrieved}
    valid: list[str] = []
    unknown: list[str] = []
    for item in cited:
        key = str(item).strip().lower()
        bucket = valid if key in allowed else unknown
        if key not in bucket:
            bucket.append(key)
    return CitationCheck(valid=tuple(valid), unknown=tuple(unknown))
