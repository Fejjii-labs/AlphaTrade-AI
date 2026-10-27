"""Read-only view of what the autonomous paper bot did today (UI overhaul B3b)."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BotActionOutcome(StrEnum):
    ENTERED = "entered"
    BLOCKED = "blocked"
    REJECTED = "rejected"


class BotAction(BaseModel):
    """One recorded paper-loop decision. Only audited outcomes appear here."""

    model_config = ConfigDict(extra="forbid")

    at: datetime
    outcome: BotActionOutcome
    symbol: str | None = None
    direction: str | None = None
    reason: str | None = None
    journal_trade_id: UUID | None = None


class AutonomousActivityView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: date
    timezone: str
    # The count the bot cap is checked against (all paper trades today), or None
    # when it could not be computed.
    trades_today: int | None
    recent_actions: list[BotAction]
