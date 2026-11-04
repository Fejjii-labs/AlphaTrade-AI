"""Process-wide Binance REST gate: 418 ban, 429 backoff, used weight, dedupe."""

from __future__ import annotations

import httpx
import pytest

from app.market_contracts.adapters.binance_rest_gate import (
    BinanceRestGate,
    process_binance_rest_gate,
    reset_process_binance_rest_gate,
)
from app.market_contracts.adapters.http import ReadOnlyHttpGetClient
from app.market_contracts.adapters.public_read_cache import ShortTtlSingleflight
from app.market_contracts.errors import RateLimitedError, UpstreamBanError
from app.providers.market_data import BinancePublicMarketDataProvider

BASE = "https://fapi.binance.com"
NOW_S = 1_800_000_000.0
CLOSED: dict[str, str | int] = {
    "symbol": "BTCUSDT",
    "limit": 1000,
    "startTime": 1_799_999_000_000,
    "endTime": 1_799_999_500_000,
}
OPEN: dict[str, str | int] = {
    "symbol": "BTCUSDT",
    "limit": 1000,
    "startTime": 1_799_999_990_000,
    "endTime": 1_800_000_000_000,
}


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Recorder:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return self.responses.pop(0) if self.responses else httpx.Response(200, json=[])


def _gate(clock: Clock, *, rng: float = 0.0, boot: float = 0.0) -> BinanceRestGate:
    return BinanceRestGate(
        clock=clock,
        wall_clock=lambda: NOW_S,
        rng=lambda: rng,
        boot_ramp_seconds=boot,
        aggtrades_min_interval_seconds=0.0,
    )


def _client(recorder: Recorder, gate: BinanceRestGate, **kw: object) -> ReadOnlyHttpGetClient:
    return ReadOnlyHttpGetClient(
        base_url=BASE,
        timeout_seconds=1.0,
        transport=httpx.MockTransport(recorder),
        rest_gate=gate,
        sleeper=lambda _s: None,
        wall_clock=lambda: NOW_S,
        **kw,  # type: ignore[arg-type]
    )


def test_418_sets_ban_window_and_blocks_every_client_in_process() -> None:
    clock = Clock()
    gate = _gate(clock)
    banned = Recorder([httpx.Response(418, headers={"Retry-After": "600"})])
    with pytest.raises(UpstreamBanError):
        _client(banned, gate).get_json("/fapi/v1/aggTrades", OPEN)
    closed = gate.remaining()
    assert closed is not None and closed[0] == "ban" and closed[1] >= 600

    other = Recorder([])
    klines: dict[str, str | int] = {"symbol": "BTCUSDT", "interval": "15m", "limit": 10}
    for path, params in (("/fapi/v1/aggTrades", OPEN), ("/fapi/v1/klines", klines)):
        with pytest.raises(UpstreamBanError):
            _client(other, gate).get_json(path, params)
    assert other.calls == 0, "no Binance REST call may be sent during a ban"

    clock.t += 600 * 1.3
    assert gate.remaining() is None
    _client(other, gate).get_json("/fapi/v1/aggTrades", OPEN)
    assert other.calls == 1
    assert gate.strikes == 0


def test_418_without_retry_after_uses_exponential_backoff_with_jitter() -> None:
    clock = Clock()
    gate = _gate(clock, rng=1.0)
    # A 418 without a deadline waits at least Binance's 2 minute minimum ban.
    first = [gate.note_failure("ban", None) for _ in range(5)]
    assert first == [pytest.approx(120.0 * 1.25)] * 5
    assert gate.note_failure("ban", None) == pytest.approx(160.0 * 1.25)
    assert gate.note_failure("ban", None) == pytest.approx(320.0 * 1.25)
    for _ in range(20):
        capped = gate.note_failure("ban", None)
    assert capped == pytest.approx(900.0 * 1.25)


def test_429_without_retry_after_keeps_the_short_exponential_backoff() -> None:
    gate = _gate(Clock(), rng=1.0)
    assert gate.note_failure("rate_limit", None) == pytest.approx(5.0 * 1.25)
    assert gate.note_failure("rate_limit", None) == pytest.approx(10.0 * 1.25)


