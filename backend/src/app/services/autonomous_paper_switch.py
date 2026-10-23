"""Organization paper switch. Env ``AUTONOMOUS_INTERNAL_PAPER_ENABLED`` is the ceiling.

A missing row is off. An unreadable row is unverified. Neither value can turn
on real-money trading. Demo mode does not consult this switch.
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.autonomous_paper_switch import AutonomousPaperSwitchRow
from app.services.autonomous_trading import (
    AutonomousMode,
    AutonomousModeResolution,
    resolve_autonomous_mode,
)


def read_internal_paper_switch(
    session: Session,
    organization_id: uuid.UUID,
) -> bool | None:
    """True when the owner switch is on, False when it is off or absent.

    None means the row could not be read. Callers fail closed.
    """

    try:
        row = session.get(AutonomousPaperSwitchRow, organization_id)
    except Exception:
        # A failed statement aborts the Postgres transaction. Roll it back so a
        # later read on this session is not stuck in that aborted state.
        with suppress(Exception):
            session.rollback()
        return None
    if row is None:
        return False
    return bool(row.enabled)


def write_internal_paper_switch(
    session: Session,
    *,
    organization_id: uuid.UUID,
    enabled: bool,
    user_id: uuid.UUID,
) -> bool:
    """Persist the owner switch. ``enabled`` must already be a bool."""

    if not isinstance(enabled, bool):
        raise TypeError("internal_paper_enabled")
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

    now = datetime.now(UTC)
    stmt = dialect_insert(AutonomousPaperSwitchRow).values(
        organization_id=organization_id,
        enabled=enabled,
        updated_by_user_id=user_id,
        created_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["organization_id"],
        set_={
            "enabled": enabled,
            "updated_by_user_id": user_id,
            "updated_at": now,
        },
    )
    session.execute(stmt)
    session.flush()
    return enabled


def resolve_worker_autonomous_mode(
    settings: object,
    session: Session | None,
    organization_id: uuid.UUID | None,
) -> AutonomousModeResolution:
    """Env ceiling AND the organization switch.

    Real-money and contradictory env flags are refused before the switch is
    read. The switch is consulted only when env would otherwise arm internal
    paper. Env false never reaches the switch, so it always wins.
    """

    preliminary = resolve_autonomous_mode(settings)
    if preliminary.refusal is not None or preliminary.mode is not AutonomousMode.INTERNAL_PAPER:
        return preliminary
    if session is None or organization_id is None:
        return AutonomousModeResolution(AutonomousMode.OFF, "autonomous_paper_toggle_unverified")
    enabled = read_internal_paper_switch(session, organization_id)
    if enabled is None:
        return AutonomousModeResolution(AutonomousMode.OFF, "autonomous_paper_toggle_unverified")
    if enabled is False:
        return AutonomousModeResolution(AutonomousMode.OFF, None)
    return preliminary


def autonomy_mode_token(resolution: AutonomousModeResolution) -> str:
    if resolution.refusal:
        token = resolution.refusal
        if not token.replace("_", "").isalnum() or token != token.lower():
            token = "unverified"
        return f"refused:{token[:40]}"
    return resolution.mode.value
