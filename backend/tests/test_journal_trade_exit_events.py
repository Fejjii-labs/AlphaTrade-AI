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


@pytest.fixture(autouse=True)
def _no_plan_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    # SQLite here has only the exit-event table; the plan lookup is tested below.
    monkeypatch.setattr(JournalTradeService, "_coins_per_contract", lambda self, row: Decimal("1"))


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


def test_blofin_demo_exits_are_shown_in_coins_not_contracts(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit M-15: on BTC one contract is 0.001 BTC; TP1 of 500 contracts is 0.5 BTC."""

    _record(session)
    monkeypatch.setattr(
        JournalTradeService, "_coins_per_contract", lambda self, row: Decimal("0.001")
    )
    service = JournalTradeService(session, audit_service=None)  # type: ignore[arg-type]
    fill, move = service._exit_events(_trade())
    assert fill.quantity == Decimal("0.500") * Decimal("0.001")
    assert move.quantity is None


def test_coins_per_contract_reads_only_blofin_demo_contract_plans() -> None:
    from app.services.journal_trade_service import coins_per_contract

    def plan(venue: str, unit: str, multiplier: object) -> SimpleNamespace:
        return SimpleNamespace(
            execution_venue=venue,
            semantic_payload={
                "quantity": {"value": "1087", "unit": unit},
                "instrument_rules": {"contract_multiplier": multiplier},
            },
        )

    assert coins_per_contract(plan("BLOFIN_DEMO", "CONTRACTS", "0.001")) == Decimal("0.001")
    assert coins_per_contract(plan("BLOFIN_DEMO", "BASE", "0.001")) == 1
    assert coins_per_contract(plan("PAPER_INTERNAL", "CONTRACTS", "0.001")) == 1
    assert coins_per_contract(plan("BLOFIN_DEMO", "CONTRACTS", "bad")) == 1
    assert coins_per_contract(plan("BLOFIN_DEMO", "CONTRACTS", "0")) == 1
    assert coins_per_contract(None) == 1