def test_429_backs_off_honors_retry_after_and_stops_other_calls() -> None:
    clock = Clock()
    gate = _gate(clock)
    limited = Recorder(
        [httpx.Response(429, headers={"Retry-After": "120", "X-MBX-USED-WEIGHT-1M": "2400"})]
    )
    with pytest.raises(RateLimitedError):
        _client(limited, gate, max_retries=3).get_json("/fapi/v1/aggTrades", OPEN)
    assert limited.calls == 1, "Retry-After beyond the in-call bound means no retry now"
    closed = gate.remaining()
    assert closed is not None and closed[0] == "rate_limit" and closed[1] >= 120
    assert gate.last_used_weight == 2400

    other = Recorder([])
    with pytest.raises(RateLimitedError):
        _client(other, gate).get_json("/fapi/v1/aggTrades", OPEN)
    assert other.calls == 0


def test_short_429_retries_after_gate_reopens() -> None:
    clock = Clock()
    gate = _gate(clock)
    slept: list[float] = []

    def sleeper(seconds: float) -> None:
        slept.append(seconds)
        clock.t += seconds

    rec = Recorder([httpx.Response(429), httpx.Response(200, json=[{"a": 1}])])
    client = ReadOnlyHttpGetClient(
        base_url=BASE,
        timeout_seconds=1.0,
        transport=httpx.MockTransport(rec),
        rest_gate=gate,
        sleeper=sleeper,
        wall_clock=lambda: NOW_S,
        max_retries=1,
    )
    assert client.get_json("/fapi/v1/aggTrades", OPEN) == [{"a": 1}]
    assert rec.calls == 2
    assert sum(slept) >= 5.0
    assert gate.strikes == 0


def test_used_weight_header_near_limit_pauses_until_next_minute() -> None:
    clock = Clock()
    gate = _gate(clock)
    rec = Recorder([httpx.Response(200, json=[], headers={"X-MBX-USED-WEIGHT-1M": "2100"})])
    _client(rec, gate).get_json("/fapi/v1/aggTrades", OPEN)
    closed = gate.remaining()
    assert closed is not None and closed[0] == "rate_limit"
    assert 1.0 <= closed[1] <= 61.0
    with pytest.raises(RateLimitedError):
        _client(rec, gate).get_json("/fapi/v1/aggTrades", OPEN)
    assert rec.calls == 1


def test_identical_closed_aggtrades_requests_share_one_call() -> None:
    clock = Clock()
    gate = _gate(clock)
    cache = ShortTtlSingleflight(ttl_seconds=15.0, clock=clock)
    rec = Recorder([httpx.Response(200, json=[{"a": 7}]), httpx.Response(200, json=[{"a": 8}])])
    client = _client(rec, gate, aggtrades_request_cache=cache)
    assert client.get_json("/fapi/v1/aggTrades", CLOSED) == [{"a": 7}]
    assert client.get_json("/fapi/v1/aggTrades", dict(CLOSED)) == [{"a": 7}]
    assert rec.calls == 1


def test_open_window_aggtrades_is_never_cached_so_paper_fills_stay_fresh() -> None:
    clock = Clock()
    gate = _gate(clock)
    cache = ShortTtlSingleflight(ttl_seconds=15.0, clock=clock)
    rec = Recorder([httpx.Response(200, json=[{"a": 1}]), httpx.Response(200, json=[{"a": 2}])])
    client = _client(rec, gate, aggtrades_request_cache=cache)
    assert client.get_json("/fapi/v1/aggTrades", OPEN) == [{"a": 1}]
    assert client.get_json("/fapi/v1/aggTrades", OPEN) == [{"a": 2}]
    assert rec.calls == 2


def test_failed_load_is_not_cached() -> None:
    clock = Clock()
    gate = _gate(clock)
    cache = ShortTtlSingleflight(ttl_seconds=15.0, clock=clock)
    rec = Recorder([httpx.Response(418, headers={"Retry-After": "1"})])
    client = _client(rec, gate, aggtrades_request_cache=cache)
    with pytest.raises(UpstreamBanError):
        client.get_json("/fapi/v1/aggTrades", CLOSED)
    assert cache.get(("/fapi/v1/aggTrades", _key(CLOSED))) is None


def test_boot_ramp_spreads_aggtrades_burst() -> None:
    clock = Clock()
    gate = BinanceRestGate(
        clock=clock,
        rng=lambda: 0.0,
        boot_ramp_seconds=120.0,
        boot_aggtrades_min_interval_seconds=1.0,
        aggtrades_min_interval_seconds=0.25,
    )
    waits = [gate.reserve_spacing("/fapi/v1/aggTrades") for _ in range(5)]
    assert waits == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert gate.reserve_spacing("/fapi/v1/klines") == 0.0
    clock.t += 300
    assert gate.reserve_spacing("/fapi/v1/aggTrades") == 0.0
    assert gate.reserve_spacing("/fapi/v1/aggTrades") == 0.25


