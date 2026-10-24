"""Chat market retrieval fails fast on Binance 429/418 and stays inside a budget.

Worker and Watcher scans are not covered here. Those clients still sleep on
Retry-After. Paper only. No network.
"""

from __future__ import annotations

import time
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.evidence_pipeline.service import CanonicalEvidenceService
from app.interactive_agent.canonical_market import CanonicalMarketStateError
from app.interactive_agent.chat_market import MARKET_DATA_UNAVAILABLE, ChatMarketQuoteReader
from app.interactive_agent.turn_runtime import PreparedQuote
from app.market_contracts.adapters import http as market_http
from app.market_contracts.adapters.evidence_pool import (
    reset_shared_binance_evidence_pools,
    shared_binance_evidence_pool,
)
from app.market_contracts.adapters.factory import resolve_chat_market_source
from app.market_contracts.adapters.http import ReadOnlyHttpGetClient
from app.market_contracts.errors import (
    RateLimitedError,
    RetrievalBudgetExceededError,
    UpstreamBanError,
)


def _client(handler, **kwargs: object) -> ReadOnlyHttpGetClient:
    return ReadOnlyHttpGetClient(
        base_url="https://fapi.binance.com",
        timeout_seconds=2.0,
        transport=httpx.MockTransport(handler),
        **kwargs,  # type: ignore[arg-type]
    )


def test_fail_fast_429_does_not_sleep_for_retry_after() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def limited(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "300"}, json={"msg": "slow"})

    client = _client(
        limited,
        max_retries=3,
        max_backoff_seconds=30.0,
        fail_fast_rate_limits=True,
        sleeper=sleeps.append,
    )
    with pytest.raises(RateLimitedError) as raised:
        client.get_json("/fapi/v1/ping")
    client.close()
    assert calls["n"] == 1
    assert sleeps == []
    assert raised.value.retry_after_seconds == 300


