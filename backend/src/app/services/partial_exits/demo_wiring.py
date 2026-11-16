"""Glue between the governed BloFin demo loop and the partial-exit manager.

- ``demo_ladder_hold_reason``: the kill switch, the worker pause and the daily
  loss lock hold every close, in the same order entries are refused. An
  unreadable gate holds (fail closed).
- ``demo_entry_terms``: ladder terms from the stored plan and the actual
  entry fills (quantity-weighted entry price, filled size), or None when the
  plan is not a ladder.
- ``PositionExitDemoStore``: the durable ``position_exit_events`` records for
  one demo command (venue ``BLOFIN_DEMO``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.repositories.position_exit_events import PositionExitEventRepository
from app.schemas.common import TradeDirection
from app.schemas.trade_plan import EntrySide
from app.services.partial_exits.demo import DemoExitRecord
from app.services.partial_exits.flags import BLOFIN_DEMO_VENUE
from app.services.partial_exits.ladder import LadderRefusal
from app.services.partial_exits.paper_terms import (
    PartialExitTerms,
    is_ladder_payload,
    parse_partial_exit_terms,
)
from app.services.partial_exits.position import PositionExitState

# Held reasons that mean our own orders or fills cannot be explained. They
# need an operator, so the loop arms the existing kill switch.
OPERATOR_HOLD_REASONS = frozenset(
    {"unexplained_order", "unexplained_fill", "order_shape_unexpected"}
)


def demo_ladder_hold_reason(
    *,
    kill_blocked: Callable[[], bool],
    paused: Callable[[], bool],
    daily_locked: Callable[[], bool],
) -> str | None:
    try:
        if kill_blocked():
            return "kill_switch_active"
        if paused():
            return "worker_paused"
        if daily_locked():
            return "daily_loss_lock"
    except Exception:
        return "hold_state_unavailable"
    return None


def demo_entry_terms(plan: Any, fills: Sequence[Any]) -> PartialExitTerms | LadderRefusal | None:
    payload = plan.model_dump(mode="json")
    risk = payload.get("risk_and_exits") if isinstance(payload, dict) else None
    if not isinstance(risk, dict) or not is_ladder_payload(risk):
        return None
    quantity = sum((fill.quantity for fill in fills), Decimal("0"))
    if quantity <= 0:
        return LadderRefusal("position_size_unavailable")
    entry_price = sum((fill.quantity * fill.price for fill in fills), Decimal("0")) / quantity
    return parse_partial_exit_terms(
        payload,
        direction=TradeDirection.LONG if plan.side is EntrySide.BUY else TradeDirection.SHORT,
        entry_price=entry_price,
        position_size=quantity,
    )


class PositionExitDemoStore:
    def __init__(self, session: Session, *, organization_id: UUID, base: PositionExitState) -> None:
        self._repository = PositionExitEventRepository(session)
        self._organization_id = organization_id
        self._base = base

    def load(self, command_id: UUID) -> DemoExitRecord:
        fills, moves = self._repository.load(
            organization_id=self._organization_id,
            execution_lifecycle_id=command_id,
            venue=BLOFIN_DEMO_VENUE,
        )
        return DemoExitRecord(fills=fills, stop_moves=moves)

    def save(self, command_id: UUID, record: DemoExitRecord) -> None:
        self._repository.append(
            organization_id=self._organization_id,
            execution_lifecycle_id=command_id,
            venue=BLOFIN_DEMO_VENUE,
            base=self._base,
            fills=record.fills,
            stop_moves=record.stop_moves,
            evidence={},
        )
