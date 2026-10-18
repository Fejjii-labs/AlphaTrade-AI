"""Breaker trips are persisted. A drawdown latch is not cleared here."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.services.risk_policy.breaker_trip import trip_breakers
from app.services.risk_policy.breakers import BreakerConfig, BreakerSnapshot

NOW = datetime(2026, 10, 18, 12, tzinfo=UTC)
ORG = uuid4()
ACCOUNT = uuid4()


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_breaker(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return kwargs


def _snapshot(**overrides: object) -> BreakerSnapshot:
    base: dict[str, object] = {
        "equity": Decimal("10000"),
        "equity_peak": Decimal("10000"),
        "reference_equity": Decimal("10000"),
        "realized_pnl_today": Decimal("0"),
        "unrealized_pnl_today": Decimal("0"),
        "consecutive_losses": 0,
        "drawdown_latched": False,
        "now": NOW,
    }
    base.update(overrides)
    return BreakerSnapshot(**base)  # type: ignore[arg-type]


def _config() -> BreakerConfig:
    return BreakerConfig(
        daily_loss_limit_percent=Decimal("6"),
        drawdown_breaker_percent=Decimal("10"),
        consecutive_loss_limit=4,
    )


def test_daily_loss_pause_is_recorded_without_a_consecutive_pause() -> None:
    recorder = _Recorder()
    verdict = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(realized_pnl_today=Decimal("-700")),
        now=NOW,
    )
    assert verdict.daily_loss_active and verdict.refuse_new_trades
    assert verdict.consecutive_loss_active is False
    written = recorder.calls[0]
    assert written["daily_paused_until"] == verdict.paused_until
    assert written["consecutive_paused_until"] is None
    assert written["drawdown_latched"] is False


def test_consecutive_losses_pause_without_touching_the_daily_pause() -> None:
    recorder = _Recorder()
    verdict = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(consecutive_losses=4),
        now=NOW,
    )
    assert verdict.consecutive_loss_active and verdict.refuse_new_trades
    written = recorder.calls[0]
    assert written["consecutive_paused_until"] == verdict.paused_until
    assert written["daily_paused_until"] is None
    assert written["consecutive_losses"] == 4


def test_drawdown_latches_and_stays_latched() -> None:
    recorder = _Recorder()
    verdict = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(equity=Decimal("8000"), equity_peak=Decimal("10000")),
        now=NOW,
    )
    assert verdict.drawdown_active and verdict.trip_kill_switch
    assert recorder.calls[0]["drawdown_latched"] is True
    held = trip_breakers(
        recorder,
        organization_id=ORG,
        account_id=ACCOUNT,
        config=_config(),
        snapshot=_snapshot(
            equity=Decimal("9500"),
            equity_peak=Decimal("10000"),
            drawdown_latched=True,
        ),
        now=NOW,
    )
    assert held.drawdown_active
    assert recorder.calls[1]["drawdown_latched"] is True
