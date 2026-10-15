"""One Binance evidence budget and aggTrade cache per process configuration.

Canonical API reads must not construct a new budget or cache on each HTTP
request. The pool is in-memory: a process restart starts empty.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from app.core.config import Settings
from app.market_contracts.adapters.aggtrade_cache import ClosedAggTradeCache, TtlValueCache
from app.market_contracts.adapters.observation_cache import CausalObservationCache
from app.market_contracts.adapters.request_budget import SlidingWeightBudget


@dataclass(frozen=True, slots=True)
class _PoolKey:
    base_url: str
    weight_per_minute: int
    max_backoff_seconds: float
    cache_entries: int
    cache_ttl_seconds: float
    cache_max_rows: int


class BinanceEvidencePool:
    """Shared request-weight budget and closed-window cache."""

    def __init__(self, key: _PoolKey) -> None:
        self.observations = CausalObservationCache()
        self.budget = SlidingWeightBudget(
            limit=key.weight_per_minute,
            max_wait_seconds=max(key.max_backoff_seconds, 60.0),
        )
        self.cache = ClosedAggTradeCache(
            max_entries=key.cache_entries,
            ttl_seconds=key.cache_ttl_seconds,
            max_rows=key.cache_max_rows,
        )
        self.reduced = TtlValueCache(
            max_entries=key.cache_entries,
            ttl_seconds=key.cache_ttl_seconds,
        )


_POOLS: dict[_PoolKey, BinanceEvidencePool] = {}
_POOLS_GUARD = threading.Lock()


def shared_binance_evidence_pool(settings: Settings) -> BinanceEvidencePool:
    """Return the process pool for this Binance evidence configuration."""

    key = _PoolKey(
        base_url=settings.market_data_futures_base_url.rstrip("/"),
        weight_per_minute=settings.binance_request_weight_per_minute,
        max_backoff_seconds=settings.binance_request_max_backoff_seconds,
        cache_entries=settings.binance_evidence_cache_entries,
        cache_ttl_seconds=settings.binance_evidence_cache_ttl_seconds,
        cache_max_rows=settings.binance_evidence_cache_max_rows,
    )
    with _POOLS_GUARD:
        pool = _POOLS.get(key)
        if pool is None:
            pool = BinanceEvidencePool(key)
            _POOLS[key] = pool
        return pool


def reset_shared_binance_evidence_pools() -> None:
    """Drop process pools. Tests use this to simulate a restart."""

    with _POOLS_GUARD:
        _POOLS.clear()
