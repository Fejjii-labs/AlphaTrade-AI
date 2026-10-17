"""Per-turn identity and session factory, never a live Session or connection."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy.orm import Session


@dataclass
class TurnContext:
    turn_id: UUID
    sessions: Callable[[], Session]
    call_number: int = 0
    embedding_number: int = 0
    node_scope: Callable[[str], AbstractContextManager[Any]] | None = None

    def next_task_id(self, purpose: str) -> UUID:
        self.call_number += 1
        return uuid5(self.turn_id, f"model:{purpose}:{self.call_number}")

    def next_embedding_id(self) -> UUID:
        self.embedding_number += 1
        return uuid5(self.turn_id, f"embedding:{self.embedding_number}")


_current: ContextVar[TurnContext | None] = ContextVar("agent_turn_context", default=None)


def current_turn() -> TurnContext | None:
    return _current.get()


@contextmanager
def turn_context(turn_id: UUID, sessions: Callable[[], Session]) -> Iterator[TurnContext]:
    context = TurnContext(turn_id, sessions)
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
