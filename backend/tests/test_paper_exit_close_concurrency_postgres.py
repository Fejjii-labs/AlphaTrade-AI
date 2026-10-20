"""Overlapping paper closes record one journal close.

Two sessions can both observe an open fill. The row lock and the close
idempotency key leave one CLOSED trade and one close event.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ValidationAppError
from app.db.models import JournalLifecycleEvent, JournalTrade
from app.schemas.common import JournalLifecycleEventType, JournalTradeStatus
from app.schemas.execution_protocol import ClosePaperPlanRequest
from tests.support.phase8_runtime import (
    EXECUTE_AT,
    build_runtime,
    canonical_execution_service,
    prepared_authorized_canonical,
)
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres
from tests.test_phase8_canonical_paper_execution import _execute


def _factory() -> sessionmaker[Session]:
    return phase7_plan_session_factory()


def _request(envelope: object, command_id: object, *, key: str) -> ClosePaperPlanRequest:
    plan = envelope.plan  # type: ignore[attr-defined]
    return ClosePaperPlanRequest(
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        account_id=plan.account_id,
        revision_id=plan.revision_id,
        command_id=command_id,  # type: ignore[arg-type]
        exit_price=Decimal("99990"),
        fees=Decimal("0.5"),
        funding=Decimal("0"),
        slippage=Decimal("0"),
        exit_reason="stop_hit",
        occurred_at=EXECUTE_AT + timedelta(minutes=1),
        idempotency_key=key,
    )


def _open_fill(factory: sessionmaker[Session], *, key: str) -> tuple[object, object]:
    _world, envelope, authorization = prepared_authorized_canonical(factory)
    result, session, service, _runtime = _execute(factory, envelope, authorization, key=key)
    try:
        service.apply_paper_plan_fill(
            command_id=result.command_id,
            fill_quantity=Decimal("1"),
            fill_price=Decimal("100000"),
            source_identity=f"{key}-fill",
            occurred_at=EXECUTE_AT,
        )
        session.commit()
        return envelope, result.command_id
    finally:
        session.close()


def _close_together(
    factory: sessionmaker[Session],
    envelope: object,
    command_id: object,
    keys: tuple[str, str],
) -> tuple[list[object], list[BaseException]]:
    barrier = threading.Barrier(2)
    results: list[object] = []
    errors: list[BaseException] = []
    guard = threading.Lock()

    def worker(key: str) -> None:
        session = factory()
        try:
            service = canonical_execution_service(session, build_runtime(factory))
            barrier.wait(timeout=20)
            result = service.close_canonical_paper_plan(_request(envelope, command_id, key=key))
            session.commit()
            with guard:
                results.append(result)
        except BaseException as exc:
            session.rollback()
            with guard:
                errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(key,)) for key in keys]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results, errors


def _close_count(factory: sessionmaker[Session], command_id: object) -> int:
    with factory() as session:
        trade = session.scalar(
            select(JournalTrade).where(JournalTrade.execution_lifecycle_id == command_id)
        )
        assert trade is not None
        assert trade.status is JournalTradeStatus.CLOSED
        assert trade.net_pnl == Decimal("9.5")
        return int(
            session.scalar(
                select(func.count())
                .select_from(JournalLifecycleEvent)
                .where(
                    JournalLifecycleEvent.execution_lifecycle_id == command_id,
                    JournalLifecycleEvent.event_type == JournalLifecycleEventType.CLOSE,
                )
            )
            or 0
        )


@requires_postgres
def test_overlapping_closes_with_one_key_replay() -> None:
    factory = _factory()
    envelope, command_id = _open_fill(factory, key="exit-race-same")
    key = f"paper-exit-monitor:{command_id}"
    results, errors = _close_together(factory, envelope, command_id, (key, key))
    assert errors == []
    assert len(results) == 2
    assert sorted(bool(getattr(item, "replayed", False)) for item in results) == [False, True]
    assert _close_count(factory, command_id) == 1


@requires_postgres
def test_overlapping_closes_with_different_keys_close_once() -> None:
    factory = _factory()
    envelope, command_id = _open_fill(factory, key="exit-race-diff")
    results, errors = _close_together(
        factory,
        envelope,
        command_id,
        ("paper-exit-monitor:left", "paper-exit-monitor:right"),
    )
    assert len(results) == 1
    assert getattr(results[0], "replayed", None) is False
    assert len(errors) == 1
    assert isinstance(errors[0], ValidationAppError)
    assert errors[0].details.get("reason") == "already_closed"
    assert _close_count(factory, command_id) == 1
