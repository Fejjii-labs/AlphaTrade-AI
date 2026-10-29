"""Provider registry: a single place to register and inspect providers.

Registers mock providers by default. When credentials and ``provider_mode`` allow,
real OpenAI, Qdrant, and Redis integrations are registered with explicit fallback
reporting via :meth:`ProviderStatus.using_fallback`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import wait as futures_wait
from datetime import UTC, datetime
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
    #: Entries older than this multiple of the TTL are reported as stale/unknown.
    STALE_FACTOR: float = 3.0
    #: Upper bound for a single provider probe during a cache refresh.
    PROBE_TIMEOUT_SECONDS: float = 10.0

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._providers: dict[str, Provider] = {}
        self._clock = clock
        self._cache_lock = threading.Lock()
        self._cached: list[ProviderStatus] | None = None
        self._cached_at: float | None = None
        self._refreshing = False
        self._refresh_started_at: float | None = None
        self._cached_wall: datetime | None = None
        self._cold_done = threading.Event()
        self._cold_done.set()
        #: Incremented per started refresh; only the owning run clears the flag.
        self._refresh_gen = 0
        self._written_gen = 0
        #: In-flight probe per provider: each probe runs on its own *daemon* thread
        #: and a hung probe is never resubmitted, so there is at most one probe
        #: thread per provider and a hung probe can never block interpreter exit.
        self._inflight: dict[str, Future] = {}
        self._probe_lock = threading.Lock()
        self._tls = threading.local()

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
        took 2-13 s and blocked the event loop. Only one caller per process
        probes on a cold cache (single-flight); afterwards a stale cache is
        returned immediately and a single background thread refreshes it.
        Entries older than ``STALE_FACTOR`` x TTL are never presented as
        healthy: they are downgraded and flagged as stale/unknown.
        """
        return self.cached_snapshot(ttl_seconds=ttl_seconds, background=background)[0]

    def cached_snapshot(
        self, *, ttl_seconds: float | None = None, background: bool = True
    ) -> tuple[list[ProviderStatus], datetime | None, float | None, bool]:
        """Return ``(statuses, checked_at, age_seconds, stale)`` from the cache."""
        ttl = self.STATUS_CACHE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        leader_cold = False
        with self._cache_lock:
            now = self._clock()
            cached, cached_at = self._cached, self._cached_at
            # A refresher that outlived every probe timeout is presumed dead.
            if (
                self._refreshing
                and self._refresh_started_at is not None
                and now - self._refresh_started_at > self._refresh_deadline()
            ):
                self._refreshing = False
            fresh = cached is not None and cached_at is not None and now - cached_at < ttl
            start_refresh = False
            gen = 0
            if not fresh and not self._refreshing and (cached is None or background):
                self._refreshing = True
                self._refresh_started_at = now
                self._refresh_gen += 1
                gen = self._refresh_gen
                if cached is None:
                    self._cold_done.clear()
                    leader_cold = True
                else:
                    start_refresh = True
        if cached is None:
            if leader_cold:
                self._run_refresh(gen)
            else:
                self._cold_done.wait(self._refresh_deadline())
            return self._snapshot_now(ttl)
        if start_refresh:
            threading.Thread(
                target=self._run_refresh,
                args=(gen,),
                name="provider-status-refresh",
                daemon=True,
            ).start()
        elif not fresh and not background:
            self.refresh_statuses()
        return self._snapshot_now(ttl)

    def _refresh_deadline(self) -> float:
        return self.PROBE_TIMEOUT_SECONDS + 5.0

    def _snapshot_now(
        self, ttl: float
    ) -> tuple[list[ProviderStatus], datetime | None, float | None, bool]:
        with self._cache_lock:
            cached, cached_at, wall = self._cached, self._cached_at, self._cached_wall
            now = self._clock()
        if cached is None or cached_at is None:
            # Cold probe failed/timed out entirely: report unknown, not healthy.
            return (
                [
                    ProviderStatus(
                        name=p.name,
                        kind=p.kind,
                        health=ProviderHealth.UNAVAILABLE,
                        detail="Status unknown: provider check has not completed.",
                    )
                    for p in self._providers.values()
                ],
                None,
                None,
                True,
            )
        age = max(0.0, now - cached_at)
        stale = age > self.STALE_FACTOR * ttl
        if not stale:
            return list(cached), wall, age, False
        detail = f"Status unknown: last checked {int(age)}s ago."
        marked = [
            s.model_copy(
                update={
                    "health": (
                        ProviderHealth.DEGRADED if s.health is ProviderHealth.HEALTHY else s.health
                    ),
                    "detail": detail,
                }
            )
            for s in cached
        ]
        return marked, wall, age, True

    def refresh_statuses(self) -> list[ProviderStatus]:
        """Probe all providers concurrently under ONE overall deadline and cache it.

        The whole refresh is bounded by ``PROBE_TIMEOUT_SECONDS`` (not per
        provider), so it always finishes before the dead-refresher deadline.
        Providers that have not answered by then are reported UNAVAILABLE.
        A provider whose previous probe is still hung is not probed again, so
        hung probes cannot pile up threads.
        """
        providers = list(self._providers.values())
        submitted: list[tuple[Provider, Future | None]] = []
        with self._probe_lock:
            for p in providers:
                prev = self._inflight.get(p.name)
                if prev is not None and not prev.done():
                    submitted.append((p, None))
                    continue
                fut = self._spawn_probe(p)
                self._inflight[p.name] = fut
                submitted.append((p, fut))
        live = [f for _, f in submitted if f is not None]
        if live:
            futures_wait(live, timeout=self.PROBE_TIMEOUT_SECONDS)
        results = [self._collect(p, f) for p, f in submitted]
        _gen: int | None = getattr(self._tls, "gen", None)
        with self._cache_lock:
            # A late, older refresh must not overwrite a newer one's result.
            if _gen is None or _gen >= self._written_gen:
                if _gen is not None:
                    self._written_gen = _gen
                self._cached = list(results)
                self._cached_at = self._clock()
                self._cached_wall = datetime.now(UTC)
        return results

    @classmethod
    def _spawn_probe(cls, provider: Provider) -> Future:
        """Run one probe on a daemon thread (no executor: its atexit join would
        block shutdown on a hung probe)."""
        fut: Future = Future()
        fut.set_running_or_notify_cancel()

        def _target() -> None:
            try:
                fut.set_result(cls._probe_inline(provider))
            except BaseException as exc:
                fut.set_exception(exc)

        threading.Thread(
            target=_target, name=f"provider-probe-{provider.name}", daemon=True
        ).start()
        return fut

    @staticmethod
    def _probe_inline(provider: Provider) -> ProviderStatus:
        return provider.status()

    def _collect(self, provider: Provider, future: Future | None) -> ProviderStatus:
        if future is None:
            logger.warning("provider_status_probe_still_running", provider=provider.name)
            detail = "Status probe timed out (previous probe still running)."
        elif not future.done():
            logger.warning("provider_status_timeout", provider=provider.name)
            detail = "Status probe timed out."
        else:
            try:
                return future.result(timeout=0)
            except Exception:
                logger.error("provider_status_failed", provider=provider.name, exc_info=True)
                detail = "Status probe raised an exception."
        return ProviderStatus(
            name=provider.name,
            kind=provider.kind,
            health=ProviderHealth.UNAVAILABLE,
            detail=detail,
        )

    def _run_refresh(self, gen: int | None = None) -> None:
        self._tls.gen = gen
        try:
            self.refresh_statuses()
        except Exception:
            logger.error("provider_status_refresh_failed", exc_info=True)
        finally:
            with self._cache_lock:
                # Only the run that owns the current generation clears the flag.
                if gen is None or gen == self._refresh_gen:
                    self._refreshing = False
                    self._refresh_started_at = None
            self._tls.gen = None
            self._cold_done.set()

    # Backwards-compatible name.
    _background_refresh = _run_refresh

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
    from app.market_monitor.persisted import (
        PersistedPerpetualEvidenceStatus,
        api_monitor_runs_in_process,
    )

    if api_monitor_runs_in_process(settings):
        registry.register(_PerpetualEvidenceAdapter(perpetual_evidence))
    else:
        # The paper worker probes the venue; the API reports its persisted view.
        registry.register(
            PersistedPerpetualEvidenceStatus(
                name=perpetual_evidence.name, kind=perpetual_evidence.kind, settings=settings
            )
        )

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
