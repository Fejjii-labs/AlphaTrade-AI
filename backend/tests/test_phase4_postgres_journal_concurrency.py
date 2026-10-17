"""Real PostgreSQL journal projector concurrency and conflict tests."""

from __future__ import annotations

import os
import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.errors import JournalProjectionConflictError
from app.core.persistence_firewall import install_persistence_firewall
from app.db.models import (
    ExecutionAccount,
    JournalLifecycleEvent,
    JournalTrade,
    Membership,
    Organization,
    User,
)
from app.schemas.common import (
    JournalLifecycleEventType,
    JournalReconciliationPosition,
    JournalTradeStatus,
    MembershipRole,
    TradeDirection,
    TradeResult,
)
from app.schemas.journal_lifecycle import JournalLifecycleEventInput
from app.schemas.trade_plan import AccountMode, ExecutionMode
from app.services.audit_service import AuditService
from app.services.journal_lifecycle_projector import JournalLifecycleProjector
from tests.support.postgres_template import refresh_database

POSTGRES_URL = os.environ.get(
    "PHASE1_POSTGRES_URL",
    os.environ.get(
        "AT028_POSTGRES_URL",
        "postgresql+psycopg://alphatrade:alphatrade@localhost:5432/alphatrade_test",
    ),
)

ORG = uuid.UUID("00000000-0000-0000-0000-00000000f001")
USER = uuid.UUID("00000000-0000-0000-0000-00000000f011")
ACCOUNT = uuid.UUID("00000000-0000-0000-0000-00000000f021")
ACCOUNT_B = uuid.UUID("00000000-0000-0000-0000-00000000f022")


def _postgres_available() -> bool:
    try:
        engine = create_engine(POSTGRES_URL, poolclass=NullPool)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason=f"PostgreSQL not reachable at {POSTGRES_URL}",
)


def _factory() -> sessionmaker[Session]:
    engine = refresh_database(POSTGRES_URL, kind="full")
    install_persistence_firewall()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all(
            [
                Organization(id=ORG, name="Journal PG Org"),
                User(id=USER, email="journal-pg@test.example", hashed_password="not-a-real-hash"),
            ]
        )
        session.flush()
        session.add_all(
            [
                Membership(organization_id=ORG, user_id=USER, role=MembershipRole.TRADER),
                ExecutionAccount(
                    id=ACCOUNT,
                    organization_id=ORG,
                    user_id=USER,
                    name="Paper A",
                    execution_mode=ExecutionMode.PAPER,
                    account_mode=AccountMode.NET,
                ),
                ExecutionAccount(
                    id=ACCOUNT_B,
                    organization_id=ORG,
                    user_id=USER,
                    name="Paper B",
                    execution_mode=ExecutionMode.PAPER,
                    account_mode=AccountMode.NET,
                ),
            ]
        )
        session.commit()
    return factory


def _event(
    event_type: JournalLifecycleEventType,
    *,
    source_event_id: str,
    execution_lifecycle_id: uuid.UUID,
    payload: dict[str, object],
    account_id: uuid.UUID = ACCOUNT,
    source_event_version: int = 1,
    supersession: int = 0,
) -> JournalLifecycleEventInput:
    return JournalLifecycleEventInput(
        event_type=event_type,
        execution_lifecycle_id=execution_lifecycle_id,
        source_system="paper_internal",
        source_aggregate="execution-lifecycle",
        source_event_id=source_event_id,
        source_event_version=source_event_version,
        supersession=supersession,
        account_id=account_id,
        payload=payload,
    )


def _payload(**extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "direction": TradeDirection.SHORT.value,
    }
    payload.update(extra)
    return payload


def _project(session: Session, event: JournalLifecycleEventInput) -> object:
    return JournalLifecycleProjector(session, AuditService(session, strict_mode=True)).project(
        event, organization_id=ORG, user_id=USER
    )


def _run_concurrent(
    factory: sessionmaker[Session],
    events: list[JournalLifecycleEventInput],
) -> tuple[list[object], list[BaseException]]:
    barrier = threading.Barrier(len(events))
    results: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(event: JournalLifecycleEventInput) -> None:
        with factory() as session:
            try:
                barrier.wait(timeout=20)
                result = _project(session, event)
                session.commit()
                with lock:
                    results.append(result)
            except BaseException as exc:
                session.rollback()
                with lock:
                    errors.append(exc)

    threads = [threading.Thread(target=worker, args=(event,)) for event in events]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)
    return results, errors


