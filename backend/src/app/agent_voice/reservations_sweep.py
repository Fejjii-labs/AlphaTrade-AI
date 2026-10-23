"""Release expired GPT Live holds that never received a provider final receipt."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agent_voice.live_config import CLEANUP_SECONDS, CLOSE_SECONDS
from app.db.voice_realtime import VoiceSessionReservation

_CLOSE_MARGIN = timedelta(seconds=CLOSE_SECONDS + CLEANUP_SECONDS)


def _parse_expires_at(ledger: dict[str, Any]) -> datetime | None:
    raw = ledger.get("expires_at")
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _live_secondary_unresolved(data: dict[str, Any]) -> bool:
    return bool(
        data.get("tools") or data.get("reasoning_attempts") or data.get("embedding_attempts")
    )


def _eligible_for_live_sweep(row: VoiceSessionReservation, moment: datetime) -> bool:
    data = row.ledger
    live = data.get("live")
    if not live:
        return False
    if live.get("finalized"):
        return False
    if _live_secondary_unresolved(data):
        return False
    expires_at = _parse_expires_at(data)
    if expires_at is None:
        return False
    return moment >= expires_at + _CLOSE_MARGIN


def release_expired_voice_holds(
    session: Session,
    organization_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> int:
    """Drop quota holds for expired GPT Live rows without a final duration receipt.

    Scoped to one organization, row-locked, and flushed only so quota reads do
    not commit unrelated work on the caller session. Realtime rows and finalized
    Live receipts with unresolved secondary delegation are never changed here.
    """
    moment = now or datetime.now(UTC)
    candidate_ids = session.scalars(
        select(VoiceSessionReservation.id).where(
            VoiceSessionReservation.organization_id == organization_id,
            or_(
                VoiceSessionReservation.status != "closed",
                VoiceSessionReservation.accounted_cost.is_(None),
            ),
        )
    ).all()
    released = 0
    for row_id in candidate_ids:
        row = session.scalar(
            select(VoiceSessionReservation)
            .where(
                VoiceSessionReservation.id == row_id,
                VoiceSessionReservation.organization_id == organization_id,
            )
            .with_for_update()
        )
        if row is None:
            continue
        if row.accounted_cost is not None:
            continue
        if not _eligible_for_live_sweep(row, moment):
            continue
        live = row.ledger.get("live", {})
        if live.get("finalized"):
            continue
        row.status = "closed"
        row.accounted_cost = Decimal(0)
        row.updated_at = moment
        released += 1
    if released:
        session.flush()
    return released
