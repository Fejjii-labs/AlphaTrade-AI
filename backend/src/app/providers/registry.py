"""Provider registry: a single place to register and inspect providers.

Registers mock providers by default. When credentials and ``provider_mode`` allow,
real OpenAI, Qdrant, and Redis integrations are registered with explicit fallback
reporting via :meth:`ProviderStatus.using_fallback`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

import structlog

from app.core.config import Settings, get_settings
from app.market_contracts.adapters.factory import resolve_perpetual_evidence_source
from app.providers.base import (
    BaseMockProvider,
    Provider,
    ProviderHealth,
    ProviderKind,
    ProviderStatus,
)
from app.providers.billing.factory import resolve_billing_provider
from app.providers.email.factory import resolve_email_provider
from app.providers.embeddings import EmbeddingsProvider, MockEmbeddingsProvider
from app.providers.exchange.factory import resolve_exchange_provider
from app.providers.factory import resolve_market_data_provider, resolve_providers, should_use_qdrant
from app.providers.infrastructure import RedisInfrastructureProvider
from app.providers.llm import LLMProvider, MockLLMProvider
from app.providers.market_data import MarketDataProvider
from app.providers.qdrant import MockQdrantProvider, VectorStore

logger = structlog.get_logger(__name__)


class ProviderRegistry:
    """Holds providers keyed by name and aggregates their statuses."""

    #: Default freshness window for :meth:`cached_statuses` (seconds).
    STATUS_CACHE_TTL_SECONDS: float = 45.0

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._providers: dict[str, Provider] = {}
        self._clock = clock
        self._cache_lock = threading.Lock()
        self._cached: list[ProviderStatus] | None = None
        self._cached_at: float | None = None
        self._refreshing = False

    def register(self, provider: Provider) -> None:
        if provider.name in self._providers:
            raise ValueError(f"Provider already registered: {provider.name}")
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def all(self) -> list[Provider]:
        return list(self._providers.values())

    def statuses(self) -> list[ProviderStatus]:
        """Aggregate statuses, degrading gracefully if a probe misbehaves."""
        results: list[ProviderStatus] = []
        for provider in self._providers.values():
            try:
                results.append(provider.status())
            except Exception:  # defensive: status aggregation must never crash
                logger.error("provider_status_failed", provider=provider.name, exc_info=True)
                results.append(
                    ProviderStatus(
                        name=provider.name,
                        kind=provider.kind,
                        health=ProviderHealth.UNAVAILABLE,
                        detail="Status probe raised an exception.",
                    )
                )
        return results

    def cached_statuses(
        self, *, ttl_seconds: float | None = None, background: bool = True
    ) -> list[ProviderStatus]:
        """Serve provider statuses from a TTL cache (stale-while-revalidate).

        Request paths (dashboard, provider card) must not probe every provider
        live: probes are sequential network calls (OpenAI, Binance, ...) that
        took 2-13 s and blocked the event loop. Only the very first call in a
        process probes synchronously; afterwards a stale cache is returned
        immediately and a single background thread refreshes it.
        """
        ttl = self.STATUS_CACHE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        with self._cache_lock:
            cached, cached_at = self._cached, self._cached_at
            now = self._clock()
            fresh = cached is not None and cached_at is not None and now - cached_at < ttl
            if fresh:
                return list(cached or [])
            start_refresh = cached is not None and background and not self._refreshing
            if start_refresh:
                self._refreshing = True
        if cached is None:
            return self.refresh_statuses()
        if start_refresh:
            threading.Thread(
                target=self._background_refresh, name="provider-status-refresh", daemon=True
            ).start()
        elif not background:
            return self.refresh_statuses()
        return list(cached)

    def refresh_statuses(self) -> list[ProviderStatus]:
        """Probe all providers now and store the result in the cache."""
        results = self.statuses()
        with self._cache_lock:
            self._cached = list(results)
            self._cached_at = self._clock()
        return results

    def _background_refresh(self) -> None:
        try:
            self.refresh_statuses()
        except Exception:  # pragma: no cover - statuses() is already defensive
            logger.error("provider_status_refresh_failed", exc_info=True)
        finally:
            with self._cache_lock:
                self._refreshing = False

    def get_llm(self) -> LLMProvider:
        for provider in self._providers.values():
            if isinstance(provider, LLMProvider):
                return provider
        return MockLLMProvider()

    def get_embeddings(self) -> EmbeddingsProvider:
        for provider in self._providers.values():
            if isinstance(provider, EmbeddingsProvider):
                return provider
        return MockEmbeddingsProvider()


class _EmbeddingsProviderAdapter:
    """Wrap EmbeddingsProvider as a generic Provider for registry status."""

    def __init__(self, inner: EmbeddingsProvider) -> None:
        self._inner = inner
        self.name = inner.name
        self.kind = inner.kind

    def status(self) -> ProviderStatus:
        return self._inner.status()


class _LLMProviderAdapter:
    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.name = inner.name
        self.kind = inner.kind

    def status(self) -> ProviderStatus:
        return self._inner.status()


class _VectorStoreAdapter:
    def __init__(self, inner: VectorStore) -> None:
        self._inner = inner
        self.name = getattr(inner, "name", "qdrant")
        self.kind = ProviderKind.VECTOR

    def status(self) -> ProviderStatus:
        return self._inner.status()


class _EmailProviderAdapter:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.name = inner.name
        self.kind = inner.kind

    def status(self) -> ProviderStatus:
        return self._inner.status()


class _BillingProviderAdapter:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.name = inner.name
        self.kind = inner.kind

    def status(self) -> ProviderStatus:
        return self._inner.status()


class _MarketDataProviderAdapter:
    def __init__(self, inner: MarketDataProvider) -> None:
        self._inner = inner
        self.name = inner.name
        self.kind = inner.kind

    def status(self) -> ProviderStatus:
        return self._inner.status()


class _StatusReporter(Protocol):
    name: str
    kind: ProviderKind

    def status(self) -> ProviderStatus: ...


class _PerpetualEvidenceAdapter:
    """Status-only wrapper for the Phase 5 read-only perpetual evidence source."""

    def __init__(self, inner: _StatusReporter) -> None:
        self._inner = inner
        self.name = inner.name
        self.kind = inner.kind

    def status(self) -> ProviderStatus:
        return self._inner.status()


def build_default_registry(settings: Settings) -> ProviderRegistry:
    """Create the registry for the current settings."""
    registry = ProviderRegistry()
    resolved = resolve_providers(settings)

    registry.register(_LLMProviderAdapter(resolved.llm))
    registry.register(_EmbeddingsProviderAdapter(resolved.embeddings))

    if should_use_qdrant(settings):
        # Reuse the same store instance as RAG (auth + dimension config).
        registry.register(_VectorStoreAdapter(resolved.vector_store))
    else:
        registry.register(MockQdrantProvider(store=resolved.fallback_vector_store))

    registry.register(RedisInfrastructureProvider(settings))
    registry.register(_EmailProviderAdapter(resolve_email_provider(settings)))
    registry.register(_BillingProviderAdapter(resolve_billing_provider(settings)))
    market_data = resolve_market_data_provider(settings)
    registry.register(_MarketDataProviderAdapter(market_data))
    perpetual_evidence = resolve_perpetual_evidence_source(settings, shared=True)
    registry.register(_PerpetualEvidenceAdapter(perpetual_evidence))

    # Exchange: mock by default; BloFin demo (read-only) when explicitly enabled.
    resolved_exchange = resolve_exchange_provider(settings)
    registry.register(resolved_exchange.status_provider)
    if resolved_exchange.market_data is not None:
        registry.register(_MarketDataProviderAdapter(resolved_exchange.market_data))

    registry.register(BaseMockProvider("mock-news", ProviderKind.NEWS))
    registry.register(BaseMockProvider("mock-notifications", ProviderKind.NOTIFICATIONS))
    registry.register(BaseMockProvider("mock-tracing", ProviderKind.TRACING))
    return registry


def get_provider_registry() -> ProviderRegistry:
    """FastAPI dependency: return the process-wide provider registry."""
    global _registry
    if _registry is None:
        _registry = build_default_registry(get_settings())
    return _registry


_registry: ProviderRegistry | None = None