def test_client_sleeps_for_spacing_before_sending() -> None:
    clock = Clock()
    gate = BinanceRestGate(clock=clock, rng=lambda: 0.0, boot_ramp_seconds=120.0)
    slept: list[float] = []
    rec = Recorder([])
    client = ReadOnlyHttpGetClient(
        base_url=BASE,
        timeout_seconds=1.0,
        transport=httpx.MockTransport(rec),
        rest_gate=gate,
        sleeper=slept.append,
        wall_clock=lambda: NOW_S,
    )
    client.get_json("/fapi/v1/aggTrades", OPEN)
    client.get_json("/fapi/v1/aggTrades", OPEN)
    assert slept == [1.0]
    assert rec.calls == 2


def test_real_network_clients_share_process_gate_but_injected_transports_do_not() -> None:
    reset_process_binance_rest_gate()
    try:
        real = ReadOnlyHttpGetClient(base_url=BASE, timeout_seconds=1.0)
        assert real._gate is process_binance_rest_gate()
        real.close()
        injected = ReadOnlyHttpGetClient(
            base_url=BASE,
            timeout_seconds=1.0,
            transport=httpx.MockTransport(Recorder([])),
        )
        assert injected._gate is None
    finally:
        reset_process_binance_rest_gate()


def _key(params: dict[str, str | int]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(k), str(v)) for k, v in params.items()))


def _provider(recorder: Recorder, gate: BinanceRestGate) -> BinancePublicMarketDataProvider:
    return BinancePublicMarketDataProvider(
        futures_base_url=BASE, rest_gate=gate, transport=httpx.MockTransport(recorder)
    )


def test_418_on_premium_index_blocks_aggtrades() -> None:
    clock = Clock()
    gate = _gate(clock)
    banned = Recorder([httpx.Response(418, headers={"Retry-After": "600"})])
    with pytest.raises(UpstreamBanError):
        _provider(banned, gate).get_funding_rate("BTCUSDT")
    closed = gate.remaining()
    assert closed is not None and closed[0] == "ban" and closed[1] >= 600

    other = Recorder([])
    with pytest.raises(UpstreamBanError):
        _client(other, gate).get_json("/fapi/v1/aggTrades", OPEN)
    assert other.calls == 0


def test_418_on_aggtrades_blocks_premium_index_and_open_interest_fail_closed() -> None:
    clock = Clock()
    gate = _gate(clock)
    with pytest.raises(UpstreamBanError):
        _client(Recorder([httpx.Response(418, headers={"Retry-After": "600"})]), gate).get_json(
            "/fapi/v1/aggTrades", OPEN
        )
    rec = Recorder([])
    provider = _provider(rec, gate)
    with pytest.raises(UpstreamBanError):
        provider.get_funding_rate("BTCUSDT")
    with pytest.raises(UpstreamBanError):
        provider.get_open_interest("BTCUSDT")
    assert rec.calls == 0, "no fapi call and no mock fallback value during a ban"


def test_429_on_open_interest_records_backoff_and_weight() -> None:
    clock = Clock()
    gate = _gate(clock)
    rec = Recorder(
        [httpx.Response(429, headers={"Retry-After": "30", "X-MBX-USED-WEIGHT-1M": "2390"})]
    )
    with pytest.raises(RateLimitedError):
        _provider(rec, gate).get_open_interest("BTCUSDT")
    closed = gate.remaining()
    assert closed is not None and closed[0] == "rate_limit" and closed[1] >= 30
    assert gate.last_used_weight == 2390


def test_premium_index_success_records_used_weight() -> None:
    clock = Clock()
    gate = _gate(clock)
    rec = Recorder(
        [
            httpx.Response(
                200, json={"lastFundingRate": "0.0001"}, headers={"X-MBX-USED-WEIGHT-1M": "2100"}
            )
        ]
    )
    data = _provider(rec, gate).get_funding_rate("BTCUSDT")
    assert data is not None and data.envelope.fallback_used is False
    assert gate.last_used_weight == 2100
    assert gate.remaining() is not None


