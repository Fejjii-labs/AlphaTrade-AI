"""Durable partial-exit records, the reversible migration, and the journal close.

Conditions 2 and 3 at the storage layer: a fill or stop move is written once
per key, a replay converges, a conflicting replay and an overfill are refused
before anything is written, and the records rebuild the same state after a
restart. Condition 6: the final close projects per-fill fees and PnL that sum
exactly to the journal totals. Condition 8: the hold gate reads the kill
switch and the worker pause, and fails closed.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.errors import ConflictError, ValidationAppError
from app.db.position_exit_events import PositionExitEventRow
from app.repositories.position_exit_events import PositionExitEventRepository
from app.schemas.common import TradeDirection
from app.schemas.execution_protocol import ClosePaperExitFill, ClosePaperPlanRequest
from app.services.canonical_paper_execution import close_economics_payload
from app.services.partial_exits.ladder import (
    ExitLadder,
    InstrumentLimits,
    LadderTarget,
    build_exit_ladder,
)
from app.services.partial_exits.position import (
    ExitFill,
    PositionExitState,
    StopMove,
    open_position_state,
    rebuild_position_state,
)
from app.workers.paper_exit_monitor import partial_hold_reason

ORG = UUID("10000000-0000-4000-8000-0000000000a1")
OTHER_ORG = UUID("10000000-0000-4000-8000-0000000000a2")
COMMAND = UUID("50000000-0000-4000-8000-0000000000a5")
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
VENUE = "PAPER_INTERNAL"
MIGRATIONS = Path(__file__).parents[1] / "src/app/db/migrations"


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    PositionExitEventRow.__table__.create(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _base() -> PositionExitState:
    ladder = build_exit_ladder(
        direction=TradeDirection.LONG,
        entry_price=Decimal("100"),
        position_quantity=Decimal("1.000"),
        targets=(
            LadderTarget(order=1, price=Decimal("110"), fraction=Decimal("0.5")),
            LadderTarget(order=2, price=Decimal("120"), fraction=Decimal("0.5")),
        ),
        runner_fraction=Decimal("0"),
        limits=InstrumentLimits(
            lot_size=Decimal("0.001"),
            minimum_quantity=Decimal("0.001"),
            minimum_notional=Decimal("0"),
        ),
    )
    assert isinstance(ladder, ExitLadder)
    return open_position_state(
        ladder=ladder,
        direction=TradeDirection.LONG,
        entry_price=Decimal("100"),
        initial_stop=Decimal("95"),
        tick_size=Decimal("0.1"),
        cost_per_unit=Decimal("0.5"),
    )


TP1 = ExitFill(
    fill_id="paper-partial-exit:x:leg:1",
    kind="target",
    leg_order=1,
    quantity=Decimal("0.500"),
    price=Decimal("110"),
    occurred_at=T0,
)
BREAKEVEN = StopMove(
    key="breakeven",
    reason="breakeven",
    previous_stop=Decimal("95"),
    new_stop=Decimal("100.5"),
    effective_at=T0,
    tightened=True,
)


def _append(
    repository: PositionExitEventRepository,
    *,
    fills: tuple[ExitFill, ...] = (TP1,),
    moves: tuple[StopMove, ...] = (BREAKEVEN,),
    organization_id: UUID = ORG,
    venue: str = VENUE,
) -> PositionExitState:
    return repository.append(
        organization_id=organization_id,
        execution_lifecycle_id=COMMAND,
        venue=venue,
        base=_base(),
        fills=fills,
        stop_moves=moves,
        evidence={TP1.fill_id: {"bar_interval_end": T0.isoformat()}},
    )


def test_records_round_trip_and_rebuild_the_same_state(session: Session) -> None:
    repository = PositionExitEventRepository(session)
    state = _append(repository)
    fills, moves = repository.load(organization_id=ORG, execution_lifecycle_id=COMMAND, venue=VENUE)
    assert fills == (TP1,)
    assert moves == (BREAKEVEN,)
    restarted = rebuild_position_state(_base(), fills=fills, stop_moves=moves)
    assert restarted == state
    assert restarted.current_stop == Decimal("100.5")


def test_replaying_the_same_records_writes_nothing(session: Session) -> None:
    repository = PositionExitEventRepository(session)
    _append(repository)
    _append(repository)
    assert session.query(PositionExitEventRow).count() == 2


def test_conflicting_replay_is_refused_before_any_write(session: Session) -> None:
    repository = PositionExitEventRepository(session)
    _append(repository)
    conflicting = ExitFill(
        fill_id=TP1.fill_id,
        kind="target",
        leg_order=1,
        quantity=Decimal("0.500"),
        price=Decimal("111"),
        occurred_at=T0,
    )
    with pytest.raises(ConflictError):
        _append(repository, fills=(conflicting,), moves=())
    assert session.query(PositionExitEventRow).count() == 2


def test_overfill_is_refused_and_nothing_is_written(session: Session) -> None:
    repository = PositionExitEventRepository(session)
    _append(repository)
    stop = ExitFill(
        fill_id="paper-partial-exit:x:stop",
        kind="stop",
        leg_order=None,
        quantity=Decimal("0.600"),
        price=Decimal("100.5"),
        occurred_at=T0 + timedelta(minutes=15),
    )
    with pytest.raises(ValidationAppError) as raised:
        _append(repository, fills=(stop,), moves=())
    assert raised.value.details["reason"] == "position_exit_records_inconsistent"
    assert session.query(PositionExitEventRow).count() == 2


def test_records_are_scoped_by_tenant_and_venue(session: Session) -> None:
    repository = PositionExitEventRepository(session)
    _append(repository)
    for organization_id, venue in ((OTHER_ORG, VENUE), (ORG, "BLOFIN_DEMO")):
        assert repository.load(
            organization_id=organization_id, execution_lifecycle_id=COMMAND, venue=venue
        ) == ((), ())


# Migration: one reversible revision on top of a29datapolicy001.


def _offline_sql(direction: str, revisions: str, monkeypatch: pytest.MonkeyPatch) -> str:
    from alembic import command
    from alembic.config import Config

    monkeypatch.setenv("ALEMBIC_DATABASE_URL", "postgresql+psycopg://offline@localhost/offline")
    buffer = io.StringIO()
    config = Config(output_buffer=buffer)
    config.set_main_option("script_location", str(MIGRATIONS))
    getattr(command, direction)(config, revisions, sql=True)
    return buffer.getvalue()


def test_migration_is_a_single_reversible_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    revision = ScriptDirectory.from_config(config).get_revision("a29partialexit001")
    assert revision is not None
    assert revision.down_revision == "a29datapolicy001"
    upgrade = _offline_sql("upgrade", "a29datapolicy001:a29partialexit001", monkeypatch)
    assert "CREATE TABLE position_exit_events" in upgrade
    assert "uq_position_exit_events_key" in upgrade
    downgrade = _offline_sql("downgrade", "a29partialexit001:a29datapolicy001", monkeypatch)
    assert "DROP TABLE position_exit_events" in downgrade


def test_migration_columns_match_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    upgrade = _offline_sql("upgrade", "a29datapolicy001:a29partialexit001", monkeypatch)
    for column in PositionExitEventRow.__table__.columns:
        assert f"\t{column.name} " in upgrade or f" {column.name} " in upgrade, column.name


# Journal close economics (condition 6).


def _request(fills: tuple[ClosePaperExitFill, ...], **overrides: object) -> ClosePaperPlanRequest:
    values: dict[str, object] = {
        "organization_id": ORG,
        "user_id": uuid4(),
        "account_id": uuid4(),
        "revision_id": uuid4(),
        "command_id": COMMAND,
        "exit_price": Decimal("114.6"),
        "fees": Decimal("0.7"),
        "funding": Decimal("0"),
        "slippage": Decimal("0"),
        "exit_reason": "stop_hit",
        "occurred_at": T0,
        "idempotency_key": f"paper-exit-monitor:{COMMAND}",
        "exit_fills": fills,
    }
    values.update(overrides)
    return ClosePaperPlanRequest(**values)  # type: ignore[arg-type]


FILLS = (
    ClosePaperExitFill(
        fill_id="a",
        kind="target",
        leg_order=1,
        quantity=Decimal("0.5"),
        price=Decimal("110"),
        occurred_at=T0,
        fees=Decimal("0.35"),
    ),
    ClosePaperExitFill(
        fill_id="b",
        kind="target",
        leg_order=2,
        quantity=Decimal("0.3"),
        price=Decimal("120"),
        occurred_at=T0,
        fees=Decimal("0.21"),
    ),
    ClosePaperExitFill(
        fill_id="c",
        kind="runner_stop",
        quantity=Decimal("0.2"),
        price=Decimal("118"),
        occurred_at=T0,
        fees=Decimal("0.14"),
    ),
)


def test_journal_close_payload_reconciles_every_fill_to_the_totals() -> None:
    economics, payload = close_economics_payload(
        _request(FILLS),
        direction=TradeDirection.LONG,
        entry_price=Decimal("100"),
        size=Decimal("1.000"),
    )
    rows = payload["exit_fills"]
    assert isinstance(rows, list) and len(rows) == 3
    assert sum(Decimal(row["gross_pnl"]) for row in rows) == economics.gross_pnl
    assert sum(Decimal(row["net_pnl"]) for row in rows) == economics.net_pnl
    assert sum(Decimal(row["fees"]) for row in rows) == Decimal("0.7")
    assert economics.gross_pnl == Decimal("14.6")
    assert economics.net_pnl == Decimal("13.9")
    assert payload["exit_price_basis"] == "quantity_weighted_average"


def test_single_close_payload_is_unchanged() -> None:
    economics, payload = close_economics_payload(
        _request((), exit_fills=None, exit_price=Decimal("110"), fees=Decimal("0.7")),
        direction=TradeDirection.LONG,
        entry_price=Decimal("100"),
        size=Decimal("1.000"),
    )
    assert economics.gross_pnl == Decimal("10")
    assert "exit_fills" not in payload


@pytest.mark.parametrize(
    "overrides",
    [
        {"fees": Decimal("0.8")},
        {"exit_fills": FILLS[:2]},
        {"exit_fills": (*FILLS, FILLS[0])},
        {"exit_fills": ()},
    ],
)
def test_close_refuses_fills_that_do_not_reconcile(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationAppError):
        close_economics_payload(
            _request(FILLS, **overrides),
            direction=TradeDirection.LONG,
            entry_price=Decimal("100"),
            size=Decimal("1.000"),
        )


# Kill switch and pause (condition 8).


class _KillSwitch:
    def __init__(self, blocked: bool | Exception) -> None:
        self.blocked = blocked

    def is_execution_blocked(self, *, organization_id: UUID) -> bool:
        if isinstance(self.blocked, Exception):
            raise self.blocked
        return self.blocked


def test_hold_reason_reads_kill_switch_then_pause_and_fails_closed() -> None:
    def paused(value: bool | Exception) -> object:
        def read() -> bool:
            if isinstance(value, Exception):
                raise value
            return value

        return read

    assert partial_hold_reason(ORG, kill_switch=_KillSwitch(False), paused=paused(False)) is None
    assert (
        partial_hold_reason(ORG, kill_switch=_KillSwitch(True), paused=paused(False))
        == "kill_switch_active"
    )
    assert (
        partial_hold_reason(ORG, kill_switch=_KillSwitch(False), paused=paused(True))
        == "worker_paused"
    )
    assert (
        partial_hold_reason(ORG, kill_switch=_KillSwitch(RuntimeError()), paused=paused(False))
        == "hold_state_unavailable"
    )
    assert (
        partial_hold_reason(ORG, kill_switch=_KillSwitch(False), paused=paused(RuntimeError()))
        == "hold_state_unavailable"
    )


# Worker recorder: one transaction for records and the final close.


@pytest.fixture
def full_db() -> sessionmaker[Session]:
    from app.db.base import Base

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _open_journal(factory: sessionmaker[Session], status: str = "open") -> None:
    from app.db.models import JournalTrade
    from app.schemas.common import JournalTradeSource, JournalTradeStatus
    from tests.test_paper_exit_monitor import ENTRY, JOURNAL
    from tests.test_paper_exit_monitor import ORG as MONITOR_ORG
    from tests.test_paper_exit_monitor import USER as MONITOR_USER

    with factory() as session:
        session.add(
            JournalTrade(
                id=JOURNAL,
                organization_id=MONITOR_ORG,
                user_id=MONITOR_USER,
                source=JournalTradeSource.PAPER_EXECUTION,
                status=JournalTradeStatus(status),
                symbol="BTCUSDT",
                timeframe="15m",
                direction=TradeDirection.LONG,
                entry_time=ENTRY,
            )
        )
        session.commit()


def _settings() -> object:
    from app.core.config import Settings

    return Settings(_env_file=None, paper_partial_exits_enabled=True)  # type: ignore[call-arg]


def _event_count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return session.query(PositionExitEventRow).count()


def test_recorder_writes_a_reduction_once_and_leaves_the_trade_open(
    full_db: sessionmaker[Session],
) -> None:
    from app.workers.paper_exit_monitor import DatabasePartialExitRecorder
    from tests.test_partial_exit_paper_monitor import _bars, _decide, _ladder_trade

    _open_journal(full_db)
    closes: list[ClosePaperPlanRequest] = []
    recorder = DatabasePartialExitRecorder(
        full_db,
        _settings(),
        close=lambda _s, request: closes.append(request),  # type: ignore[arg-type]
    )
    trade = _ladder_trade()
    step = _decide(trade, _bars(("101", "110")))
    recorder.record(trade, step)
    recorder.record(trade, step)
    assert _event_count(full_db) == 2  # TP1 fill + breakeven move, written once
    fills, moves = recorder.load(trade)
    assert [fill.leg_order for fill in fills] == [1]
    assert [move.key for move in moves] == ["breakeven"]
    assert closes == []


def test_recorder_closes_with_every_fill_in_the_same_transaction(
    full_db: sessionmaker[Session],
) -> None:
    from app.workers.paper_exit_monitor import DatabasePartialExitRecorder
    from tests.test_partial_exit_paper_monitor import _bars, _decide, _ladder_trade

    _open_journal(full_db)
    closes: list[ClosePaperPlanRequest] = []
    recorder = DatabasePartialExitRecorder(
        full_db,
        _settings(),
        close=lambda _s, request: closes.append(request),  # type: ignore[arg-type]
    )
    trade = _ladder_trade()
    step = _decide(trade, _bars(("101", "121"), ("118", "125"), ("122", "128"), ("117", "126")))
    recorder.record(trade, step)
    assert len(closes) == 1
    assert closes[0].exit_fills is not None
    assert sum(fill.quantity for fill in closes[0].exit_fills) == Decimal("1.000")


def test_failed_close_rolls_back_the_records(full_db: sessionmaker[Session]) -> None:
    from app.workers.paper_exit_monitor import DatabasePartialExitRecorder
    from tests.test_partial_exit_paper_monitor import _bars, _decide, _ladder_trade

    _open_journal(full_db)

    def failing(_session: Session, _request: ClosePaperPlanRequest) -> object:
        raise RuntimeError("close failed")

    recorder = DatabasePartialExitRecorder(full_db, _settings(), close=failing)  # type: ignore[arg-type]
    trade = _ladder_trade()
    step = _decide(trade, _bars(("101", "121"), ("118", "125"), ("122", "128"), ("117", "126")))
    with pytest.raises(RuntimeError):
        recorder.record(trade, step)
    assert _event_count(full_db) == 0


def test_closed_journal_row_refuses_so_a_ladder_cannot_close_twice(
    full_db: sessionmaker[Session],
) -> None:
    from app.workers.paper_exit_monitor import DatabasePartialExitRecorder
    from tests.test_partial_exit_paper_monitor import _bars, _decide, _ladder_trade

    _open_journal(full_db, status="closed")
    recorder = DatabasePartialExitRecorder(full_db, _settings())  # type: ignore[arg-type]
    trade = _ladder_trade()
    with pytest.raises(ValidationAppError) as raised:
        recorder.record(trade, _decide(trade, _bars(("101", "110"))))
    assert raised.value.details["reason"] == "already_closed"
    assert _event_count(full_db) == 0
