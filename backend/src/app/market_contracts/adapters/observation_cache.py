"""Bounded public snapshot reuse with causal admission and one collector at a time."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from typing import cast

from app.market_contracts.adapters.aggtrade_cache import CacheKey, TtlValueCache
from app.market_contracts.adapters.request_budget import record_cache_hit
from app.market_contracts.derivatives import DerivativeObservation
from app.market_contracts.order_book import OrderBookObservation
from app.schemas.nested_continuation import EvidenceAvailability


class CausalObservationCache:
    """Public facts only; tenant assessments never enter this process cache.

    TTL is two seconds, independent of consumer freshness. Acquisition time is
    retained, so a later fetch cannot be replayed into an earlier evaluation.
    Failed reads are not cached and cannot replace a missing value with a zero.
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._values = TtlValueCache(max_entries=64, ttl_seconds=2, clock=clock)
        self._guard = threading.Lock()

    def collect[T: DerivativeObservation | OrderBookObservation](
        self, key: CacheKey, observed_at: datetime, loader: Callable[[], T]
    ) -> T:
        with self._guard:
            item = self._values.get(key)
            if (
                item is not None
                and item.collected_at is not None
                and item.collected_at <= observed_at
            ):
                record_cache_hit()
                return cast(T, item)
            item = loader()
            if item.availability in {EvidenceAvailability.AVAILABLE, EvidenceAvailability.STALE}:
                self._values.put(key, item)
            return item

    def drop_symbol(self, symbol: str) -> None:
        with self._guard:
            self._values.drop_symbol(symbol)
