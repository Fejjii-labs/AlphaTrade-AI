"""The Journal trade detail lists the trade's recorded partial exits.

Take-profit fills and stop moves live in the append-only ``position_exit_events``
record. The Journal's final close averages all exits into one price, so the
detail view reads the legs from that record, oldest first, for the trade's own
execution lifecycle and organization only.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.position_exit_events import PositionExitEventRow
from app.repositories.position_exit_events import PositionExitEventRepository
from app.schemas.journal_trades import JournalTradeDetail
from app.services.journal_trade_service import JournalTradeService
from tests.test_partial_exit_persistence import (
    BREAKEVEN,
    COMMAND,
    ORG,
    OTHER_ORG,
    TP1,
    VENUE,
    _base,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PositionExitEventRow.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _record(session: Session, organization_id: UUID = ORG) -> None:
    PositionExitEventRepository(session).append(
        organization_id=organization_id,
        execution_lifecycle_id=COMMAND,
        venue=VENUE,
        base=_base(),
        fills=(TP1,),
        stop_moves=(BREAKEVEN,),
        evidence={},
    )


def _trade(lifecycle: UUID | None = COMMAND, organization_id: UUID = ORG):
    return SimpleNamespace(organization_id=organization_id, execution_lifecycle_id=lifecycle)


def test_detail_lists_tp_fill_and_stop_move_oldest_first(session: Session) -> None:
    _record(session)
    service = JournalTradeService(session, audit_service=None)  # type: ignore[arg-type]
    events = service._exit_events(_trade())
    assert [(e.kind, e.fill_kind, e.leg, e.stop_reason) for e in events] == [
        ("fill", "target", 1, None),
        ("stop_move", None, None, "breakeven"),
    ]
    fill, move = events
    assert (fill.quantity, fill.price) == (Decimal("0.500"), Decimal("110"))
    assert (move.previous_stop, move.new_stop) == (Decimal("95"), Decimal("100.5"))
    # The API model serializes them for the trade detail page.
    assert JournalTradeDetail.model_fields["exit_events"].default_factory is list


def test_trade_without_execution_lifecycle_has_no_exit_events(session: Session) -> None:
    _record(session)
    service = JournalTradeService(session, audit_service=None)  # type: ignore[arg-type]
    assert service._exit_events(_trade(lifecycle=None)) == []


def test_other_organizations_exits_are_never_shown(session: Session) -> None:
    _record(session, organization_id=OTHER_ORG)
    service = JournalTradeService(session, audit_service=None)  # type: ignore[arg-type]
    assert service._exit_events(_trade()) == []
