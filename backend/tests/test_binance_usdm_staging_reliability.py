"""Binance USD-M HTTP 418 ban backoff and monitor recovery.

These tests do not enable Watcher, Telegram, or live trading.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.http import ReadOnlyHttpGetClient
from app.market_contracts.adapters.request_budget import SlidingWeightBudget, request_weight
from app.market_contracts.enums import GapState, ReconnectState
from app.market_contracts.errors import (
    RateLimitedError,
    RegionalProviderFailureError,
    SpotFallbackRejectedError,
    UpstreamBanError,
)
from app.market_monitor.backoff import BackoffPolicy, delay_seconds
from app.market_monitor.monitor import PerpetualMarketMonitor
from app.market_monitor.types import MarketAvailability, MonitorReason
from app.providers.base import ProviderHealth
from tests.support.live_market_monitor import ScriptedPerpetualSource
from tests.test_live_market_monitor import T0, _live_monitor, _trade


def _client(
    handler: object,
    *,
    max_retries: int,
    sleeper: object,
    budget: SlidingWeightBudget | None = None,
    max_backoff_seconds: float = 30.0,
) -> ReadOnlyHttpGetClient:
    transport = (
        handler if isinstance(handler, httpx.BaseTransport) else httpx.MockTransport(handler)  # type: ignore[arg-type]
    )
    return ReadOnlyHttpGetClient(
        base_url="https://fapi.binance.com",
        timeout_seconds=2.0,
        transport=transport,
        max_retries=max_retries,
        max_backoff_seconds=max_backoff_seconds,
        sleeper=sleeper,  # type: ignore[arg-type]
        budget=budget,
        weight_per_minute=1800,
    )


def test_418_then_recovery_retries_once_inside_the_ban_window() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(418, headers={"Retry-After": "2"}, json={"msg": "banned"})
        return httpx.Response(200, json={"ok": True})

    client = _client(handler, max_retries=1, sleeper=sleeps.append)
    assert client.get_json("/fapi/v1/ping") == {"ok": True}
    client.close()
    assert calls["n"] == 2
    assert sleeps == [2.0]


def test_repeated_418_with_a_long_ban_does_not_send_another_request() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(418, headers={"Retry-After": "180"}, json={"msg": "banned"})

    client = _client(handler, max_retries=3, sleeper=sleeps.append)
    with pytest.raises(UpstreamBanError) as first:
        client.get_json("/fapi/v1/ping")
    with pytest.raises(UpstreamBanError) as second:
        client.get_json("/fapi/v1/time")
    client.close()
    assert calls["n"] == 1
    assert sleeps == []
    assert first.value.retry_after_seconds == 180.0
    assert second.value.retry_after_seconds is not None
    assert 0 < second.value.retry_after_seconds <= 180.0
    assert not isinstance(first.value, RegionalProviderFailureError)
    assert not isinstance(second.value, RegionalProviderFailureError)


def _assert_regional_status_is_not_retried(status: int) -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status, json={"msg": "restricted"})

    client = _client(handler, max_retries=3, sleeper=sleeps.append)
    with pytest.raises(RegionalProviderFailureError, match=f"HTTP {status}"):
        client.get_json("/fapi/v1/ping")
    client.close()
    assert calls["n"] == 1
    assert sleeps == []


def test_451_401_and_403_stay_regional_and_are_not_retried() -> None:
    for status in (451, 401, 403):
        _assert_regional_status_is_not_retried(status)


def test_429_stays_rate_limited_and_is_not_an_upstream_ban() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "4"}, json={"msg": "slow"})

    client = _client(handler, max_retries=0, sleeper=lambda _seconds: None)
    with pytest.raises(RateLimitedError) as exc:
        client.get_json("/fapi/v1/ping")
    client.close()
    assert type(exc.value) is RateLimitedError
    assert exc.value.retry_after_seconds == 4.0


def test_timeout_stays_a_regional_provider_failure() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("timed out")

    client = _client(handler, max_retries=1, sleeper=sleeps.append)
    with pytest.raises(RegionalProviderFailureError, match="unreachable"):
        client.get_json("/fapi/v1/time")
    client.close()
    assert calls["n"] == 2
    assert sleeps == [1.0]


def test_418_preserves_the_request_weight_budget() -> None:
    calls = {"n": 0}
    weight = request_weight("/fapi/v1/aggTrades", {"symbol": "BTCUSDT", "limit": 1000})
    budget = SlidingWeightBudget(limit=weight, max_wait_seconds=0.0, _clock=lambda: 0.0)

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(418, headers={"Retry-After": "90"}, json={"msg": "banned"})

    client = _client(handler, max_retries=2, sleeper=lambda _seconds: None, budget=budget)
    with pytest.raises(UpstreamBanError):
        client.get_json("/fapi/v1/aggTrades", {"symbol": "BTCUSDT", "limit": 1000})
    with pytest.raises(RateLimitedError):
        client.get_json("/fapi/v1/aggTrades", {"symbol": "BTCUSDT", "limit": 1000})
    client.close()
    assert calls["n"] == 1
    assert budget.used() == weight


def test_418_status_does_not_use_spot_or_fabricated_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "fapi.binance.com":
            raise AssertionError(request.url.host)
        return httpx.Response(418, headers={"Retry-After": "90"}, json={"msg": "banned"})

    source = BinanceUsdmPerpetualSource(
        transport=httpx.MockTransport(handler),
        max_retries=3,
        max_backoff_seconds=30.0,
    )
    status = source.status()
    source.close()
    assert status.health is ProviderHealth.DEGRADED
    assert status.using_fallback is False
    assert status.is_mock is False
    assert "spot" in (status.detail or "").lower()

    with pytest.raises(SpotFallbackRejectedError):
        ReadOnlyHttpGetClient(
            base_url="https://api.binance.com",
            timeout_seconds=2.0,
        )


def test_ban_backoff_honors_retry_after_without_raising_request_volume() -> None:
    policy = BackoffPolicy(initial_seconds=0.25, max_seconds=30.0, multiplier=2.0)
    assert delay_seconds(0, policy, retry_after_seconds=90.0) == 30.0
    assert delay_seconds(0, policy, retry_after_seconds=90.0, honor_full_retry_after=True) == 90.0


def test_upstream_ban_backs_off_without_dropping_the_connection_epoch() -> None:
    source = ScriptedPerpetualSource()
    source.enqueue([_trade(10, when=T0 - timedelta(seconds=1), price="100000")])
    monitor = _live_monitor(source)
    fresh = monitor.tick("BTCUSDT", now=T0)
    epoch = fresh.stream.connection_identity
    source.enqueue(UpstreamBanError("banned", retry_after_seconds=120.0))
    banned = monitor.tick("BTCUSDT", now=T0 + timedelta(milliseconds=20))
    assert banned.reason is MonitorReason.RATE_LIMITED
    assert banned.availability is MarketAvailability.DEGRADED
    assert banned.stream.connection_identity == epoch
    assert banned.stream.reconnect_state is ReconnectState.CONTINUOUS
    assert banned.current_price is not None
    assert banned.current_price.usable_as_current_market_price is True
    assert banned.fallback_used is False
    source.enqueue([_trade(11, when=T0 + timedelta(seconds=120), price="100010")])
    held = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=30))
    assert held.backoff.active is True
    assert source.fetch_count == 2
    recovered = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=121))
    assert recovered.availability is MarketAvailability.FRESH
    assert recovered.current_price is not None
    assert str(recovered.current_price.price) == "100010"
    assert recovered.stream.connection_identity == epoch


def test_empty_then_incomplete_backfill_can_recover_without_restart() -> None:
    source = ScriptedPerpetualSource()
    source.enqueue([_trade(10, when=T0 - timedelta(seconds=2), price="100000")])
    monitor = _live_monitor(source)
    connected = monitor.tick("BTCUSDT", now=T0)
    epoch = connected.stream.connection_identity
    source.enqueue(RegionalProviderFailureError("connect timeout"))
    dropped = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=1))
    assert dropped.reason is MonitorReason.PROVIDER_UNAVAILABLE
    assert dropped.current_price is None
    assert dropped.stream.reconnect_state is ReconnectState.RECONNECTING
    reconnect_epoch = dropped.stream.connection_identity
    assert reconnect_epoch != epoch
    held = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=1, milliseconds=100))
    assert held.backoff.active is True
    assert source.fetch_count == 2
    empty = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=2))
    assert empty.current_price is None
    assert empty.stream.connection_identity == reconnect_epoch
    assert empty.stream.gap_state is GapState.SUSPECTED
    assert empty.fallback_used is False
    assert empty.provider.using_fallback is False
    source.enqueue([_trade(12, when=T0 + timedelta(seconds=1), price="100200")])
    incomplete = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=3))
    assert incomplete.current_price is None
    assert incomplete.stream.connection_identity == reconnect_epoch
    assert incomplete.stream.gap_state is GapState.CONFIRMED
    assert incomplete.stream.reconnect_count == 1
    during = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=3, milliseconds=100))
    assert during.backoff.active is True
    assert during.current_price is None
    assert source.fetch_count == 4
    source.enqueue(
        [
            _trade(11, when=T0 + timedelta(seconds=1), price="100100"),
            _trade(12, when=T0 + timedelta(seconds=1, milliseconds=10), price="100200"),
        ]
    )
    recovered = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=4))
    assert recovered.stream.connection_identity == reconnect_epoch
    assert recovered.stream.gap_state is GapState.NONE
    assert recovered.stream.reconnect_state in {
        ReconnectState.RECOVERED,
        ReconnectState.CONTINUOUS,
    }
    assert recovered.current_price is not None
    assert recovered.current_price.usable_as_current_market_price is True
    assert recovered.fallback_used is False


def test_process_restart_opens_a_new_epoch_after_a_failed_backfill() -> None:
    source = ScriptedPerpetualSource()
    source.enqueue([_trade(10, when=T0 - timedelta(seconds=2))])
    monitor = _live_monitor(source)
    monitor.tick("BTCUSDT", now=T0)
    source.enqueue(RegionalProviderFailureError("connect timeout"))
    failed = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=1))
    stalled = failed.stream.connection_identity
    empty = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=2))
    assert empty.current_price is None
    monitor.restart()
    source.enqueue([_trade(30, when=T0 + timedelta(seconds=3), price="101000")])
    restarted = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=4))
    assert restarted.stream.connection_identity != stalled
    assert restarted.stream.reconnect_count == 0
    assert restarted.availability is MarketAvailability.FRESH
    assert restarted.current_price is not None
    assert restarted.current_price.usable_as_current_market_price is True
    assert restarted.fallback_used is False


def test_incomplete_coverage_does_not_expose_a_stale_price() -> None:
    source = ScriptedPerpetualSource()
    source.enqueue([_trade(10, when=T0 - timedelta(seconds=2), price="100000")])
    monitor = PerpetualMarketMonitor(
        source,
        replay=False,
        backoff=BackoffPolicy(initial_seconds=30.0, max_seconds=30.0, multiplier=1.0),
        poll_seconds=0.01,
    )
    monitor.tick("BTCUSDT", now=T0)
    source.enqueue(RegionalProviderFailureError("connect timeout"))
    monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=1))
    source.enqueue([_trade(14, when=T0 + timedelta(seconds=1), price="100500")])
    incomplete = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=32))
    assert incomplete.current_price is None
    later = monitor.tick("BTCUSDT", now=T0 + timedelta(seconds=40))
    assert later.backoff.active is True
    assert later.current_price is None
    assert later.reason in {
        MonitorReason.GAP,
        MonitorReason.RECONNECTING,
        MonitorReason.BACKOFF,
        MonitorReason.PROVIDER_UNAVAILABLE,
    }
    price = later.current_price
    assert price is None or price.usable_as_current_market_price is False


def test_spot_rejection_still_does_not_fabricate_a_price() -> None:
    source = ScriptedPerpetualSource()
    source.enqueue(SpotFallbackRejectedError("spot cannot satisfy perpetual evidence"))
    snapshot = _live_monitor(source).tick("BTCUSDT", now=T0)
    assert snapshot.reason is MonitorReason.SPOT_REJECTED
    assert snapshot.current_price is None
    assert snapshot.fallback_used is False
    assert snapshot.provider.using_fallback is False
