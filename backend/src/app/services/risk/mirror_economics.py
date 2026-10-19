"""BloFin demo mirror economics stay out of risk, breakers, and promotion.

Venue P&L written by the hand-trade mirror — a new ``blofin_demo_manual`` row,
or any journal row whose ``external_ref`` starts with ``blofin-demo-`` — is a
human-channel record. It must not change open exposure, daily realized loss,
the daily lock, the paper loss cooldown, or promotion inputs. Promotion reads
``PaperTrade`` only; the mirror does not write that table.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, or_

from app.db.models import JournalTrade
from app.schemas.common import JournalTradeSource

MIRROR_REF_PREFIX = "blofin-demo-%"


def journal_mirror_economics() -> ColumnElement[bool]:
    """True when a journal row carries mirrored BloFin demo venue economics."""
    return or_(
        JournalTrade.source == JournalTradeSource.BLOFIN_DEMO_MANUAL,
        and_(
            JournalTrade.external_ref.is_not(None),
            JournalTrade.external_ref.like(MIRROR_REF_PREFIX),
        ),
    )
