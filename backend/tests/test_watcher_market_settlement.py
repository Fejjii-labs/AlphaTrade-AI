"""The Binance settlement wait is deterministic when the clock is injected."""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.workers.watcher_market import (
    SymbolMarketFactory,
    _wait_for_binance_rest_settlement,
)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="local",
        execution_mode="paper",
        enable_real_trading=False,
        real_trading_enabled=False,
        database_url="sqlite+pysqlite:///:memory:",
        rate_limit_use_redis=False,
        access_token_denylist_use_redis=False,
        market_data_cache_use_redis=False,
        watcher_orchestration_enabled=False,
        perpetual_evidence_source="binance_usdm",
        binance_request_max_retries=0,
    )


def _frozen(moment: datetime):
    sleeps: list[float] = []

    def clock() -> datetime:
        return moment if not sleeps else moment + timedelta(seconds=sum(sleeps))

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    return clock, sleeper, sleeps


def test_settlement_wait_sleeps_only_inside_the_five_second_window(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.workers.watcher_market.sleep",
        lambda _seconds: pytest.fail("injected clock must not call time.sleep"),
    )
    inside = datetime(2026, 10, 16, 0, 0, 3, tzinfo=UTC)
    clock, sleeper, sleeps = _frozen(inside)
    assert _wait_for_binance_rest_settlement(inside, now=clock, sleeper=sleeper) is True
    assert sleeps == [2.0]
    assert clock() == datetime(2026, 10, 16, 0, 0, 5, tzinfo=UTC)

    at_close = datetime(2026, 10, 16, 0, 15, 0, tzinfo=UTC)
    clock, sleeper, sleeps = _frozen(at_close)
    assert _wait_for_binance_rest_settlement(at_close, now=clock, sleeper=sleeper) is True
    assert sleeps == [5.0]

    settled = datetime(2026, 10, 16, 0, 0, 5, tzinfo=UTC)
    clock, sleeper, sleeps = _frozen(settled)
    assert _wait_for_binance_rest_settlement(settled, now=clock, sleeper=sleeper) is False
    assert sleeps == []

    later = datetime(2026, 10, 16, 0, 0, 30, tzinfo=UTC)
    clock, sleeper, sleeps = _frozen(later)
    assert _wait_for_binance_rest_settlement(later, now=clock, sleeper=sleeper) is False
    assert sleeps == []


def test_default_settlement_wait_uses_the_wall_clock(monkeypatch) -> None:
    fixed = datetime(2026, 10, 16, 0, 0, 3, tzinfo=UTC)
    slept: list[float] = []

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr("app.workers.watcher_market.datetime", Frozen)
    monkeypatch.setattr("app.workers.watcher_market.sleep", slept.append)
    assert _wait_for_binance_rest_settlement(fixed) is True
    assert slept == [2.0]


def test_probe_uses_the_injected_clock_before_the_market_read(monkeypatch) -> None:
    moment = datetime(2026, 10, 16, 0, 0, 3, tzinfo=UTC)
    state = {"at": moment}
    sleeps: list[float] = []

    def clock() -> datetime:
        return state["at"]

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        state["at"] += timedelta(seconds=seconds)

    monkeypatch.setattr(
        "app.workers.watcher_market.sleep",
        lambda _seconds: pytest.fail("probe must not block on the real grace period"),
    )
    factory = SymbolMarketFactory(_settings(), clock=clock, sleeper=sleeper)
    monkeypatch.setattr(factory, "composition", lambda _symbol: (object(), object(), None, ()))

    def read_started(*_args, **_kwargs):
        raise RuntimeError("read started")

    monkeypatch.setattr("app.workers.watcher_market.instrument_for_source", read_started)
    with pytest.raises(RuntimeError, match="read started"):
        factory.probe("ETHUSDT")
    assert sleeps == [2.0]
    assert state["at"] == datetime(2026, 10, 16, 0, 0, 5, tzinfo=UTC)
