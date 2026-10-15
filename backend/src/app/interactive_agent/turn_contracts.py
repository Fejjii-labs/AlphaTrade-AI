"""Additive OpenAPI contract for durable recovery through compatible HTTP adapters."""

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import StrictModel


class TurnConflictDetails(StrictModel):
    reason: Literal[
        "turn_key_conflict",
        "conversation_turn_in_progress",
        "turn_running",
        "turn_capture",
        "turn_failed",
        "turn_interrupted",
        "turn_stale",
        "turn_stale_snapshot",
    ]
    turn_id: UUID
    conversation_id: UUID


class TurnConflictError(StrictModel):
    code: str
    message: str
    request_id: str | None = None
    # Retain other existing domain-conflict details; recovery reasons have a
    # concrete branch while unrelated conflict envelopes remain compatible.
    details: TurnConflictDetails | dict[str, Any] | None = Field(
        default=None, union_mode="left_to_right"
    )


class TurnConflictResponse(StrictModel):
    error: TurnConflictError


TURN_CONFLICT_RESPONSES: dict[int | str, dict[str, Any]] = {
    409: {
        "model": TurnConflictResponse,
        "description": (
            "Durable turn recovery or domain conflict. Same-key recovery never repeats model I/O."
        ),
    }
}