def test_fail_fast_418_does_not_sleep_or_retry() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def banned(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(418, headers={"Retry-After": "180"}, json={"msg": "banned"})

    client = _client(
        banned,
        max_retries=3,
        max_backoff_seconds=30.0,
        fail_fast_rate_limits=True,
        sleeper=sleeps.append,
    )
    with pytest.raises(UpstreamBanError) as raised:
        client.get_json("/fapi/v1/ping")
    client.close()
    assert calls["n"] == 1
    assert sleeps == []
    assert raised.value.retry_after_seconds == 180


def test_fail_fast_weight_wait_does_not_sleep() -> None:
    sleeps: list[float] = []

    def ok(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = _client(
        ok,
        weight_per_minute=1,
        fail_fast_rate_limits=True,
        sleeper=sleeps.append,
    )
    assert client.get_json("/fapi/v1/ping") == {"ok": True}
    with pytest.raises(RateLimitedError):
        client.get_json("/fapi/v1/time")
    client.close()
    assert sleeps == []


def test_request_timeout_is_capped_by_the_remaining_budget() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    client = _client(handler, deadline_monotonic=time.monotonic() + 0.05)
    try:
        assert client._request_timeout() <= 0.05
    finally:
        client.close()


def test_spent_retrieval_budget_does_not_send() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("request sent after the budget")

    client = _client(
        handler,
        fail_fast_rate_limits=True,
        deadline_monotonic=time.monotonic() - 1,
    )
    with pytest.raises(RetrievalBudgetExceededError, match="market data unavailable"):
        client.get_json("/fapi/v1/ping")
    client.close()


def test_chat_quote_that_finishes_after_the_deadline_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reader and HTTP client share one deadline clock; a late quote is dropped."""

    now = [1000.0]
    monkeypatch.setattr(market_http, "deadline_clock", lambda: now[0])

    class _Evidence:
        def read(self, *, organization_id: object, symbol: str = "BTCUSDT") -> object:
            del organization_id, symbol
            now[0] += 100.0
            raise CanonicalMarketStateError("unavailable", "reason=unavailable")

    reader = ChatMarketQuoteReader(
        Settings(agent_chat_market_retrieval_budget_seconds=15),
        uuid4(),
        evidence=_Evidence(),  # type: ignore[arg-type]
    )
    with pytest.raises(CanonicalMarketStateError) as raised:
        reader.quote("BTCUSDT")
    assert raised.value.availability == "unavailable"
    assert raised.value.reason == MARKET_DATA_UNAVAILABLE


def test_chat_quote_does_not_mask_programming_errors() -> None:
    class _Evidence:
        def read(self, *, organization_id: object, symbol: str = "BTCUSDT") -> object:
            del organization_id, symbol
            raise TypeError("bug")

    reader = ChatMarketQuoteReader(Settings(), uuid4(), evidence=_Evidence())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="bug"):
        reader.quote("BTCUSDT")


def test_bounded_get_stops_waiting_at_the_deadline() -> None:
    """A slow GET is cut at the remaining budget, not only per httpx phase."""

    def handler(_request: httpx.Request) -> httpx.Response:
        time.sleep(1.0)
        return httpx.Response(200, json={"ok": True})

    client = _client(
        handler,
        fail_fast_rate_limits=True,
        deadline_monotonic=market_http.deadline_clock() + 0.2,
    )
    started = time.monotonic()
    with pytest.raises(RetrievalBudgetExceededError):
        client.get_json("/fapi/v1/ping")
    assert time.monotonic() - started < 0.8
    client.close()


def test_chat_client_honors_a_process_wide_binance_ban() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.host)
        return httpx.Response(200, json={})

    settings = Settings(perpetual_evidence_source="binance_usdm")
    reset_shared_binance_evidence_pools()
    try:
        shared_binance_evidence_pool(settings).ban_gate.note("ban", 300)
        source = resolve_chat_market_source(
            settings,
            deadline_monotonic=market_http.deadline_clock() + 15,
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(UpstreamBanError):
                source._http.get_json("/fapi/v1/ping")  # type: ignore[union-attr]
        finally:
            source.close()  # type: ignore[union-attr]
        assert sent == []
    finally:
        reset_shared_binance_evidence_pools()


def test_chat_429_does_not_pause_the_shared_worker_gate() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "60"}, json={"msg": "limit"})

    settings = Settings(perpetual_evidence_source="binance_usdm")
    reset_shared_binance_evidence_pools()
    try:
        source = resolve_chat_market_source(
            settings,
            deadline_monotonic=market_http.deadline_clock() + 15,
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(RateLimitedError):
                source._http.get_json("/fapi/v1/ping")  # type: ignore[union-attr]
        finally:
            source.close()  # type: ignore[union-attr]
        assert shared_binance_evidence_pool(settings).ban_gate.remaining() is None
    finally:
        reset_shared_binance_evidence_pools()


def test_interactive_turn_quote_completes_fast_when_binance_is_rate_limited() -> None:
    """The run_interactive quote step (PreparedQuote over ChatMarketQuoteReader).

    Binance answers 418 and Bybit 429. The turn's prepared quote must settle in
    well under the 15 s budget and surface market data unavailable, after
    trying Bybit, instead of waiting out Retry-After.
    """

    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "fapi.binance.com":
            return httpx.Response(418, headers={"Retry-After": "600"}, json={"msg": "banned"})
        return httpx.Response(429, headers={"Retry-After": "180"}, json={"msg": "limit"})

    settings = Settings(
        perpetual_evidence_source="binance_usdm",
        perpetual_evidence_secondary_source="bybit_usdt_perpetual",
        agent_chat_market_retrieval_budget_seconds=15,
    )
    reset_shared_binance_evidence_pools()
    try:
        started = time.monotonic()
        prepared = PreparedQuote(
            ChatMarketQuoteReader(settings, uuid4(), transport=httpx.MockTransport(handler)),
            "BTCUSDT",
        )
        elapsed = time.monotonic() - started
    finally:
        reset_shared_binance_evidence_pools()
    assert elapsed < 3
    assert "api.bybit.com" in hosts
    with pytest.raises(CanonicalMarketStateError) as raised:
        prepared.quote("BTCUSDT")
    assert raised.value.availability == "unavailable"
    assert raised.value.reason == MARKET_DATA_UNAVAILABLE


def test_chat_quote_maps_rate_limit_to_market_data_unavailable() -> None:
    class _Evidence:
        def read(self, *, organization_id: object, symbol: str = "BTCUSDT") -> object:
            del organization_id, symbol
            raise RateLimitedError("limited", retry_after_seconds=120)

    reader = ChatMarketQuoteReader(
        Settings(),
        uuid4(),
        evidence=_Evidence(),  # type: ignore[arg-type]
    )
    with pytest.raises(CanonicalMarketStateError) as raised:
        reader.quote("BTCUSDT")
    assert raised.value.availability == "unavailable"
    assert raised.value.reason == MARKET_DATA_UNAVAILABLE


def test_chat_quote_tries_bybit_after_binance_429_without_sleeping() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(429, headers={"Retry-After": "180"}, json={"msg": "limit"})

    settings = Settings(
        perpetual_evidence_source="binance_usdm",
        perpetual_evidence_secondary_source="bybit_usdt_perpetual",
        agent_chat_market_retrieval_budget_seconds=15,
    )
    reader = ChatMarketQuoteReader(
        settings,
        uuid4(),
        transport=httpx.MockTransport(handler),
    )
    started = time.monotonic()
    with pytest.raises(CanonicalMarketStateError) as raised:
        reader.quote("BTCUSDT")
    elapsed = time.monotonic() - started
    assert raised.value.reason == MARKET_DATA_UNAVAILABLE
    assert elapsed < 3
    assert hosts.index("fapi.binance.com") < hosts.index("api.bybit.com")


def test_chat_reader_keeps_a_labeled_replay_reason() -> None:
    """A replay fixture that is not a live quote stays labeled. It is not an outage."""

    service = CanonicalEvidenceService(Settings(perpetual_evidence_source="replay"))
    reader = ChatMarketQuoteReader(Settings(), uuid4(), evidence=service)
    with pytest.raises(CanonicalMarketStateError) as raised:
        reader.quote("BTCUSDT")
    assert "replay_fixture" in raised.value.reason
    assert raised.value.reason != MARKET_DATA_UNAVAILABLE
