"""Read and append partial-exit records for one position.

``append`` is idempotent per event key. Before anything is written it checks
that a replayed key carries identical content and that the recorded fills
plus the new ones still fold into a consistent position (no leg or position
overfill, no widening stop move). The caller holds the position lock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ValidationAppError
from app.db.position_exit_events import PositionExitEventRow
from app.services.partial_exits.position import (
    ExitFill,
    PositionExitState,
    StopMove,
    rebuild_position_state,
)


class PositionExitEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _rows(
        self, *, organization_id: UUID, execution_lifecycle_id: UUID, venue: str
    ) -> list[PositionExitEventRow]:
        return list(
            self._session.scalars(
                select(PositionExitEventRow)
                .where(
                    PositionExitEventRow.organization_id == organization_id,
                    PositionExitEventRow.execution_lifecycle_id == execution_lifecycle_id,
                    PositionExitEventRow.venue == venue,
                )
                .order_by(PositionExitEventRow.occurred_at, PositionExitEventRow.event_key)
            )
        )

    def load(
        self, *, organization_id: UUID, execution_lifecycle_id: UUID, venue: str
    ) -> tuple[tuple[ExitFill, ...], tuple[StopMove, ...]]:
        rows = self._rows(
            organization_id=organization_id,
            execution_lifecycle_id=execution_lifecycle_id,
            venue=venue,
        )
        fills = tuple(_fill(row) for row in rows if row.event_kind == "fill")
        moves = tuple(_move(row) for row in rows if row.event_kind == "stop_move")
        return fills, moves

    def append(
        self,
        *,
        organization_id: UUID,
        execution_lifecycle_id: UUID,
        venue: str,
        base: PositionExitState,
        fills: Iterable[ExitFill],
        stop_moves: Iterable[StopMove],
        evidence: Mapping[str, Mapping[str, str]],
    ) -> PositionExitState:
        """Write new records and return the state they fold into."""

        recorded_fills, recorded_moves = self.load(
            organization_id=organization_id,
            execution_lifecycle_id=execution_lifecycle_id,
            venue=venue,
        )
        known_fills = {fill.fill_id: fill for fill in recorded_fills}
        known_moves = {move.key: move for move in recorded_moves}
        new_fills: list[ExitFill] = []
        new_moves: list[StopMove] = []
        for fill in fills:
            known = known_fills.get(fill.fill_id)
            if known is None:
                new_fills.append(fill)
                known_fills[fill.fill_id] = fill
            elif known != fill:
                raise ConflictError(
                    "Partial-exit fill was recorded with different content.",
                    details={"reason": "position_exit_event_conflict", "key": fill.fill_id},
                )
        for move in stop_moves:
            known_move = known_moves.get(move.key)
            if known_move is None:
                new_moves.append(move)
                known_moves[move.key] = move
            elif known_move != move:
                raise ConflictError(
                    "Stop move was recorded with different content.",
                    details={"reason": "position_exit_event_conflict", "key": move.key},
                )
        try:
            state = rebuild_position_state(
                base, fills=known_fills.values(), stop_moves=known_moves.values()
            )
        except ValueError as exc:
            raise ValidationAppError(
                "Partial-exit records would not fold into a consistent position.",
                details={"reason": "position_exit_records_inconsistent", "detail": str(exc)},
            ) from exc
        for fill in new_fills:
            self._session.add(
                PositionExitEventRow(
                    organization_id=organization_id,
                    execution_lifecycle_id=execution_lifecycle_id,
                    venue=venue,
                    event_key=fill.fill_id,
                    event_kind="fill",
                    fill_kind=fill.kind,
                    leg_order=fill.leg_order,
                    quantity=fill.quantity,
                    price=fill.price,
                    occurred_at=fill.occurred_at,
                    evidence=dict(evidence.get(fill.fill_id, {})),
                )
            )
        for move in new_moves:
            self._session.add(
                PositionExitEventRow(
                    organization_id=organization_id,
                    execution_lifecycle_id=execution_lifecycle_id,
                    venue=venue,
                    event_key=move.key,
                    event_kind="stop_move",
                    stop_reason=move.reason,
                    previous_stop=move.previous_stop,
                    new_stop=move.new_stop,
                    tightened=move.tightened,
                    occurred_at=move.effective_at,
                    evidence=dict(evidence.get(move.key, {})),
                )
            )
        self._session.flush()
        return state


def _decimal(value: Decimal | None) -> Decimal:
    if value is None:
        raise ValueError("missing decimal in partial-exit record")
    # Numeric columns return a padded scale; Decimal equality is numeric, so
    # a replayed record still compares equal.
    return Decimal(value)


def _aware(row: PositionExitEventRow) -> datetime:
    moment = row.occurred_at
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _fill(row: PositionExitEventRow) -> ExitFill:
    return ExitFill(
        fill_id=row.event_key,
        kind=row.fill_kind,  # type: ignore[arg-type]
        leg_order=row.leg_order,
        quantity=_decimal(row.quantity),
        price=_decimal(row.price),
        occurred_at=_aware(row),
    )


def _move(row: PositionExitEventRow) -> StopMove:
    return StopMove(
        key=row.event_key,
        reason=row.stop_reason,  # type: ignore[arg-type]
        previous_stop=_decimal(row.previous_stop),
        new_stop=_decimal(row.new_stop),
        effective_at=_aware(row),
        tightened=bool(row.tightened),
    )