@pytest.mark.parametrize("header", ["inf", "nan", "-5"])
def test_unusable_retry_after_falls_back_to_the_ban_floor(header: str) -> None:
    clock = Clock()
    gate = _gate(clock, rng=1.0)
    rec = Recorder([httpx.Response(418, headers={"Retry-After": header})])
    with pytest.raises(UpstreamBanError):
        _client(rec, gate).get_json("/fapi/v1/aggTrades", OPEN)
    closed = gate.remaining()
    assert closed is not None
    assert 120.0 <= closed[1] <= 120.0 * 1.25
    clock.t += 120.0 * 1.25 + 1
    assert gate.remaining() is None


def test_huge_retry_after_is_clamped_to_three_days() -> None:
    clock = Clock()
    gate = _gate(clock, rng=1.0)
    rec = Recorder([httpx.Response(418, headers={"Retry-After": "1e12"})])
    with pytest.raises(UpstreamBanError):
        _client(rec, gate).get_json("/fapi/v1/aggTrades", OPEN)
    closed = gate.remaining()
    assert closed is not None
    # Jitter is bounded by the 900 s backoff cap, not proportional to the deadline.
    assert 3 * 86_400.0 <= closed[1] <= 3 * 86_400.0 + 900.0 * 0.25
    clock.t += 3 * 86_400.0 + 900.0 * 0.25 + 1
    assert gate.remaining() is None


def test_gate_note_failure_clamps_raw_retry_after() -> None:
    gate = _gate(Clock())
    # Non-finite or negative is rejected: a 418 falls back to the 120 s floor.
    assert gate.note_failure("ban", float("inf")) == pytest.approx(120.0)
    assert gate.note_failure("ban", float("nan")) == pytest.approx(120.0)
    assert gate.note_failure("ban", -1.0) == pytest.approx(120.0)
    # Finite but huge is clamped to the 3 day ceiling.
    assert gate.note_failure("ban", 1e12) == pytest.approx(3 * 86_400.0)


def test_entry_gate_check_blocks_cached_closed_window_during_ban() -> None:
    """Fails if only the request_json entry check is removed."""

    clock = Clock()
    gate = _gate(clock)
    cache = ShortTtlSingleflight(ttl_seconds=15.0, clock=clock)
    rec = Recorder([httpx.Response(200, json=[{"a": 1}])])
    client = _client(rec, gate, aggtrades_request_cache=cache)
    assert client.get_json("/fapi/v1/aggTrades", CLOSED) == [{"a": 1}]
    gate.note_failure("ban", 600.0)
    with pytest.raises(UpstreamBanError):
        client.get_json("/fapi/v1/aggTrades", CLOSED)


def test_per_attempt_gate_check_stops_retry_after_gate_closes() -> None:
    """Fails if only the per-attempt check inside the retry loop is removed."""

    clock = Clock()
    gate = _gate(clock)
    rec = Recorder([httpx.Response(429), httpx.Response(200, json=[{"a": 1}])])
    client = _client(rec, gate, max_retries=1)
    with pytest.raises(RateLimitedError):
        client.get_json("/fapi/v1/aggTrades", OPEN)
    assert rec.calls == 1, "the retry must not be sent while the gate is still closed"


def test_aggtrades_without_end_time_is_never_cached() -> None:
    clock = Clock()
    gate = _gate(clock)
    cache = ShortTtlSingleflight(ttl_seconds=15.0, clock=clock)
    params: dict[str, str | int] = {"symbol": "BTCUSDT", "limit": 1000, "startTime": 1}
    rec = Recorder([httpx.Response(200, json=[{"a": 1}]), httpx.Response(200, json=[{"a": 2}])])
    client = _client(rec, gate, aggtrades_request_cache=cache)
    assert client.get_json("/fapi/v1/aggTrades", params) == [{"a": 1}]
    assert client.get_json("/fapi/v1/aggTrades", params) == [{"a": 2}]
    assert rec.calls == 2


def test_end_time_string_and_int_share_cache_key() -> None:
    clock = Clock()
    gate = _gate(clock)
    cache = ShortTtlSingleflight(ttl_seconds=15.0, clock=clock)
    rec = Recorder([httpx.Response(200, json=[{"a": 7}]), httpx.Response(200, json=[{"a": 8}])])
    client = _client(rec, gate, aggtrades_request_cache=cache)
    as_str = {k: str(v) for k, v in CLOSED.items()}
    assert client.get_json("/fapi/v1/aggTrades", CLOSED) == [{"a": 7}]
    assert client.get_json("/fapi/v1/aggTrades", as_str) == [{"a": 7}]
    assert rec.calls == 1


@pytest.mark.parametrize("raw", ["12abc", "-1", "1.5", "", "\uff11\uff12"])
def test_used_weight_header_parse_is_strict(raw: str) -> None:
    gate = _gate(Clock())
    gate.note_used_weight({"x-mbx-used-weight-1m": raw})
    assert gate.last_used_weight is None


