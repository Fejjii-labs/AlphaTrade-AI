"""BloFin demo mirror economics stay out of risk, breakers, and promotion.

Only rows the mirror itself wrote qualify: source ``blofin_demo_manual``, or
the ``blofin-reconciled`` tag the mirror adds when it closes an existing
manual row in place. A client-supplied ``external_ref`` prefix does not.
Promotion reads ``PaperTrade`` only; the mirror does not write that table.
Journal statistics classify by source, not by this predicate.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, String, cast, or_

from app.db.models import JournalTrade
from app.schemas.common import JournalTradeSource

MIRROR_REF_PREFIX = "blofin-demo-"
RECONCILED_TAG = "blofin-reconciled"


def journal_mirror_economics() -> ColumnElement[bool]:
    """True when the mirror wrote the row or stamped its reconciliation tag."""
    marker = cast(JournalTrade.tags, String).contains(f'"{RECONCILED_TAG}"')
    return or_(
        JournalTrade.source == JournalTradeSource.BLOFIN_DEMO_MANUAL,
        marker,
    )
