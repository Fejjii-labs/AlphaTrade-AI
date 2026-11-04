"""Stale-bar and backfill paper losses arm the kill switch only when configured."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

import pytest

from app.core.config import Settings
from app.schemas.common import ActorType
from app.schemas.execution_protocol import ClosePaperPlanRequest
from app.services import paper_exit_protection
from app.services.paper_exit_protection import (
    protect_account_from_backfill_loss,
    protect_account_from_stale_live_loss,
)


def _request() -> ClosePaperPlanRequest:
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
        occurred_at=datetime(2026, 11, 4, 15, 15, tzinfo=UTC),
        idempotency_key="paper-exit-monitor:test",
        exit_channel="PAPER_EXIT_MONITOR",
        exit_actor="paper_exit_monitor",
    )


class _FakeKillSwitch:
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *_args: object) -> None:
        pass

    def activate(self, **kwargs: Any) -> None:
        _FakeKillSwitch.calls.append(kwargs)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    alerts: list[object] = []
    _FakeKillSwitch.calls = []
    monkeypatch.setattr(paper_exit_protection, "KillSwitchService", _FakeKillSwitch)
    monkeypatch.setattr(paper_exit_protection, "AuditService", lambda _session: None)

    def _record(_session: object, alert: object, **_kw: object) -> bool:
        alerts.append(alert)
        return True

    monkeypatch.setattr(paper_exit_protection, "record_exit_monitor_alert", _record)
    return alerts


_PROTECTORS = [protect_account_from_backfill_loss, protect_account_from_stale_live_loss]


def test_stale_loss_arming_defaults_off() -> None:
    assert Settings().paper_exit_kill_switch_on_stale_loss is False


@pytest.mark.parametrize("protect", _PROTECTORS)
def test_closed_stale_loss_is_alerted_and_logged_not_armed_by_default(
    protect: Any, recorded: list[object], capsys: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(paper_exit_kill_switch_on_stale_loss=False)
    alert = protect(None, request=_request(), net_pnl=Decimal("-10.67"), settings=settings)
    assert _FakeKillSwitch.calls == []
    assert recorded == [alert]
    assert alert.fields["refusal"] == "none"
    assert "not armed" in alert.message
    assert "deactivates the kill switch" not in alert.message
    assert "paper_exit_stale_loss_kill_switch_skipped" in capsys.readouterr().out


@pytest.mark.parametrize("protect", _PROTECTORS)
def test_closed_stale_loss_arms_as_system_when_opted_in(
    protect: Any, recorded: list[object]
) -> None:
    settings = Settings(paper_exit_kill_switch_on_stale_loss=True)
    request = _request()
    alert = protect(None, request=request, net_pnl=Decimal("-10.67"), settings=settings)
    assert len(_FakeKillSwitch.calls) == 1
    call = _FakeKillSwitch.calls[0]
    assert call["organization_id"] == request.organization_id
    assert call["actor_type"] is ActorType.SYSTEM
    assert call["source"] == "exit_monitor"
    assert alert.fields["refusal"] == "kill_switch"
    assert recorded == [alert]
