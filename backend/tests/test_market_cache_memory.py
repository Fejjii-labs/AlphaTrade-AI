"""Expired scan pages and fallback market keys must release their payloads."""

from __future__ import annotations

import weakref

import pytest

from app.core.config import Settings
from app.market_contracts.adapters.public_read_cache import ShortTtlSingleflight
from app.services.market_cache import MarketDataCache


class _Payload:
    pass


@pytest.mark.parametrize("operation", ["get", "get_or_load"])
def test_unrelated_read_releases_expired_payloads(operation: str) -> None:
    clock = [0.0]
    cache = ShortTtlSingleflight(ttl_seconds=10, clock=lambda: clock[0])
    old = _Payload()
    reference = weakref.ref(old)
    cache.get_or_load("old-page", lambda value=old: value)
    del old
    clock[0] = 5
    fresh = cache.get_or_load("fresh-page", _Payload)
    # LRU order is not expiry order: the old entry was read more recently.
    assert cache.get("old-page") is reference()
    clock[0] = 11
    if operation == "get":
        cache.get("new-page")
    else:
        cache.get_or_load("new-page", _Payload)
    assert reference() is None
    assert cache.get("fresh-page") is fresh


def test_expiry_during_loader_releases_other_pages_on_completion() -> None:
    clock = [0.0]
    cache = ShortTtlSingleflight(ttl_seconds=10, clock=lambda: clock[0])
    old = _Payload()
    reference = weakref.ref(old)
    cache.get_or_load("old-page", lambda value=old: value)
    del old

    def slow_load() -> str:
        clock[0] = 11
        return "new"

    assert cache.get_or_load("new-page", slow_load) == "new"
    assert reference() is None
    assert cache.get("new-page") == "new"


def test_request_cache_preserves_ttl_boundary() -> None:
    clock = [0.0]
    cache = ShortTtlSingleflight(ttl_seconds=10, clock=lambda: clock[0])
    cache.get_or_load("page", lambda: "value")
    clock[0] = 10
    assert cache.get("page") == "value"
    clock[0] += 0.001
    assert cache.get("page") is None


def test_market_fallback_evicts_least_recently_used_keys() -> None:
    cache = MarketDataCache(Settings(_env_file=None, market_data_cache_use_redis=False))
    for n in range(128):
        cache.set(str(n), {"close": n}, ttl_seconds=60)
    assert cache.get("0").value == {"close": 0}
    cache.set("next", {"close": 128}, ttl_seconds=60)
    assert not cache.get("1").hit
    assert cache.get("0").hit
    assert cache.get("next").value == {"close": 128}
    assert len(cache._memory) == 128


@pytest.mark.parametrize("operation", ["get", "set"])
def test_market_fallback_sweeps_expired_unrequested_keys(monkeypatch, operation: str) -> None:
    clock = [0.0]
    monkeypatch.setattr("app.services.market_cache.time.monotonic", lambda: clock[0])
    cache = MarketDataCache(Settings(_env_file=None, market_data_cache_use_redis=False))
    cache.set("old", {"close": 1}, ttl_seconds=10)
    cache.set("fresh", {"close": 2}, ttl_seconds=20)
    clock[0] = 10
    assert cache.get("old").hit
    clock[0] = 11
    if operation == "get":
        cache.get("unrelated")
    else:
        cache.set("unrelated", {"close": 3}, ttl_seconds=10)
    assert "old" not in cache._memory
    assert cache.get("fresh").value == {"close": 2}
