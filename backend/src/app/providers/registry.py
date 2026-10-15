"""Provider registry: a single place to register and inspect providers.

Registers mock providers by default. When credentials and ``provider_mode`` allow,
real OpenAI, Qdrant, and Redis integrations are registered with explicit fallback
reporting via :meth:`ProviderStatus.using_fallback`.
"""

from __future__ import annotations

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

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

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
    perpetual_evidence = resolve_perpetual_evidence_source(settings)
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
