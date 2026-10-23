"""Short quote/kline cache, 418/429 pause, and failure logs. No live trading."""

from __future__ import annotations

import threading
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from structlog.testing import capture_logs

from app.api.routes.health import readiness_projection
from app.core.config import Settings
from app.evidence_pipeline.current_price import quote_current_price
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.adapters.bybit_usdt_perpetual import BybitUsdtPerpetualSource
from app.market_contracts.adapters.failover import FailoverPerpetualSource
from app.market_contracts.adapters.http import ReadOnlyHttpGetClient
from app.market_contracts.adapters.public_read_cache import ShortTtlSingleflight
from app.market_contracts.errors import (
    EvidenceSourceSwitchRequiredError,
    RateLimitedError,
    RegionalProviderFailureError,
)
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.identity import (
    binance_usdm_btcusdt,
    bybit_usdt_perpetual_btcusdt,
    interval_timedelta,
)
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.schemas.common import Timeframe
from tests.support.phase5_market import CONNECTION, EVALUATED_AT, TRIGGER_OPEN, trade


def _kline_row(open_time: object) -> list[object]:
    from datetime import datetime

    assert isinstance(open_time, datetime)
    end = open_time + interval_timedelta(Timeframe.M15)
    return [
        int(open_time.timestamp() * 1000),
        "100000",
        "100003",
        "99999",
        "100002",
        "10",
        int(end.timestamp() * 1000) - 1,
        "1000000",
        4,
        "0",
        "0",
        "0",
    ]


def _client(handler: object, *, max_retries: int = 0) -> ReadOnlyHttpGetClient:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return ReadOnlyHttpGetClient(
        base_url="https://fapi.binance.com",
        timeout_seconds=2.0,
        transport=transport,
        max_retries=max_retries,
        max_backoff_seconds=30.0,
        sleeper=lambda _seconds: None,
    )


def test_default_retry_budget_is_one_attempt_past_the_first_read() -> None:
    assert Settings(_env_file=None).binance_request_max_retries == 1
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("down")

    source = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=httpx.MockTransport(handler),
    )
    status = source.status()
    source.close()
    assert calls["n"] == 2
    assert status.health is ProviderHealth.UNAVAILABLE
    assert status.using_fallback is False


def test_429_pause_blocks_the_next_request_until_retry_after() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "30", "X-Api-Key": "headersecret"},
            json={"msg": "slow"},
        )

    client = _client(handler, max_retries=0)
    with pytest.raises(RateLimitedError) as first:
        client.get_json("/fapi/v1/ping")
    with pytest.raises(RateLimitedError) as second:
        client.get_json("/fapi/v1/time")
    client.close()
    assert calls["n"] == 1
    assert first.value.retry_after_seconds == 30.0
    assert second.value.retry_after_seconds is not None
    assert second.value.retry_after_seconds <= 30.0
    assert type(second.value) is RateLimitedError


