"""Overlapping paper closes record one journal close.

Two sessions can both observe an open fill. The row lock and the close
idempotency key leave one CLOSED trade and one close event. A labelled
monitor close stores ``exit_evidence.candle_venue`` on that journal event.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
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
    AuditResult,
    JournalLifecycleEventType,
    JournalTradeStatus,
    PaperAlertType,
    TradeDirection,
)
from app.schemas.execution_protocol import ClosePaperPlanRequest
from app.services import canonical_paper_execution
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


def _arm_on_backfill_loss(monkeypatch: pytest.MonkeyPatch, arm: bool) -> None:
    monkeypatch.setenv("PAPER_EXIT_KILL_SWITCH_ON_BACKFILL_LOSS", "true" if arm else "false")


def _assert_not_armed(session: Session, organization_id: object) -> None:
    switch = session.scalar(
        select(KillSwitchState).where(KillSwitchState.organization_id == organization_id)
    )
    assert switch is None or switch.active is False
    audit = session.scalar(
        select(AuditLog).where(
            AuditLog.organization_id == organization_id,
            AuditLog.action == AuditEventType.KILL_SWITCH_ACTIVATED,
        )
    )
    assert audit is None


def _assert_armed(session: Session, organization_id: object, reason_part: str) -> None:
    switch = session.scalar(
        select(KillSwitchState).where(KillSwitchState.organization_id == organization_id)
    )
    assert switch is not None
    assert switch.active is True
    assert switch.activated_by is None
    assert reason_part in (switch.reason or "")
    audit = session.scalar(
        select(AuditLog).where(
            AuditLog.organization_id == organization_id,
            AuditLog.action == AuditEventType.KILL_SWITCH_ACTIVATED,
        )
    )
    assert audit is not None
    assert audit.actor_type is ActorType.SYSTEM
    assert audit.user_id is None


def _assert_alert_audit(session: Session, organization_id: object, *, blocked: bool) -> None:
    """The alert audit row says BLOCKED only when the kill switch was armed."""

    rows = session.scalars(
        select(AuditLog).where(
            AuditLog.organization_id == organization_id,
            AuditLog.resource_type == "paper_exit_monitor",
        )
    ).all()
    assert len(rows) == 1
    if blocked:
        assert rows[0].action == AuditEventType.RISK_BLOCK
        assert rows[0].result == AuditResult.BLOCKED
    else:
        assert rows[0].action == AuditEventType.RISK_WARNING
        assert rows[0].result == AuditResult.WARNING


@requires_postgres
@pytest.mark.parametrize("arm", [False, True], ids=["default_alert_only", "opt_in_arms"])
def test_losing_backfill_alerts_and_arms_only_when_configured(
    monkeypatch: pytest.MonkeyPatch, arm: bool
) -> None:
    """A loss closed a day or more ago is a true backfill: arming is opt-in."""

    _arm_on_backfill_loss(monkeypatch, arm)
    factory = _factory()
    envelope, command_id = _open_fill(factory, key=f"exit-backfill-loss-{arm}")
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
        if arm:
            switch = session.scalar(
                select(KillSwitchState).where(
                    KillSwitchState.organization_id == plan.organization_id
                )
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
        else:
            _assert_not_armed(session, plan.organization_id)
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
        assert metadata.get("refusal") == ("kill_switch" if arm else "none")
        assert metadata.get("exit_origin") == "backfill"
        _assert_alert_audit(session, plan.organization_id, blocked=arm)
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


def _losing_close(
    factory: sessionmaker[Session],
    key: str,
    *,
    origin: str,
    age: timedelta | None,
) -> tuple[object, object, datetime | None]:
    """Close one losing trade ``age`` before database now(). None keeps the fixture bar."""

    envelope, command_id = _open_fill(factory, key=key)
    with factory() as session:
        trusted = read_database_now(session)
        trade = session.scalar(
            select(JournalTrade).where(JournalTrade.execution_lifecycle_id == command_id)
        )
        assert trusted is not None
        assert trade is not None
        assert trade.entry_price is not None
        exit_price = _loss_exit(trade.direction, trade.entry_price)
    occurred = None if age is None else trusted - age
    result = _close_labeled(
        factory,
        envelope,
        command_id,
        exit_price=exit_price,
        origin=origin,
        occurred_at=occurred,
    )
    assert result.net_pnl < 0  # type: ignore[attr-defined]
    return envelope.plan, command_id, occurred  # type: ignore[attr-defined]


def _stale_alert_refusal(session: Session, organization_id: object) -> str | None:
    alert = session.scalar(
        select(PaperValidationAlert).where(
            PaperValidationAlert.organization_id == organization_id,
            PaperValidationAlert.alert_type == PaperAlertType.PAPER_EXIT_STALE_LOSS,
        )
    )
    assert alert is not None
    metadata = alert.metadata_json or {}
    assert metadata.get("exit_origin") == "live"
    assert "net_pnl" not in metadata
    return metadata.get("refusal")


@requires_postgres
@pytest.mark.parametrize("arm", [False, True], ids=["flag_off", "flag_on"])
def test_six_hour_live_loss_always_arms_the_kill_switch(
    monkeypatch: pytest.MonkeyPatch, arm: bool
) -> None:
    """A loss about 6h late is current risk, not a backfill: it arms whatever the flag."""

    _arm_on_backfill_loss(monkeypatch, arm)
    factory = _factory()
    plan, command_id, occurred = _losing_close(
        factory, f"exit-stale-live-loss-{arm}", origin="live", age=timedelta(hours=6)
    )
    assert occurred is not None
    with factory() as session:
        trade = session.scalar(
            select(JournalTrade).where(JournalTrade.execution_lifecycle_id == command_id)
        )
        assert trade is not None
        assert trade.exit_time is not None
        assert abs(trade.exit_time - occurred) < timedelta(seconds=1)
        _assert_armed(session, plan.organization_id, "too old")
        assert _stale_alert_refusal(session, plan.organization_id) == "kill_switch"
        _assert_alert_audit(session, plan.organization_id, blocked=True)


@requires_postgres
def test_late_loss_under_a_day_arms_even_with_a_backfill_label() -> None:
    """The age on the database clock decides, not the label: 2h late still arms."""

    factory = _factory()
    plan, _command_id, _occurred = _losing_close(
        factory, "exit-backfill-label-2h", origin="backfill", age=timedelta(hours=2)
    )
    with factory() as session:
        _assert_armed(session, plan.organization_id, "historical backfill")
        _assert_alert_audit(session, plan.organization_id, blocked=True)
        _assert_breakers_untouched(session, plan.organization_id, plan.account_id)


@requires_postgres
@pytest.mark.parametrize("arm", [False, True], ids=["default_alert_only", "opt_in_arms"])
def test_live_labelled_loss_a_day_old_is_a_true_backfill(
    monkeypatch: pytest.MonkeyPatch, arm: bool
) -> None:
    """The eb76b033 shape: labelled live, closed 25h ago. Alert only by default."""

    _arm_on_backfill_loss(monkeypatch, arm)
    noted: list[datetime] = []
    monkeypatch.setattr(
        canonical_paper_execution,
        "_note_profile_close",
        lambda *_a, **kw: noted.append(kw["now"]) or (1, False),
    )
    monkeypatch.setattr(
        canonical_paper_execution, "profile_close_should_note", lambda *_a, **_k: True
    )
    factory = _factory()
    plan, _command_id, _occurred = _losing_close(
        factory, f"exit-live-25h-{arm}", origin="live", age=timedelta(hours=25)
    )
    assert noted == []
    with factory() as session:
        if arm:
            _assert_armed(session, plan.organization_id, "too old")
        else:
            _assert_not_armed(session, plan.organization_id)
        refusal = _stale_alert_refusal(session, plan.organization_id)
        assert refusal == ("kill_switch" if arm else "none")
        _assert_alert_audit(session, plan.organization_id, blocked=arm)
        _assert_breakers_untouched(session, plan.organization_id, plan.account_id)


@requires_postgres
def test_stale_live_loss_feeds_streak_and_daily_lock_at_close_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 2h-late profile loss is noted at database now(), not at the old bar."""

    noted: list[datetime] = []
    monkeypatch.setattr(
        canonical_paper_execution,
        "_note_profile_close",
        lambda *_a, **kw: noted.append(kw["now"]) or (1, False),
    )
    # The Postgres plan fixture is cross-margin. Treat it as a profile close.
    monkeypatch.setattr(
        canonical_paper_execution, "profile_close_should_note", lambda *_a, **_k: True
    )
    factory = _factory()
    plan, _command_id, occurred = _losing_close(
        factory, "exit-stale-live-note", origin="live", age=timedelta(hours=2)
    )
    assert occurred is not None
    assert len(noted) == 1
    assert noted[0] - occurred >= timedelta(hours=2)
    with factory() as session:
        trusted = read_database_now(session)
        assert trusted is not None
        assert abs(trusted - noted[0]) < timedelta(minutes=1)
        _assert_armed(session, plan.organization_id, "noted on the live streak")