@requires_postgres
def test_simultaneous_approved_plan_and_fill() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    results, errors = _run_concurrent(
        factory,
        [
            _event(
                JournalLifecycleEventType.APPROVED_PLAN,
                source_event_id="plan-1",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(),
            ),
            _event(
                JournalLifecycleEventType.FILL,
                source_event_id="fill-1",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(entry_price="64000", size="0.1"),
            ),
        ],
    )
    assert errors == []
    assert len(results) == 2
    with factory() as session:
        trades = list(session.scalars(select(JournalTrade)).all())
        assert len(trades) == 1
        assert trades[0].status in {JournalTradeStatus.PLANNED, JournalTradeStatus.OPEN}
        assert trades[0].entry_price == Decimal("64000")
        assert session.scalar(select(func.count()).select_from(JournalLifecycleEvent)) == 2


@requires_postgres
def test_simultaneous_fill_and_close() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    results, errors = _run_concurrent(
        factory,
        [
            _event(
                JournalLifecycleEventType.FILL,
                source_event_id="fill-2",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(entry_price="64000"),
            ),
            _event(
                JournalLifecycleEventType.CLOSE,
                source_event_id="close-2",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(exit_price="63000", result=TradeResult.WIN.value),
            ),
        ],
    )
    assert errors == []
    with factory() as session:
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.status is JournalTradeStatus.CLOSED
        assert trade.entry_price == Decimal("64000")
        assert trade.exit_price == Decimal("63000")
        assert len(results) == 2


@requires_postgres
def test_simultaneous_two_fill_events() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    results, errors = _run_concurrent(
        factory,
        [
            _event(
                JournalLifecycleEventType.FILL,
                source_event_id="fill-a",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(entry_price="64000", size="0.1"),
            ),
            _event(
                JournalLifecycleEventType.FILL,
                source_event_id="fill-b",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(fees="1.25"),
            ),
        ],
    )
    assert errors == []
    with factory() as session:
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.entry_price == Decimal("64000")
        assert trade.fees == Decimal("1.25")
        assert session.scalar(select(func.count()).select_from(JournalLifecycleEvent)) == 2
        assert len(results) == 2


@requires_postgres
def test_simultaneous_close_and_reconcile() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    results, errors = _run_concurrent(
        factory,
        [
            _event(
                JournalLifecycleEventType.CLOSE,
                source_event_id="close-3",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(exit_price="63000"),
            ),
            _event(
                JournalLifecycleEventType.RECONCILE,
                source_event_id="recon-3",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(
                    fees="2.5",
                    net_pnl="100",
                    reconciliation_position=JournalReconciliationPosition.CLOSED.value,
                ),
            ),
        ],
    )
    assert errors == []
    with factory() as session:
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.status is JournalTradeStatus.CLOSED
        assert trade.exit_price == Decimal("63000")
        assert trade.fees == Decimal("2.5")
        assert len(results) == 2


