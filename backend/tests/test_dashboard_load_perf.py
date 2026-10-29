"""Dashboard load performance: cached provider status, non-blocking routes."""

from __future__ import annotations

import asyncio
import inspect
import threading
import time

import pytest

from app.api.routes import canonical as canonical_routes
from app.api.routes import market_watcher as mw_routes
from app.api.routes import providers as provider_routes
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.providers.registry import ProviderRegistry
from app.services.watcher_monitoring_service import WatcherMonitoringService

# A refresh-under-lock regression deadlocks; fail fast instead of stalling CI.
pytestmark = pytest.mark.timeout(30)


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


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_health_ready_probes_live_not_cache() -> None:
    from app.api.routes import health as health_routes
    from app.core.config import get_settings

    reg, provider, _ = _registry()
    reg.cached_statuses()  # warm cache
    before = provider.calls
    asyncio.run(health_routes.readiness(reg, get_settings()))
    asyncio.run(health_routes.readiness(reg, get_settings()))
    assert provider.calls == before + 2


def test_refresh_flag_cleared_after_each_of_consecutive_stale_cycles() -> None:
    reg, provider, clock = _registry()
    reg.cached_statuses(ttl_seconds=45)
    for expected in (2, 3, 4):
        clock.now += 60
        reg.cached_statuses(ttl_seconds=45)
        assert _wait_until(lambda n=expected: provider.calls == n and not reg._refreshing)
    assert reg.cached_statuses(ttl_seconds=45)[0].detail == "4"


def test_refresh_flag_cleared_when_refresh_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    reg, provider, clock = _registry()
    reg.cached_statuses(ttl_seconds=45)

    def boom() -> list[ProviderStatus]:
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(reg, "refresh_statuses", boom)
    clock.now += 60
    reg.cached_statuses(ttl_seconds=45)
    assert _wait_until(lambda: not reg._refreshing)
    monkeypatch.undo()
    clock.now += 1
    reg.cached_statuses(ttl_seconds=45)
    assert _wait_until(lambda: provider.calls == 2 and not reg._refreshing)


def test_hung_probe_times_out_and_clears_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ProviderRegistry, "PROBE_TIMEOUT_SECONDS", 0.2)
    reg, provider, clock = _registry()
    reg.cached_statuses(ttl_seconds=45)
    provider.release.clear()  # every further probe hangs
    clock.now += 60
    reg.cached_statuses(ttl_seconds=45)
    assert _wait_until(lambda: not reg._refreshing, timeout=3)
    status = reg.cached_statuses(ttl_seconds=45)[0]
    assert status.health is ProviderHealth.UNAVAILABLE
    assert "timed out" in (status.detail or "")
    provider.release.set()


def test_old_healthy_cache_reported_stale_not_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    reg, _provider, clock = _registry()
    reg.cached_statuses(ttl_seconds=45)

    def boom() -> list[ProviderStatus]:
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(reg, "refresh_statuses", boom)
    clock.now += 60  # stale but within 3x TTL: still served as-is
    statuses, checked_at, age, stale = reg.cached_snapshot(ttl_seconds=45)
    assert not stale and statuses[0].health is ProviderHealth.HEALTHY
    assert checked_at is not None and age == pytest.approx(60)
    _wait_until(lambda: not reg._refreshing)
    clock.now += 1_000_000
    statuses, _, age, stale = reg.cached_snapshot(ttl_seconds=45)
    assert stale and age > 135
    assert statuses[0].health is not ProviderHealth.HEALTHY
    assert "unknown" in (statuses[0].detail or "").lower()


def test_provider_route_exposes_checked_at_and_age() -> None:
    reg, _, clock = _registry()
    first = provider_routes.provider_status(reg)
    assert first.checked_at is not None and first.age_seconds == 0 and not first.stale
    clock.now += 10
    second = provider_routes.provider_status(reg)
    assert second.checked_at == first.checked_at
    assert second.age_seconds == pytest.approx(10)


def test_cold_start_is_single_flight() -> None:
    reg = ProviderRegistry()
    provider = _CountingProvider(delay=0.3)
    reg.register(provider)
    results: list[list[ProviderStatus]] = []
    threads = [
        threading.Thread(target=lambda: results.append(reg.cached_statuses())) for _ in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert provider.calls == 1
    assert len(results) == 20
    assert all(r[0].health is ProviderHealth.HEALTHY for r in results)
