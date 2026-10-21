"""Overlapping paper closes record one journal close.

Two sessions can both observe an open fill. The row lock and the close
idempotency key leave one CLOSED trade and one close event. A labelled
monitor close stores ``exit_evidence.candle_venue`` on that journal event.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ValidationAppError
from app.db.models import (
    AuditLog,
    DailyRiskState,
    JournalLifecycleEvent,
    JournalTrade,
    KillSwitchState,
    PaperValidationAlert,
)
from app.db.risk_policy import RiskPolicyBreakerState
from app.schemas.common import (
    ActorType,
    AuditEventType,
    JournalLifecycleEventType,
    JournalTradeStatus,
    PaperAlertType,
    TradeDirection,
)
from app.schemas.execution_protocol import ClosePaperPlanRequest
from app.workers.paper_exit_monitor import read_database_now
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


def _loss_exit(direction: TradeDirection, entry: Decimal) -> Decimal:
    if direction is TradeDirection.SHORT:
        return entry + Decimal("50")
    return entry - Decimal("50")


def _win_exit(direction: TradeDirection, entry: Decimal) -> Decimal:
    if direction is TradeDirection.SHORT:
        return entry - Decimal("50")
    return entry + Decimal("50")


def _close_labeled(
    factory: sessionmaker[Session],
    envelope: object,
    command_id: object,
    *,
    exit_price: Decimal,
    origin: str,
    occurred_at: datetime | None = None,
) -> object:
    plan = envelope.plan  # type: ignore[attr-defined]
    request = ClosePaperPlanRequest(
        organization_id=plan.organization_id,
        user_id=plan.user_id,
        account_id=plan.account_id,
        revision_id=plan.revision_id,
        command_id=command_id,  # type: ignore[arg-type]
        exit_price=exit_price,
        fees=Decimal("0"),
        funding=Decimal("0"),
        slippage=Decimal("0"),
        exit_reason="stop_hit",
        occurred_at=occurred_at or EXECUTE_AT + timedelta(minutes=1),
        idempotency_key=f"paper-exit-monitor:{command_id}",
        exit_channel="PAPER_EXIT_MONITOR",
        exit_actor="paper_exit_monitor",
        exit_evidence={
            "exit_origin": origin,
            "calibration_sample": "excluded" if origin == "backfill" else "eligible",
            "level": "stop",
            "same_bar_assumption": "none",
            "candle_venue": "bybit",
        },
    )
    session = factory()
    try:
        service = canonical_execution_service(session, build_runtime(factory))
        result = service.close_canonical_paper_plan(request)
        session.commit()
        return result
    finally:
        session.close()


@requires_postgres
def test_losing_backfill_refuses_entries_without_moving_breakers() -> None:
    factory = _factory()
    envelope, command_id = _open_fill(factory, key="exit-backfill-loss")
    plan = envelope.plan  # type: ignore[attr-defined]
    with factory() as session:
        trade = session.scalar(
            select(JournalTrade).where(JournalTrade.execution_lifecycle_id == command_id)
        )
        assert trade is not None
        assert trade.entry_price is not None
        exit_price = _loss_exit(trade.direction, trade.entry_price)
    result = _close_labeled(
        factory,
        envelope,
        command_id,
        exit_price=exit_price,
        origin="backfill",
    )
    assert result.net_pnl < 0  # type: ignore[attr-defined]
    with factory() as session:
        event = session.scalar(
            select(JournalLifecycleEvent).where(
                JournalLifecycleEvent.execution_lifecycle_id == command_id,
                JournalLifecycleEvent.event_type == JournalLifecycleEventType.CLOSE,
            )
        )
        assert event is not None
        evidence = event.payload.get("exit_evidence")
        assert isinstance(evidence, dict)
        assert evidence.get("candle_venue") == "bybit"
    with factory() as session:
        switch = session.scalar(
            select(KillSwitchState).where(KillSwitchState.organization_id == plan.organization_id)
        )
        assert switch is not None
        assert switch.active is True
        assert switch.activated_by is None
        assert "not folded into the live streak" in (switch.reason or "")
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.organization_id == plan.organization_id,
                AuditLog.action == AuditEventType.KILL_SWITCH_ACTIVATED,
            )
        )
        assert audit is not None
        assert audit.actor_type is ActorType.SYSTEM
        assert audit.user_id is None
        assert audit.actor == "system"
        alert = session.scalar(
            select(PaperValidationAlert).where(
                PaperValidationAlert.organization_id == plan.organization_id,
                PaperValidationAlert.alert_type == PaperAlertType.PAPER_EXIT_BACKFILL_LOSS,
            )
        )
        assert alert is not None
        assert alert.severity.value == "critical"
        metadata = alert.metadata_json or {}
        assert metadata.get("net_loss") == "true"
        assert metadata.get("refusal") == "kill_switch"
        assert metadata.get("exit_origin") == "backfill"
        breaker = session.scalar(
            select(RiskPolicyBreakerState).where(
                RiskPolicyBreakerState.organization_id == plan.organization_id,
                RiskPolicyBreakerState.account_id == plan.account_id,
            )
        )
        assert breaker is None
        locked = session.scalars(
            select(DailyRiskState).where(
                DailyRiskState.organization_id == plan.organization_id,
                DailyRiskState.locked.is_(True),
            )
        ).all()
        assert locked == []


@requires_postgres
def test_winning_backfill_does_not_refuse_or_move_breakers() -> None:
    factory = _factory()
    envelope, command_id = _open_fill(factory, key="exit-backfill-win")
    plan = envelope.plan  # type: ignore[attr-defined]
    with factory() as session:
        trade = session.scalar(
            select(JournalTrade).where(JournalTrade.execution_lifecycle_id == command_id)
        )
        assert trade is not None
        assert trade.entry_price is not None
        exit_price = _win_exit(trade.direction, trade.entry_price)
    result = _close_labeled(
        factory,
        envelope,
        command_id,
        exit_price=exit_price,
        origin="backfill",
    )
    assert result.net_pnl > 0  # type: ignore[attr-defined]
    with factory() as session:
        switch = session.scalar(
            select(KillSwitchState).where(KillSwitchState.organization_id == plan.organization_id)
        )
        assert switch is None or switch.active is False
        alert = session.scalar(
            select(PaperValidationAlert).where(
                PaperValidationAlert.organization_id == plan.organization_id,
                PaperValidationAlert.alert_type == PaperAlertType.PAPER_EXIT_BACKFILL_LOSS,
            )
        )
        assert alert is None
        breaker = session.scalar(
            select(RiskPolicyBreakerState).where(
                RiskPolicyBreakerState.organization_id == plan.organization_id
            )
        )
        assert breaker is None


def _assert_breakers_untouched(
    session: Session, organization_id: object, account_id: object
) -> None:
    breaker = session.scalar(
        select(RiskPolicyBreakerState).where(
            RiskPolicyBreakerState.organization_id == organization_id,
            RiskPolicyBreakerState.account_id == account_id,
        )
    )
    assert breaker is None
    locked = session.scalars(
        select(DailyRiskState).where(
            DailyRiskState.organization_id == organization_id,
            DailyRiskState.locked.is_(True),
        )
    ).all()
    assert locked == []


@requires_postgres
def test_six_hour_live_loss_arms_the_kill_switch_without_moving_breakers() -> None:
    """A loss about 6h old is labelled live and still gets working protection."""

    factory = _factory()
    envelope, command_id = _open_fill(factory, key="exit-stale-live-loss")
    plan = envelope.plan  # type: ignore[attr-defined]
    with factory() as session:
        trusted = read_database_now(session)
        trade = session.scalar(
            select(JournalTrade).where(JournalTrade.execution_lifecycle_id == command_id)
        )
        assert trusted is not None
        assert trade is not None
        assert trade.entry_price is not None
        exit_price = _loss_exit(trade.direction, trade.entry_price)
    occurred = trusted - timedelta(hours=6)
    result = _close_labeled(
        factory,
        envelope,
        command_id,
        exit_price=exit_price,
        origin="live",
        occurred_at=occurred,
    )
    assert result.net_pnl < 0  # type: ignore[attr-defined]
    with factory() as session:
        trade = session.scalar(
            select(JournalTrade).where(JournalTrade.execution_lifecycle_id == command_id)
        )
        assert trade is not None
        assert trade.exit_time is not None
        assert abs(trade.exit_time - occurred) < timedelta(seconds=1)
        switch = session.scalar(
            select(KillSwitchState).where(KillSwitchState.organization_id == plan.organization_id)
        )
        assert switch is not None
        assert switch.active is True
        assert switch.activated_by is None
        assert "too old" in (switch.reason or "")
        alert = session.scalar(
            select(PaperValidationAlert).where(
                PaperValidationAlert.organization_id == plan.organization_id,
                PaperValidationAlert.alert_type == PaperAlertType.PAPER_EXIT_STALE_LOSS,
            )
        )
        assert alert is not None
        metadata = alert.metadata_json or {}
        assert metadata.get("refusal") == "kill_switch"
        assert metadata.get("exit_origin") == "live"
        assert "net_pnl" not in metadata
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.organization_id == plan.organization_id,
                AuditLog.action == AuditEventType.KILL_SWITCH_ACTIVATED,
            )
        )
        assert audit is not None
        assert audit.actor_type is ActorType.SYSTEM
        assert audit.user_id is None
        _assert_breakers_untouched(session, plan.organization_id, plan.account_id)


@requires_postgres
def test_recent_live_loss_does_not_arm_the_kill_switch() -> None:
    """A loss inside the pause and inside one candle stays on the live path."""

    factory = _factory()
    envelope, command_id = _open_fill(factory, key="exit-recent-live-loss")
    plan = envelope.plan  # type: ignore[attr-defined]
    with factory() as session:
        trusted = read_database_now(session)
        trade = session.scalar(
            select(JournalTrade).where(JournalTrade.execution_lifecycle_id == command_id)
        )
        assert trusted is not None
        assert trade is not None
        assert trade.entry_price is not None
        exit_price = _loss_exit(trade.direction, trade.entry_price)
    occurred = trusted - timedelta(seconds=30)
    result = _close_labeled(
        factory,
        envelope,
        command_id,
        exit_price=exit_price,
        origin="live",
        occurred_at=occurred,
    )
    assert result.net_pnl < 0  # type: ignore[attr-defined]
    with factory() as session:
        switch = session.scalar(
            select(KillSwitchState).where(KillSwitchState.organization_id == plan.organization_id)
        )
        assert switch is None or switch.active is False
        alert = session.scalar(
            select(PaperValidationAlert).where(
                PaperValidationAlert.organization_id == plan.organization_id,
                PaperValidationAlert.alert_type == PaperAlertType.PAPER_EXIT_STALE_LOSS,
            )
        )
        assert alert is None
        backfill = session.scalar(
            select(PaperValidationAlert).where(
                PaperValidationAlert.organization_id == plan.organization_id,
                PaperValidationAlert.alert_type == PaperAlertType.PAPER_EXIT_BACKFILL_LOSS,
            )
        )
        assert backfill is None
        _assert_breakers_untouched(session, plan.organization_id, plan.account_id)


@requires_postgres
def test_database_now_is_an_aware_trusted_clock() -> None:
    factory = _factory()
    with factory() as session:
        trusted = read_database_now(session)
    assert trusted is not None
    assert trusted.tzinfo is not None
    assert abs(datetime.now(UTC) - trusted) < timedelta(seconds=15)
