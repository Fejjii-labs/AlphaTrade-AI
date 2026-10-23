"""Release expired GPT Live holds that never received a provider final receipt."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid5

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agent_voice.live_config import CLEANUP_SECONDS, CLOSE_SECONDS
from app.db.models import UsageEvent
from app.db.voice_realtime import VoiceSessionReservation
from app.schemas.common import CostSource, UsageStatus

_CLOSE_MARGIN = timedelta(seconds=CLOSE_SECONDS + CLEANUP_SECONDS)
_EXPIRED_HOLD_CHARGE = "expired-voice-hold"


def expired_hold_charge_id(reservation_id: uuid.UUID) -> uuid.UUID:
    """Stable usage-event id for the conservative charge of one swept hold."""

    return uuid5(reservation_id, _EXPIRED_HOLD_CHARGE)


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


def _charge_full_reservation(
    session: Session,
    row: VoiceSessionReservation,
    moment: datetime,
) -> None:
    """Count the whole reservation as spend. A missing receipt is never $0.

    A crash before the provider duration receipt must not free the $1 org
    ceiling. The charge is one usage event for ``reserved_cost``. A later final
    duration receipt deletes that event and records the measured seconds instead.
    """

    charge = row.reserved_cost
    event_id = expired_hold_charge_id(row.id)
    usage = session.get(UsageEvent, event_id)
    if usage is None:
        session.add(
            UsageEvent(
                id=event_id,
                organization_id=row.organization_id,
                user_id=row.user_id,
                request_id=str(row.id),
                feature="agent_voice_expired_hold",
                provider="openai",
                model=row.model,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                estimated_cost=charge,
                cost_source=CostSource.STATIC_ESTIMATED,
                cost_is_placeholder=False,
                status=UsageStatus.SUCCESS,
                event_at=moment,
            )
        )
    elif usage.estimated_cost < charge:
        usage.estimated_cost = charge
        usage.event_at = moment
    row.status = "closed"
    row.accounted_cost = charge
    row.updated_at = moment


def release_expired_voice_holds(
    session: Session,
    organization_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> int:
    """Charge expired GPT Live rows that never received a final duration receipt.

    Scoped to one organization, row-locked, and flushed only so quota reads do
    not commit unrelated work on the caller session. Each released row is charged
    its full ``reserved_cost`` (never zero). Realtime rows and finalized Live
    receipts with unresolved secondary delegation are never changed here.
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
        _charge_full_reservation(session, row, moment)
        released += 1
    if released:
        session.flush()
    return released
