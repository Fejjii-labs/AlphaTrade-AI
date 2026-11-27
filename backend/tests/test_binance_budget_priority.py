"""Audit M-5: candle reads keep room in the Binance budget; long starvation pages."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.market_contracts.adapters import request_budget
from app.market_contracts.adapters.request_budget import SlidingWeightBudget
from app.market_contracts.errors import RateLimitedError


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _budget(clock: _Clock, *, max_wait: float = 0.0) -> SlidingWeightBudget:
    return SlidingWeightBudget(limit=100, max_wait_seconds=max_wait, _clock=clock)


def test_bulk_trade_reads_leave_room_for_candles() -> None:
    clock = _Clock()
    budget = _budget(clock)
    for _ in range(4):  # 4 x 20 = 80, the bulk ceiling
        budget.acquire(20, sleeper=clock.sleep, bulk=True)
    with pytest.raises(RateLimitedError):
        budget.acquire(20, sleeper=clock.sleep, bulk=True)
    # Candles still fit in the reserved 20%.
    for _ in range(4):
        budget.acquire(5, sleeper=clock.sleep)
    assert budget.used() == 100


def test_without_bulk_flag_the_whole_budget_is_usable() -> None:
    clock = _Clock()
    budget = _budget(clock)
    for _ in range(5):
        budget.acquire(20, sleeper=clock.sleep)
    assert budget.used() == 100


def test_starvation_is_tracked_until_a_read_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_budget, "_STARVED_SINCE", None)
    clock = _Clock()
    budget = _budget(clock)
    budget.acquire(100, sleeper=clock.sleep)
    with pytest.raises(RateLimitedError):
        budget.acquire(1, sleeper=clock.sleep)
    first = request_budget.budget_starved_since()
    assert first is not None
    with pytest.raises(RateLimitedError):
        budget.acquire(1, sleeper=clock.sleep)
    assert request_budget.budget_starved_since() == first  # the streak keeps its start
    clock.now += 61  # the window frees up
    budget.acquire(1, sleeper=clock.sleep)
    assert request_budget.budget_starved_since() is None


def test_publisher_pages_once_after_five_minutes_of_starvation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.services.market_ban_visibility import MarketBanPublisher

    now = datetime(2026, 11, 27, 20, 0, tzinfo=UTC)
    state = SimpleNamespace(
        closed_kind=None,
        until_epoch=None,
        last_status_code=None,
        banned=False,
        ban_started_epoch=None,
    )
    monkeypatch.setattr(
        "app.market_contracts.adapters.binance_rest_gate.process_binance_rest_gate_state",
        lambda: state,
    )
    monkeypatch.setattr(
        "app.persistence.runtime_status.publish_runtime_status", lambda *_a, **_k: None
    )
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.services.worker_heartbeat.raise_prepared_alert",
        lambda _settings, alert, **_kwargs: alerts.append((alert.code, alert.message)) or True,
    )
    monkeypatch.setattr(request_budget, "_STARVED_SINCE", now.timestamp() - 240)
    publisher = MarketBanPublisher(SimpleNamespace(watcher_paper_worker_id="w"), object())  # type: ignore[arg-type]
    publisher.observe(now=now)
    assert alerts == []  # 4 minutes: not yet
    later = datetime.fromtimestamp(now.timestamp() + 120, tz=UTC)
    publisher.observe(now=later)
    publisher.observe(now=later)
    assert [code for code, _ in alerts] == ["market_data_starved"]
    assert "6 minutes" in alerts[0][1]


def test_the_starved_code_reaches_telegram_by_default() -> None:
    from app.services.safety_alerts import TELEGRAM_SAFETY_DEFAULT_CODES

    assert "market_data_starved" in TELEGRAM_SAFETY_DEFAULT_CODES
