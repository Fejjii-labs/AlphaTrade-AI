"""Dashboard load performance: cached provider status, non-blocking routes."""

from __future__ import annotations

import asyncio
import inspect
import threading
import time

from app.api.routes import canonical as canonical_routes
from app.api.routes import market_watcher as mw_routes
from app.api.routes import providers as provider_routes
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.providers.registry import ProviderRegistry
from app.services.watcher_monitoring_service import WatcherMonitoringService


class _CountingProvider:
    def __init__(self, name: str = "probe", delay: float = 0.0) -> None:
        self.name = name
        self.kind = ProviderKind.LLM
        self.calls = 0
        self.delay = delay
        self.release = threading.Event()
        self.release.set()

    def status(self) -> ProviderStatus:
        self.calls += 1
        self.release.wait(5)
        if self.delay:
            time.sleep(self.delay)
        return ProviderStatus(
            name=self.name, kind=self.kind, health=ProviderHealth.HEALTHY, detail=str(self.calls)
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _registry() -> tuple[ProviderRegistry, _CountingProvider, _Clock]:
    clock = _Clock()
    reg = ProviderRegistry(clock=clock)
    provider = _CountingProvider()
    reg.register(provider)
    return reg, provider, clock


def test_cached_statuses_probe_once_within_ttl() -> None:
    reg, provider, clock = _registry()
    for _ in range(5):
        assert reg.cached_statuses(ttl_seconds=45)[0].name == "probe"
        clock.now += 5
    assert provider.calls == 1


def test_stale_cache_served_immediately_and_refreshed_in_background() -> None:
    reg, provider, clock = _registry()
    reg.cached_statuses(ttl_seconds=45)
    clock.now += 60
    provider.release.clear()  # the next live probe would hang
    started = time.monotonic()
    stale = reg.cached_statuses(ttl_seconds=45)
    assert time.monotonic() - started < 0.5
    assert stale[0].detail == "1"
    # a second stale read does not start another refresh
    reg.cached_statuses(ttl_seconds=45)
    provider.release.set()
    for _ in range(100):
        if provider.calls == 2 and not reg._refreshing:
            break
        time.sleep(0.02)
    assert provider.calls == 2
    assert reg.cached_statuses(ttl_seconds=45)[0].detail == "2"


def test_failing_probe_is_cached_as_unavailable_not_raised() -> None:
    reg = ProviderRegistry()

    class _Boom:
        name = "boom"
        kind = ProviderKind.LLM

        def status(self) -> ProviderStatus:
            raise RuntimeError("down")

    reg.register(_Boom())
    assert reg.cached_statuses()[0].health is ProviderHealth.UNAVAILABLE


def test_routes_are_sync_so_fastapi_runs_them_in_threadpool() -> None:
    assert not inspect.iscoroutinefunction(provider_routes.provider_status)
    assert not inspect.iscoroutinefunction(mw_routes.market_watcher_monitoring)
    assert not inspect.iscoroutinefunction(canonical_routes.get_canonical_market_status)


def test_provider_route_uses_cache_not_live_probe() -> None:
    reg, provider, _ = _registry()
    provider_routes.provider_status(reg)
    provider_routes.provider_status(reg)
    assert provider.calls == 1


def test_slow_provider_route_does_not_block_event_loop() -> None:
    """A cold-cache probe runs in the threadpool; other requests keep flowing."""
    import httpx
    from fastapi import FastAPI

    from app.core.dependencies import get_provider_registry

    slow = ProviderRegistry()
    slow.register(_CountingProvider(delay=1.0))
    app = FastAPI()
    app.include_router(provider_routes.router)
    app.dependency_overrides[get_provider_registry] = lambda: slow

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    async def run() -> float:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            slow_task = asyncio.create_task(client.get("/providers/status"))
            await asyncio.sleep(0.05)
            started = time.monotonic()
            resp = await client.get("/ping")
            elapsed = time.monotonic() - started
            assert resp.status_code == 200
            assert (await slow_task).status_code == 200
            return elapsed

    assert asyncio.run(run()) < 0.5


def test_monitoring_snapshot_does_not_force_monitor_tick() -> None:
    calls: list[dict[str, object]] = []

    class _Monitor:
        def snapshot(self, symbol: str, **kwargs: object) -> object:
            calls.append(kwargs)
            return object()

        def latest(self, symbol: str) -> object:  # pragma: no cover - must not be used
            raise AssertionError("latest() forces a tick")

    svc = WatcherMonitoringService.__new__(WatcherMonitoringService)
    svc._monitor = _Monitor()
    assert svc._monitor_snapshot() is not None
    assert calls == [{}]


def test_monitoring_provider_health_uses_cache() -> None:
    reg, provider, _ = _registry()
    svc = WatcherMonitoringService.__new__(WatcherMonitoringService)
    svc._providers = reg
    svc._provider_health()
    svc._provider_health()
    assert provider.calls == 1