@requires_postgres
def test_close_commits_before_stale_fill() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    close_done = threading.Event()
    errors: list[BaseException] = []

    def closer() -> None:
        with factory() as session:
            try:
                _project(
                    session,
                    _event(
                        JournalLifecycleEventType.CLOSE,
                        source_event_id="close-first",
                        execution_lifecycle_id=lifecycle_id,
                        payload=_payload(exit_price="63000"),
                    ),
                )
                session.commit()
            except BaseException as exc:
                session.rollback()
                errors.append(exc)
            finally:
                close_done.set()

    def stale_fill() -> None:
        close_done.wait(timeout=20)
        with factory() as session:
            try:
                _project(
                    session,
                    _event(
                        JournalLifecycleEventType.FILL,
                        source_event_id="stale-fill",
                        execution_lifecycle_id=lifecycle_id,
                        payload=_payload(entry_price="64000"),
                    ),
                )
                session.commit()
            except BaseException as exc:
                session.rollback()
                errors.append(exc)

    threads = [threading.Thread(target=closer), threading.Thread(target=stale_fill)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    with factory() as session:
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.status is JournalTradeStatus.CLOSED
        assert trade.entry_price == Decimal("64000")
        assert trade.exit_price == Decimal("63000")


@requires_postgres
def test_fill_commits_before_close() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    fill_done = threading.Event()
    errors: list[BaseException] = []

    def filler() -> None:
        with factory() as session:
            try:
                _project(
                    session,
                    _event(
                        JournalLifecycleEventType.FILL,
                        source_event_id="fill-first",
                        execution_lifecycle_id=lifecycle_id,
                        payload=_payload(entry_price="64000"),
                    ),
                )
                session.commit()
            except BaseException as exc:
                session.rollback()
                errors.append(exc)
            finally:
                fill_done.set()

    def closer() -> None:
        fill_done.wait(timeout=20)
        with factory() as session:
            try:
                _project(
                    session,
                    _event(
                        JournalLifecycleEventType.CLOSE,
                        source_event_id="close-after",
                        execution_lifecycle_id=lifecycle_id,
                        payload=_payload(exit_price="63000"),
                    ),
                )
                session.commit()
            except BaseException as exc:
                session.rollback()
                errors.append(exc)

    threads = [threading.Thread(target=filler), threading.Thread(target=closer)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    with factory() as session:
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.status is JournalTradeStatus.CLOSED
        assert trade.entry_price == Decimal("64000")
        assert trade.exit_price == Decimal("63000")


@requires_postgres
def test_multiple_projectors_from_empty_converge() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    event = _event(
        JournalLifecycleEventType.APPROVED_PLAN,
        source_event_id="plan-empty",
        execution_lifecycle_id=lifecycle_id,
        payload=_payload(),
    )
    results, errors = _run_concurrent(factory, [event, event, event])
    assert errors == []
    assert len(results) == 3
    with factory() as session:
        trades = list(session.scalars(select(JournalTrade)).all())
        assert len(trades) == 1
        assert session.scalar(select(func.count()).select_from(JournalLifecycleEvent)) == 1
        created = [item.created_journal_trade for item in results]  # type: ignore[union-attr]
        assert created.count(True) == 1


@requires_postgres
def test_same_event_replay_race() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    event = _event(
        JournalLifecycleEventType.FILL,
        source_event_id="fill-replay",
        execution_lifecycle_id=lifecycle_id,
        payload=_payload(entry_price="64000"),
    )
    results, errors = _run_concurrent(factory, [event, event])
    assert errors == []
    assert len(results) == 2
    replayed = [item.replayed for item in results]  # type: ignore[union-attr]
    assert replayed.count(False) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(JournalTrade)) == 1
        assert session.scalar(select(func.count()).select_from(JournalLifecycleEvent)) == 1


@requires_postgres
def test_conflicting_event_replay_race() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    first = _event(
        JournalLifecycleEventType.FILL,
        source_event_id="fill-conflict",
        execution_lifecycle_id=lifecycle_id,
        payload=_payload(entry_price="64000"),
    )
    conflict = _event(
        JournalLifecycleEventType.FILL,
        source_event_id="fill-conflict",
        execution_lifecycle_id=lifecycle_id,
        payload=_payload(entry_price="1"),
    )
    results, errors = _run_concurrent(factory, [first, conflict])
    assert any(isinstance(err, JournalProjectionConflictError) for err in errors)
    assert not any(isinstance(err, IntegrityError) for err in errors)
    with factory() as session:
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.entry_price in {Decimal("64000"), Decimal("1")}
        assert session.scalar(select(func.count()).select_from(JournalLifecycleEvent)) == 1
        assert len(results) + len(errors) == 2


@requires_postgres
def test_different_source_events_one_lifecycle() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    results, errors = _run_concurrent(
        factory,
        [
            _event(
                JournalLifecycleEventType.APPROVED_PLAN,
                source_event_id="plan-d",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(),
            ),
            _event(
                JournalLifecycleEventType.FILL,
                source_event_id="fill-d",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(entry_price="64000"),
            ),
            _event(
                JournalLifecycleEventType.CLOSE,
                source_event_id="close-d",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(exit_price="63000"),
            ),
        ],
    )
    assert errors == []
    with factory() as session:
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.status is JournalTradeStatus.CLOSED
        assert trade.entry_price == Decimal("64000")
        assert trade.exit_price == Decimal("63000")
        assert session.scalar(select(func.count()).select_from(JournalLifecycleEvent)) == 3
        assert len(results) == 3


@requires_postgres
def test_same_org_different_accounts_do_not_collide() -> None:
    factory = _factory()
    lifecycle_a = uuid.uuid4()
    lifecycle_b = uuid.uuid4()
    with factory() as session:
        first = _project(
            session,
            _event(
                JournalLifecycleEventType.APPROVED_PLAN,
                source_event_id="shared-source",
                execution_lifecycle_id=lifecycle_a,
                account_id=ACCOUNT,
                payload=_payload(),
            ),
        )
        second = _project(
            session,
            _event(
                JournalLifecycleEventType.APPROVED_PLAN,
                source_event_id="shared-source",
                execution_lifecycle_id=lifecycle_b,
                account_id=ACCOUNT_B,
                payload=_payload(),
            ),
        )
        session.commit()
        assert first.journal_trade_id != second.journal_trade_id  # type: ignore[union-attr]
        trades = list(session.scalars(select(JournalTrade)).all())
        assert len(trades) == 2
        assert {trade.account_id for trade in trades} == {ACCOUNT, ACCOUNT_B}
        assert session.scalar(select(func.count()).select_from(JournalLifecycleEvent)) == 2


def _project_two(
    factory: sessionmaker[Session],
    first: JournalLifecycleEventInput,
    second: JournalLifecycleEventInput,
) -> JournalTrade:
    with factory() as session:
        _project(session, first)
        _project(session, second)
        session.commit()
        return session.scalars(select(JournalTrade)).one()


@requires_postgres
def test_reconcile_position_open_then_stale_close() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    trade = _project_two(
        factory,
        _event(
            JournalLifecycleEventType.RECONCILE,
            source_event_id="recon-open-pg",
            execution_lifecycle_id=lifecycle_id,
            payload=_payload(
                reconciliation_position=JournalReconciliationPosition.POSITION_OPEN.value,
                entry_price="64000",
            ),
        ),
        _event(
            JournalLifecycleEventType.CLOSE,
            source_event_id="stale-close-open-pg",
            execution_lifecycle_id=lifecycle_id,
            payload=_payload(exit_price="63000"),
        ),
    )
    assert trade.status is JournalTradeStatus.OPEN
    assert trade.entry_price == Decimal("64000")
    assert trade.projector_watermark_rank == 40


@requires_postgres
def test_reconcile_closed_then_stale_close() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    trade = _project_two(
        factory,
        _event(
            JournalLifecycleEventType.RECONCILE,
            source_event_id="recon-closed-pg",
            execution_lifecycle_id=lifecycle_id,
            payload=_payload(
                reconciliation_position=JournalReconciliationPosition.CLOSED.value,
                exit_price="63100",
            ),
        ),
        _event(
            JournalLifecycleEventType.CLOSE,
            source_event_id="stale-close-closed-pg",
            execution_lifecycle_id=lifecycle_id,
            payload=_payload(exit_price="1"),
        ),
    )
    assert trade.status is JournalTradeStatus.CLOSED
    assert trade.exit_price == Decimal("63100")
    assert trade.projector_watermark_rank == 40


@requires_postgres
def test_close_then_newer_reconcile_position_open() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    trade = _project_two(
        factory,
        _event(
            JournalLifecycleEventType.CLOSE,
            source_event_id="close-then-open-pg",
            execution_lifecycle_id=lifecycle_id,
            payload=_payload(exit_price="63000"),
        ),
        _event(
            JournalLifecycleEventType.RECONCILE,
            source_event_id="recon-reopen-pg",
            execution_lifecycle_id=lifecycle_id,
            payload=_payload(
                reconciliation_position=JournalReconciliationPosition.POSITION_OPEN.value
            ),
        ),
    )
    assert trade.status is JournalTradeStatus.OPEN
    assert trade.projector_watermark_rank == 40


@requires_postgres
def test_close_then_newer_reconcile_closed() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    trade = _project_two(
        factory,
        _event(
            JournalLifecycleEventType.CLOSE,
            source_event_id="close-then-closed-pg",
            execution_lifecycle_id=lifecycle_id,
            payload=_payload(exit_price="63000"),
        ),
        _event(
            JournalLifecycleEventType.RECONCILE,
            source_event_id="recon-keep-closed-pg",
            execution_lifecycle_id=lifecycle_id,
            payload=_payload(
                reconciliation_position=JournalReconciliationPosition.CLOSED.value,
                fees="3.25",
            ),
        ),
    )
    assert trade.status is JournalTradeStatus.CLOSED
    assert trade.fees == Decimal("3.25")
    assert trade.projector_watermark_rank == 40


@requires_postgres
def test_concurrent_close_and_reconcile_position_open() -> None:
    factory = _factory()
    lifecycle_id = uuid.uuid4()
    results, errors = _run_concurrent(
        factory,
        [
            _event(
                JournalLifecycleEventType.CLOSE,
                source_event_id="close-conc-open",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(exit_price="63000"),
            ),
            _event(
                JournalLifecycleEventType.RECONCILE,
                source_event_id="recon-conc-open",
                execution_lifecycle_id=lifecycle_id,
                payload=_payload(
                    fees="2.5",
                    reconciliation_position=JournalReconciliationPosition.POSITION_OPEN.value,
                ),
            ),
        ],
    )
    assert errors == []
    with factory() as session:
        trade = session.scalars(select(JournalTrade)).one()
        assert trade.status is JournalTradeStatus.OPEN
        assert trade.fees == Decimal("2.5")
        assert trade.projector_watermark_rank == 40
        assert len(results) == 2