def test_regional_timeout_and_5xx_log_status_type_and_snippet_without_headers() -> None:
    def regional(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            451,
            headers={"X-Api-Key": "headersecret", "Retry-After": "9"},
            json={"msg": "restricted region", "api_key": "supersecretvalue"},
        )

    def server_error(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream unavailable " + ("x" * 400))

    def timed_out(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out talking to fapi")

    with capture_logs() as regional_logs:
        client = _client(regional)
        with pytest.raises(RegionalProviderFailureError):
            client.get_json("/fapi/v1/ping")
        client.close()
    regional_event = next(
        item for item in regional_logs if item["event"] == "perpetual_http_failure"
    )
    assert regional_event["http_status"] == 451
    assert regional_event["exception_type"] == "HTTPStatusError"
    assert regional_event["body_snippet"] == "[redacted]"
    assert regional_event["path"] == "/fapi/v1/ping"
    rendered = str(regional_logs)
    assert "supersecretvalue" not in rendered
    assert "headersecret" not in rendered
    assert "retry-after" not in rendered.lower()

    with capture_logs() as server_logs:
        client = _client(server_error)
        with pytest.raises(RegionalProviderFailureError, match="HTTP 500"):
            client.get_json("/fapi/v1/klines", {"symbol": "BTCUSDT", "interval": "15m", "limit": 2})
        client.close()
    server_event = next(item for item in server_logs if item["event"] == "perpetual_http_failure")
    assert server_event["http_status"] == 500
    assert server_event["exception_type"] == "HTTPStatusError"
    assert server_event["body_snippet"].startswith("upstream unavailable")
    assert len(server_event["body_snippet"]) == 200

    with capture_logs() as timeout_logs:
        client = _client(timed_out)
        with pytest.raises(RegionalProviderFailureError, match="unreachable"):
            client.get_json("/fapi/v1/time")
        client.close()
    timeout_event = next(item for item in timeout_logs if item["event"] == "perpetual_http_failure")
    assert timeout_event["http_status"] is None
    assert timeout_event["exception_type"] == "ReadTimeout"
    assert "timed out" in timeout_event["body_snippet"]
    assert len(timeout_event["body_snippet"]) <= 200


def test_klines_are_cached_per_symbol_timeframe_and_concurrent_loads_share_one_read() -> None:
    rows = [_kline_row(TRIGGER_OPEN)]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/fapi/v1/klines"):
            calls["n"] += 1
            return httpx.Response(200, json=rows)
        return httpx.Response(404, json={"msg": "missing"})

    source = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    identity = first_slice_identity(
        timeframe=Timeframe.M15, replay=False, is_live=True, instrument=binance_usdm_btcusdt()
    )
    first = source.fetch_closed_ohlcv(
        identity=identity,
        instrument=binance_usdm_btcusdt(),
        timeframe=Timeframe.M15,
        min_final_bars=1,
        evaluated_at=EVALUATED_AT,
    )
    second = source.fetch_closed_ohlcv(
        identity=identity,
        instrument=binance_usdm_btcusdt(),
        timeframe=Timeframe.M15,
        min_final_bars=1,
        evaluated_at=EVALUATED_AT,
    )
    source.close()
    assert calls["n"] == 2
    assert first.content_hash == second.content_hash

    calls["n"] = 0
    entered = threading.Event()
    release = threading.Event()

    def slow(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/fapi/v1/klines"):
            calls["n"] += 1
            if calls["n"] == 1:
                entered.set()
                assert release.wait(2)
            return httpx.Response(200, json=rows)
        return httpx.Response(404, json={"msg": "missing"})

    shared = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=httpx.MockTransport(slow),
        max_retries=0,
    )
    found: list[str] = []

    def read() -> None:
        series = shared.fetch_closed_ohlcv(
            identity=identity,
            instrument=binance_usdm_btcusdt(),
            timeframe=Timeframe.M15,
            min_final_bars=1,
            evaluated_at=EVALUATED_AT,
        )
        found.append(series.content_hash)

    leader = threading.Thread(target=read)
    follower = threading.Thread(target=read)
    leader.start()
    assert entered.wait(2)
    follower.start()
    threading.Event().wait(0.05)
    assert calls["n"] == 1
    release.set()
    leader.join(2)
    follower.join(2)
    shared.close()
    assert calls["n"] == 2
    assert found == [first.content_hash, first.content_hash]


def test_quote_cache_reuses_one_fetch_until_the_ttl() -> None:
    clock = {"t": 0.0}
    price_trade = trade(
        sequence=7,
        price="100000",
        quantity="0.01",
        buyer_is_maker=False,
        event_time=EVALUATED_AT - timedelta(seconds=1),
    )

    class _Quotes:
        kind = ProviderKind.MARKET_DATA
        name = "binance-usdm-perpetual"

        def __init__(self) -> None:
            self.calls = 0
            self.public_quote_cache = ShortTtlSingleflight(
                ttl_seconds=10.0,
                max_entries=4,
                clock=lambda: clock["t"],
            )

        def fetch_ordered_trades(self, **_kwargs: object) -> SimpleNamespace:
            self.calls += 1
            return SimpleNamespace(trades=[price_trade])

    source = _Quotes()
    identity = first_slice_identity(
        timeframe=Timeframe.M15, replay=False, is_live=True, instrument=binance_usdm_btcusdt()
    )
    first = quote_current_price(
        source,  # type: ignore[arg-type]
        identity=identity,
        instrument=binance_usdm_btcusdt(),
        evaluated_at=EVALUATED_AT,
        connection_id=CONNECTION,
        replay=False,
    )
    second = quote_current_price(
        source,  # type: ignore[arg-type]
        identity=identity,
        instrument=binance_usdm_btcusdt(),
        evaluated_at=EVALUATED_AT + timedelta(seconds=1),
        connection_id=CONNECTION,
        replay=False,
    )
    assert source.calls == 1
    assert second.price == first.price
    assert second.freshness.evaluated_at == EVALUATED_AT + timedelta(seconds=1)
    clock["t"] = 11.0
    quote_current_price(
        source,  # type: ignore[arg-type]
        identity=identity,
        instrument=binance_usdm_btcusdt(),
        evaluated_at=EVALUATED_AT + timedelta(seconds=2),
        connection_id=CONNECTION,
        replay=False,
    )
    assert source.calls == 2


def test_primary_ban_status_does_not_probe_binance_again_or_bybit() -> None:
    seen = {"binance": 0, "bybit": 0}

    def binance(_request: httpx.Request) -> httpx.Response:
        seen["binance"] += 1
        return httpx.Response(418, headers={"Retry-After": "120"}, json={"msg": "banned"})

    def bybit(_request: httpx.Request) -> httpx.Response:
        seen["bybit"] += 1
        return httpx.Response(500, json={"retCode": 10001})

    primary = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        timeout_seconds=2.0,
        transport=httpx.MockTransport(binance),
        max_retries=3,
    )
    secondary = BybitUsdtPerpetualSource(
        base_url="https://api.bybit.com",
        timeout_seconds=2.0,
        transport=httpx.MockTransport(bybit),
        max_retries=3,
    )
    router = FailoverPerpetualSource(
        primary,
        secondary,
        primary_instrument=binance_usdm_btcusdt(),
        secondary_instrument=bybit_usdt_perpetual_btcusdt(),
    )
    identity = first_slice_identity(
        timeframe=Timeframe.M15, replay=False, is_live=True, instrument=binance_usdm_btcusdt()
    )
    with pytest.raises(EvidenceSourceSwitchRequiredError):
        router.fetch_closed_ohlcv(
            identity=identity,
            instrument=binance_usdm_btcusdt(),
            timeframe=Timeframe.M15,
            min_final_bars=1,
            evaluated_at=EVALUATED_AT,
        )
    status = router.status()
    assert router.try_recover_primary() is None
    primary.close()
    secondary.close()
    assert seen == {"binance": 1, "bybit": 0}
    assert status.health is ProviderHealth.DEGRADED
    assert status.using_fallback is False
    assert status.is_mock is False
    assert "banned" in (status.detail or "").lower()
    assert "bybit" in (status.detail or "").lower()
    assert router.using_secondary is True


def test_banned_source_status_is_degraded_without_a_second_request() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(418, headers={"Retry-After": "90"}, json={"msg": "banned"})

    source = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    first = source.status()
    second = source.status()
    source.close()
    assert calls["n"] == 1
    assert first.health is ProviderHealth.DEGRADED
    assert second.health is ProviderHealth.DEGRADED
    assert second.using_fallback is False
    assert "spot fallback" in (second.detail or "").lower()


def test_readiness_names_a_degraded_primary_without_counting_it_unavailable() -> None:
    statuses = [
        ProviderStatus(
            name="binance-usdm-perpetual",
            kind=ProviderKind.MARKET_DATA,
            health=ProviderHealth.DEGRADED,
            using_fallback=False,
            is_mock=False,
            detail="Binance USD-M temporarily banned this client; no spot fallback.",
        ),
        ProviderStatus(
            name="mock-llm",
            kind=ProviderKind.LLM,
            health=ProviderHealth.HEALTHY,
            is_mock=True,
        ),
    ]
    ready, label, unavailable = readiness_projection(statuses, fail_closed=False)
    assert ready is True
    assert label == "degraded"
    assert unavailable == 0


def test_singleflight_loader_runs_once() -> None:
    started = threading.Event()
    release = threading.Event()
    calls = {"n": 0}
    cache = ShortTtlSingleflight(ttl_seconds=10.0, max_entries=4)

    def loader() -> dict[str, bool]:
        calls["n"] += 1
        started.set()
        assert release.wait(2)
        return {"ok": True}

    results: list[dict[str, bool]] = []

    def run() -> None:
        results.append(cache.get_or_load("btc-15m", loader))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert started.wait(2)
    release.set()
    for thread in threads:
        thread.join(2)
    assert calls["n"] == 1
    assert results == [{"ok": True}, {"ok": True}]
    assert cache.get("btc-15m") == {"ok": True}
