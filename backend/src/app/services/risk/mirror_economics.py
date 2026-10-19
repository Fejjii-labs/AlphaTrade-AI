"""BloFin demo mirror economics stay out of risk, breakers, and promotion.

Only server-written identity qualifies: source ``blofin_demo_manual``, or an
``external_ref`` the mirror itself set to the exact prefix
``blofin-demo-history:``. Client tags are never consulted. A case or
whitespace variant of that prefix does not qualify. Promotion reads
``PaperTrade`` only; the mirror does not write that table. Journal
statistics classify by source, not by this predicate.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, func, or_

from app.db.models import JournalTrade
from app.schemas.common import JournalTradeSource

MIRROR_REF_PREFIX = "blofin-demo-"
HISTORY_REF_PREFIX = "blofin-demo-history:"
RECONCILED_TAG = "blofin-reconciled"


def exact_prefix(column: Any, prefix: str) -> ColumnElement[bool]:
    """Case-sensitive prefix match. ``LIKE`` is case-insensitive on SQLite.

    A null ref coalesces to an empty string so ``NOT`` this predicate stays true.
    """
    return func.coalesce(func.substr(column, 1, len(prefix)), "") == prefix


def journal_mirror_economics() -> ColumnElement[bool]:
    """True when the mirror wrote the row or its history ref."""
    return or_(
        JournalTrade.source == JournalTradeSource.BLOFIN_DEMO_MANUAL,
        exact_prefix(JournalTrade.external_ref, HISTORY_REF_PREFIX),
    )


def client_mirror_ref(value: object) -> bool:
    """True when a client ref is a case or whitespace variant of the mirror prefix."""
    if not isinstance(value, str):
        return False
    normalized = "".join(value.split()).casefold()
    return normalized.startswith(MIRROR_REF_PREFIX)


def client_reserved_tag(value: object) -> bool:
    """True for a double quote, or a tag that starts with ``blofin-``."""
    if not isinstance(value, str):
        return False
    if '"' in value:
        return True
    return value.strip().casefold().startswith("blofin-")