@requires_postgres
def test_missing_database_clock_fails_closed_and_arms(monkeypatch: pytest.MonkeyPatch) -> None:
    """No database clock: a day-old backfill cannot be proven old, so it arms."""

    _arm_on_backfill_loss(monkeypatch, False)
    noted: list[datetime] = []
    monkeypatch.setattr(canonical_paper_execution, "_monitor_loss_clock", lambda *_a, **_k: None)
    monkeypatch.setattr(
        canonical_paper_execution,
        "_note_profile_close",
        lambda *_a, **kw: noted.append(kw["now"]) or (1, False),
    )
    factory = _factory()
    plan, _command_id, _occurred = _losing_close(
        factory, "exit-backfill-no-clock", origin="backfill", age=None
    )
    assert noted == []
    with factory() as session:
        _assert_armed(session, plan.organization_id, "historical backfill")
        _assert_alert_audit(session, plan.organization_id, blocked=True)


@requires_postgres
def test_missing_candle_period_fails_closed_and_arms(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable timeframe on a 25h-old live loss arms even with the flag off."""

    _arm_on_backfill_loss(monkeypatch, False)
    monkeypatch.setattr(canonical_paper_execution, "_candle_period", lambda _timeframe: None)
    factory = _factory()
    plan, _command_id, _occurred = _losing_close(
        factory, "exit-live-no-candle", origin="live", age=timedelta(hours=25)
    )
    with factory() as session:
        _assert_armed(session, plan.organization_id, "too old")
        assert _stale_alert_refusal(session, plan.organization_id) == "kill_switch"
        _assert_alert_audit(session, plan.organization_id, blocked=True)


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
