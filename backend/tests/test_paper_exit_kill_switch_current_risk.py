"""Only a true backfill may skip the kill switch (AT-ADR-151).

A true backfill is a losing close at least
``paper_exit_backfill_loss_min_age_seconds`` old (default 24h) on the database
clock. A late loss younger than that arms as before, and a stale live one is
also noted on the streak and the daily lock at the close time. A missing
database clock or candle period always arms.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.common import ActorType, AuditEventType, AuditResult
from app.schemas.execution_protocol import ClosePaperPlanRequest
from app.services import paper_exit_protection
from app.services.canonical_paper_execution import stale_loss_notes_at_close_time
from app.services.paper_exit_monitor import ExitMonitorAlert, loss_is_true_backfill
from app.services.paper_exit_protection import (
    loss_arms_kill_switch,
    protect_account_from_backfill_loss,
    protect_account_from_stale_live_loss,
    record_exit_monitor_alert,
)

CLOSED_AT = datetime(2026, 11, 4, 15, 15, tzinfo=UTC)
DAY = timedelta(hours=24)
CANDLE = timedelta(minutes=15)
PROFILE = "paper-profile-isolated"


def _request(occurred_at: datetime = CLOSED_AT, *, origin: str = "live") -> ClosePaperPlanRequest:
    return ClosePaperPlanRequest(
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        revision_id=uuid.uuid4(),
        command_id=uuid.uuid4(),
        exit_price=Decimal("100"),
        fees=Decimal("0"),
        funding=Decimal("0"),
        slippage=Decimal("0"),
        exit_reason="stop_hit",
        occurred_at=occurred_at,
        idempotency_key="paper-exit-monitor:test",
        exit_channel="PAPER_EXIT_MONITOR",
        exit_actor="paper_exit_monitor",
        exit_evidence={"exit_origin": origin, "calibration_sample": "eligible"},
    )


def _arms(
    age: timedelta | None,
    *,
    settings: Settings | None = None,
    candle: timedelta | None = CANDLE,
    origin: str = "live",
) -> bool:
    return loss_arms_kill_switch(
        _request(origin=origin),
        evaluated_at=None if age is None else CLOSED_AT + age,
        candle_period=candle,
        settings=settings or Settings(),
    )


# --- item 1: only a close >= the configured age skips the switch -------------


def test_defaults_relax_only_a_day_old_backfill() -> None:
    settings = Settings()
    assert settings.paper_exit_kill_switch_on_backfill_loss is False
    assert settings.paper_exit_backfill_loss_min_age_seconds == 86400


@pytest.mark.parametrize("origin", ["live", "backfill"])
def test_a_day_old_loss_is_a_true_backfill_and_does_not_arm(origin: str) -> None:
    assert _arms(DAY, origin=origin) is False
    assert _arms(DAY * 3, origin=origin) is False


@pytest.mark.parametrize(
    "age",
    [
        timedelta(minutes=16),  # one candle late (#432 backoff, OOM restart)
        timedelta(hours=2),
        timedelta(hours=6),
        DAY - timedelta(microseconds=1),
    ],
)
@pytest.mark.parametrize("origin", ["live", "backfill"])
def test_late_loss_under_the_bound_arms_whatever_the_label(age: timedelta, origin: str) -> None:
    assert _arms(age, origin=origin) is True


def test_bound_is_configurable_and_cannot_go_below_a_day() -> None:
    settings = Settings(paper_exit_backfill_loss_min_age_seconds=48 * 3600)
    assert _arms(DAY, settings=settings) is True
    assert _arms(timedelta(hours=48), settings=settings) is False
    with pytest.raises(ValidationError):
        Settings(paper_exit_backfill_loss_min_age_seconds=3600)


def test_flag_arms_a_true_backfill_too() -> None:
    settings = Settings(paper_exit_kill_switch_on_backfill_loss=True)
    assert _arms(DAY * 3, settings=settings) is True


# --- item 3: fail-closed paths always arm ------------------------------------


@pytest.mark.parametrize("arm_flag", [False, True])
def test_missing_database_clock_arms(arm_flag: bool) -> None:
    settings = Settings(paper_exit_kill_switch_on_backfill_loss=arm_flag)
    assert _arms(None, settings=settings) is True
    assert _arms(None, settings=settings, origin="backfill") is True


@pytest.mark.parametrize("arm_flag", [False, True])
def test_missing_candle_period_arms(arm_flag: bool) -> None:
    settings = Settings(paper_exit_kill_switch_on_backfill_loss=arm_flag)
    assert _arms(DAY * 3, settings=settings, candle=None) is True
    assert _arms(DAY * 3, settings=settings, candle=None, origin="backfill") is True


def test_naive_clocks_are_never_a_true_backfill() -> None:
    naive_close = _request(CLOSED_AT.replace(tzinfo=None))
    assert not loss_is_true_backfill(
        naive_close, evaluated_at=CLOSED_AT + DAY * 3, candle_period=CANDLE, min_age=DAY
    )
    assert not loss_is_true_backfill(
        _request(),
        evaluated_at=(CLOSED_AT + DAY * 3).replace(tzinfo=None),
        candle_period=CANDLE,
        min_age=DAY,
    )


# --- item 2: a stale live loss also notes at the close time ------------------


def _notes(
    age: timedelta | None, *, margin: str = PROFILE, origin: str = "live", replayed: bool = False
) -> bool:
    return stale_loss_notes_at_close_time(
        _request(origin=origin),
        replayed=replayed,
        margin_assumption_id=margin,
        net_pnl=Decimal("-5"),
        evaluated_at=None if age is None else CLOSED_AT + age,
        candle_period=CANDLE,
        settings=Settings(),
    )


def test_stale_live_profile_loss_under_a_day_is_noted() -> None:
    assert _notes(timedelta(minutes=16)) is True
    assert _notes(timedelta(hours=6)) is True
    assert _notes(DAY - timedelta(microseconds=1)) is True


def test_true_backfill_missing_clock_non_profile_backfill_and_replay_are_not_noted() -> None:
    assert _notes(DAY) is False
    assert _notes(None) is False
    assert _notes(timedelta(hours=2), margin="cross-margin-conservative") is False
    assert _notes(timedelta(hours=2), origin="backfill") is False
    assert _notes(timedelta(hours=2), replayed=True) is False


# --- protect functions and the audit label (item 5) --------------------------


class _FakeKillSwitch:
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *_args: object) -> None:
        pass

    def activate(self, **kwargs: Any) -> None:
        _FakeKillSwitch.calls.append(kwargs)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, bool]]:
    alerts: list[tuple[object, bool]] = []
    _FakeKillSwitch.calls = []
    monkeypatch.setattr(paper_exit_protection, "KillSwitchService", _FakeKillSwitch)
    monkeypatch.setattr(paper_exit_protection, "AuditService", lambda _session: None)

    def _record(_session: object, alert: object, *, blocked: bool = True, **_kw: object) -> bool:
        alerts.append((alert, blocked))
        return True

    monkeypatch.setattr(paper_exit_protection, "record_exit_monitor_alert", _record)
    return alerts


_PROTECTORS = [protect_account_from_backfill_loss, protect_account_from_stale_live_loss]


@pytest.mark.parametrize("protect", _PROTECTORS)
def test_true_backfill_is_alerted_as_a_warning_not_armed(
    protect: Any, recorded: list[tuple[object, bool]], capsys: pytest.CaptureFixture[str]
) -> None:
    alert = protect(
        None,
        request=_request(),
        net_pnl=Decimal("-10.67"),
        arm_kill_switch=False,
        settings=Settings(),
    )
    assert _FakeKillSwitch.calls == []
    assert recorded == [(alert, False)]
    assert alert.fields["refusal"] == "none"
    assert "not armed" in alert.message
    assert "deactivates the kill switch" not in alert.message
    assert "paper_exit_backfill_loss_kill_switch_skipped" in capsys.readouterr().out


@pytest.mark.parametrize("protect", _PROTECTORS)
def test_armed_loss_arms_as_system_and_is_alerted_as_blocked(
    protect: Any, recorded: list[tuple[object, bool]]
) -> None:
    request = _request()
    alert = protect(
        None,
        request=request,
        net_pnl=Decimal("-10.67"),
        arm_kill_switch=True,
        settings=Settings(),
    )
    assert len(_FakeKillSwitch.calls) == 1
    call = _FakeKillSwitch.calls[0]
    assert call["organization_id"] == request.organization_id
    assert call["actor_type"] is ActorType.SYSTEM
    assert call["source"] == "exit_monitor"
    assert alert.fields["refusal"] == "kill_switch"
    assert recorded == [(alert, True)]


class _CapturingAudit:
    rows: ClassVar[list[Any]] = []

    def __init__(self, _session: object) -> None:
        pass

    def record(self, row: Any) -> None:
        _CapturingAudit.rows.append(row)


@pytest.mark.parametrize(
    ("blocked", "event", "result"),
    [
        (True, AuditEventType.RISK_BLOCK, AuditResult.BLOCKED),
        (False, AuditEventType.RISK_WARNING, AuditResult.WARNING),
    ],
)
def test_alert_audit_row_says_blocked_only_when_something_was_blocked(
    monkeypatch: pytest.MonkeyPatch,
    blocked: bool,
    event: AuditEventType,
    result: AuditResult,
) -> None:
    _CapturingAudit.rows = []
    monkeypatch.setattr(paper_exit_protection, "AuditService", _CapturingAudit)
    alert = ExitMonitorAlert(
        code="backfill_loss", severity="critical", message="m", fields={"command_id": "c"}
    )
    assert record_exit_monitor_alert(
        None,  # type: ignore[arg-type]
        alert,
        organization_id=None,
        user_id=None,
        blocked=blocked,
    )
    assert len(_CapturingAudit.rows) == 1
    assert _CapturingAudit.rows[0].event_type == event
    assert _CapturingAudit.rows[0].result == result
