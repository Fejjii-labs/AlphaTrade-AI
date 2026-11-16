"""Simple TTL cache for market data (Redis when available, in-memory fallback)."""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import structlog

from app.core.config import Settings

logger = structlog.get_logger(__name__)


@dataclass
class CacheResult:
    hit: bool
    value: Any | None = None


class MarketDataCache:
    """Short-TTL cache for ticker and OHLCV responses."""

    def __init__(self, settings: Settings, *, max_entries: int = 128) -> None:
        if max_entries < 1:
            raise ValueError("market cache must keep at least one entry.")
        self._settings = settings
        self._max_entries = max_entries
        self._memory: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._memory_lock = threading.Lock()
        self._redis: Any | None = None
        self._using_redis = False

        if settings.market_data_cache_use_redis:
            try:
                import redis

                self._redis = redis.from_url(
                    settings.redis_url,
                    socket_connect_timeout=settings.redis_connect_timeout_seconds,
                    socket_timeout=settings.redis_connect_timeout_seconds,
                    decode_responses=True,
                )
                self._redis.ping()
                self._using_redis = True
            except Exception as exc:
                logger.info("market_cache_redis_unavailable", error=str(exc))

    @property
    def using_redis(self) -> bool:
        return self._using_redis

    def _cache_key(
        self,
        *,
        provider: str,
        exchange: str,
        symbol: str,
        data_type: str,
        timeframe: str | None = None,
    ) -> str:
        tf = timeframe or "none"
        return f"market:{provider}:{exchange}:{symbol}:{data_type}:{tf}"

    def get(self, key: str) -> CacheResult:
        if self._using_redis and self._redis is not None:
            try:
                raw = self._redis.get(key)
                if raw is not None:
                    return CacheResult(hit=True, value=json.loads(raw))
            except Exception as exc:
                logger.warning("market_cache_redis_get_failed", error=str(exc))

        with self._memory_lock:
            self._expire_memory()
            entry = self._memory.get(key)
            if entry is None:
                return CacheResult(hit=False)
            self._memory.move_to_end(key)
            _, payload = entry
        return CacheResult(hit=True, value=json.loads(payload))

    def set(self, key: str, value: Any, *, ttl_seconds: int) -> None:
        payload = json.dumps(value, default=str)
        if self._using_redis and self._redis is not None:
            try:
                self._redis.setex(key, ttl_seconds, payload)
                return
            except Exception as exc:
                logger.warning("market_cache_redis_set_failed", error=str(exc))

        with self._memory_lock:
            self._expire_memory()
            self._memory[key] = (time.monotonic() + ttl_seconds, payload)
            self._memory.move_to_end(key)
            while len(self._memory) > self._max_entries:
                self._memory.popitem(last=False)

    def _expire_memory(self) -> None:
        """Prune under the fallback lock, including keys never requested again."""

        now = time.monotonic()
        expired = [key for key, (expires_at, _) in self._memory.items() if now > expires_at]
        for key in expired:
            self._memory.pop(key, None)
