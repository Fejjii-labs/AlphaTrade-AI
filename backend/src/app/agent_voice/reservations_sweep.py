"""Release expired voice holds that never received a provider final receipt."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.voice_realtime import VoiceSessionReservation


def release_expired_voice_holds(session: Session, *, now: datetime | None = None) -> int:
    """Close stale reservations and zero their accounted cost so quota holds drop.

    A GPT Live row with a finalized duration receipt is left to ``LiveLedger.close``
    so unresolved secondary delegation can keep its conservative hold. Realtime rows
    without a finalized Live block are released once ``expires_at`` has passed and
    no final receipt was recorded.
    """
    moment = now or datetime.now(UTC)
    rows = session.scalars(
        select(VoiceSessionReservation).where(
            or_(
                VoiceSessionReservation.status != "closed",
                VoiceSessionReservation.accounted_cost.is_(None),
            )
        )
    ).all()
    released = 0
    for row in rows:
        expires_raw = row.ledger.get("expires_at")
        if not expires_raw:
            continue
        expires_at = datetime.fromisoformat(expires_raw)
        if expires_at > moment:
            continue
        live = row.ledger.get("live")
        if live and live.get("finalized"):
            continue
        if row.status == "closed" and row.accounted_cost is not None:
            continue
        row.status = "closed"
        row.accounted_cost = Decimal(0)
        row.updated_at = moment
        released += 1
    if released:
        session.commit()
    return released