# --- 2026-11-04 overlap incident: warnings, deadline sharing, standby hold ---


def _events(logs: list[dict], name: str) -> list[dict]:
    return [entry for entry in logs if entry["event"] == name]


def test_used_weight_warning_is_strictly_above_threshold_and_rate_limited() -> None:
    from structlog.testing import capture_logs

    clock = Clock()
    gate = BinanceRestGate(
        clock=clock,
        wall_clock=lambda: NOW_S,
        used_weight_warn_threshold=1500,
        used_weight_warn_interval_seconds=60.0,
    )
    with capture_logs() as logs:
        gate.note_success({"x-mbx-used-weight-1m": "1500"})
        assert _events(logs, "binance_used_weight_high") == []
        gate.note_success({"x-mbx-used-weight-1m": "1501"})
        gate.note_used_weight({"x-mbx-used-weight-1m": "1700"})
        clock.t += 59.0
        gate.note_success({"x-mbx-used-weight-1m": "1600"})
        clock.t += 1.0
        gate.note_used_weight({"x-mbx-used-weight-1m": "1800"})
    warned = _events(logs, "binance_used_weight_high")
    assert [entry["used_weight_1m"] for entry in warned] == [1501, 1800]
    assert all(entry["log_level"] == "warning" for entry in warned)
    assert warned[0]["warn_threshold"] == 1500
    assert gate.last_used_weight == 1800


def test_every_429_and_418_logs_a_warning_without_rate_limit() -> None:
    from structlog.testing import capture_logs

    gate = _gate(Clock())
    with capture_logs() as logs:
        for _ in range(3):
            gate.note_failure("rate_limit", 2.0)
        gate.note_failure("ban", None)
    throttled = _events(logs, "binance_rest_throttled")
    assert [entry["status_code"] for entry in throttled] == [429, 429, 429, 418]
    assert all(entry["log_level"] == "warning" for entry in throttled)
    assert throttled[-1]["strikes"] == 4


def test_failure_listener_gets_wall_clock_deadline_and_errors_are_contained() -> None:
    clock = Clock()
    gate = _gate(clock)
    seen: list[tuple[str, float]] = []
    gate.set_failure_listener(lambda kind, until: seen.append((kind, until)))
    seconds = gate.note_failure("ban", 600.0)
    assert seen == [("ban", NOW_S + seconds)]

    def boom(kind: str, until: float) -> None:
        raise RuntimeError("db down")

    gate.set_failure_listener(boom)
    gate.note_failure("rate_limit", 1.0)  # does not raise
    assert gate.remaining() is not None


def test_adopt_deadline_keeps_later_deadline_and_counts_no_strike() -> None:
    clock = Clock()
    gate = _gate(clock)
    assert gate.adopt_deadline("ban", NOW_S + 300.0) == pytest.approx(300.0)
    assert gate.remaining() == ("ban", pytest.approx(300.0))
    # An earlier deadline never shortens the window.
    gate.adopt_deadline("rate_limit", NOW_S + 10.0)
    assert gate.remaining() == ("ban", pytest.approx(300.0))
    assert gate.strikes == 0
    # Past or nonsense deadlines are ignored.
    assert gate.adopt_deadline("ban", NOW_S - 1.0) == 0.0
    assert gate.adopt_deadline("other", NOW_S + 50.0) == 0.0
    clock.t += 301.0
    assert gate.remaining() is None


def test_hold_reads_as_rate_limit_until_released_or_expired() -> None:
    clock = Clock()
    gate = _gate(clock)
    gate.hold(240.0)
    assert gate.holding
    assert gate.remaining() == ("rate_limit", pytest.approx(240.0))
    gate.release_hold()
    assert not gate.holding
    assert gate.remaining() is None
    gate.hold(10.0)
    clock.t += 10.0
    assert not gate.holding
    assert gate.remaining() is None


def test_hold_blocks_http_client_before_any_request() -> None:
    clock = Clock()
    gate = _gate(clock)
    gate.hold(60.0)
    recorder = Recorder([])
    client = ReadOnlyHttpGetClient(
        base_url=BASE,
        timeout_seconds=1.0,
        transport=httpx.MockTransport(recorder),
        rest_gate=gate,
    )
    with pytest.raises(RateLimitedError):
        client.get_json("/fapi/v1/time")
    assert recorder.calls == 0
