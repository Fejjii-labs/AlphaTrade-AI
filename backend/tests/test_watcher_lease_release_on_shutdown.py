"""Graceful shutdown hands the watcher lease over without waiting for the TTL.

Reproduces the staging rollover where the stopped worker's lease blocked the
new instance (``lease_held``) for ~94 s. Release is fenced on owner + token.
"""

from __future__ import annotations

import threading
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.watcher.memory import FakeClock, InMemoryWatcherStore
from tests.test_watcher_paper_runtime import (
    ORG,
    _runtime,
    _seed_approved_compiled,
    make_world,
    session_factory,
)

_ = session_factory


def _claim(store: InMemoryWatcherStore, clock: FakeClock, owner: str, org=None, scope="s"):
    return store.claim_lease(
        organization_id=org or ORG,
        scan_scope=scope,
        owner_id=owner,
        ttl_seconds=60,
        now=clock.now(),
    )


def test_store_release_only_by_owner_and_token() -> None:
    store, clock = InMemoryWatcherStore(), FakeClock()
    _ok, lease, _r = _claim(store, clock, "old")
    kw = {"organization_id": ORG, "scan_scope": "s", "now": clock.now()}
    assert (
        store.release_lease(owner_id="intruder", fencing_token=lease.fencing_token, **kw) is False
    )
    assert store.release_lease(owner_id="old", fencing_token=lease.fencing_token + 1, **kw) is False
    assert _claim(store, clock, "new")[2] == "lease_held"
    assert store.release_lease(owner_id="old", fencing_token=lease.fencing_token, **kw) is True
    ok, new_lease, reason = _claim(store, clock, "new")
    assert ok and reason == "claimed" and new_lease.fencing_token > lease.fencing_token
    # Stale owner can never release (or renew) the successor's lease.
    assert store.release_lease(owner_id="old", fencing_token=lease.fencing_token, **kw) is False
    assert store.get_lease(ORG, "s").owner_id == "new"


def test_release_of_missing_lease_is_noop() -> None:
    store, clock = InMemoryWatcherStore(), FakeClock()
    assert (
        store.release_lease(
            organization_id=uuid4(), scan_scope="x", owner_id="a", fencing_token=1, now=clock.now()
        )
        is False
    )


def _seeded(session_factory: sessionmaker[Session], **kw):
    with session_factory() as session:
        _seed_approved_compiled(session)
    store, clock = InMemoryWatcherStore(), FakeClock()
    return store, clock, kw


def test_handoff_without_release_is_blocked_until_ttl(
    session_factory: sessionmaker[Session],
) -> None:
    store, clock, _ = _seeded(session_factory)
    world = make_world()
    old, *_ = _runtime(
        session_factory,
        world=world,
        store=store,
        clock=clock,
        worker_id="old",
        lease_ttl_seconds=90,
    )
    scope = old.run_cycle().scans[0].scan_scope
    old.stop()  # stop alone (pre-fix behaviour) keeps the lease
    new, *_ = _runtime(
        session_factory,
        world=world,
        store=store,
        clock=clock,
        worker_id="new",
        lease_ttl_seconds=90,
    )
    report = new.run_cycle()
    assert report.scans[0].reason_code == "lease_held"
    assert store.get_lease(ORG, scope).owner_id == "old"


def test_graceful_release_lets_successor_claim_immediately(
    session_factory: sessionmaker[Session],
) -> None:
    store, clock, _ = _seeded(session_factory)
    world = make_world()
    old, _c, probe_old, _s = _runtime(
        session_factory,
        world=world,
        store=store,
        clock=clock,
        worker_id="old",
        lease_ttl_seconds=90,
    )
    scope = old.run_cycle().scans[0].scan_scope
    old.stop()
    assert old.release_held_leases() == 1
    new, _c, probe_new, _s = _runtime(
        session_factory,
        world=world,
        store=store,
        clock=clock,
        worker_id="new",
        lease_ttl_seconds=90,
    )
    report = new.run_cycle()  # no clock advance: no TTL wait
    assert report.scans[0].reason_code != "lease_held"
    assert store.get_lease(ORG, scope).owner_id == "new"
    # Old runtime fails closed after release: no further cycles, no re-claim.
    with pytest.raises(RuntimeError):
        old.run_cycle()
    assert old.release_held_leases() == 0
    assert store.get_lease(ORG, scope).owner_id == "new"
    assert probe_old.unused and probe_new.unused


def test_release_never_touches_a_lease_taken_over(session_factory: sessionmaker[Session]) -> None:
    store, clock, _ = _seeded(session_factory)
    world = make_world()
    old, *_ = _runtime(
        session_factory, world=world, store=store, clock=clock, worker_id="old", lease_ttl_seconds=1
    )
    scope = old.run_cycle().scans[0].scan_scope
    clock.advance(2)
    new, *_ = _runtime(
        session_factory,
        world=world,
        store=store,
        clock=clock,
        worker_id="new",
        lease_ttl_seconds=60,
    )
    new.run_cycle()
    before = store.get_lease(ORG, scope)
    assert before.owner_id == "new"
    assert old.release_held_leases() == 0
    assert store.get_lease(ORG, scope) == before


def test_release_store_error_fails_closed(session_factory: sessionmaker[Session]) -> None:
    store, clock, _ = _seeded(session_factory)
    rt, *_ = _runtime(
        session_factory, store=store, clock=clock, worker_id="old", lease_ttl_seconds=90
    )
    scope = rt.run_cycle().scans[0].scan_scope

    def boom(**_kw):
        raise RuntimeError("db down")

    store.release_lease = boom  # type: ignore[method-assign]
    assert rt.release_held_leases() == 0  # logged, not raised
    assert store.get_lease(ORG, scope).owner_id == "old"  # TTL expiry still applies


def test_release_waits_for_inflight_cycle_then_skips(
    session_factory: sessionmaker[Session],
) -> None:
    store, clock, _ = _seeded(session_factory)
    rt, *_ = _runtime(
        session_factory, store=store, clock=clock, worker_id="old", lease_ttl_seconds=90
    )
    scope = rt.run_cycle().scans[0].scan_scope
    rt._release_wait_seconds = 0.05
    rt._cycle_lock.acquire()  # simulate a cycle still running past join timeout
    try:
        assert rt.release_held_leases() == 0
    finally:
        rt._cycle_lock.release()
    assert store.get_lease(ORG, scope).owner_id == "old"
    assert rt.release_held_leases() == 1


def test_on_stop_order_is_stop_then_release_then_close() -> None:
    """L7/L8: release runs, and only after every runtime stop."""

    from app.workers.paper_worker import compose_watcher_on_stop

    order: list[str] = []
    on_stop = compose_watcher_on_stop(
        [lambda: order.append("stop-watcher"), lambda: order.append("stop-telegram")],
        [lambda: order.append("release") or 1],
        [lambda: order.append("close")],
    )
    on_stop()
    assert order == ["stop-watcher", "stop-telegram", "release", "close"]


def test_on_stop_release_error_still_closes() -> None:
    """L9: a failing release is logged; closers still run."""

    from app.workers.paper_worker import compose_watcher_on_stop

    order: list[str] = []

    def bad_release() -> int:
        order.append("release")
        raise RuntimeError("db down")

    on_stop = compose_watcher_on_stop(
        [lambda: order.append("stop")],
        [bad_release, lambda: order.append("release2") or 1],
        [lambda: order.append("close")],
    )
    on_stop()
    assert order == ["stop", "release", "release2", "close"]


def test_on_stop_raising_stop_still_releases_and_closes() -> None:
    from app.workers.paper_worker import compose_watcher_on_stop

    order: list[str] = []

    def bad_stop() -> None:
        order.append("stop")
        raise RuntimeError("stop failed")

    on_stop = compose_watcher_on_stop(
        [bad_stop], [lambda: order.append("release") or 1], [lambda: order.append("close")]
    )
    with pytest.raises(RuntimeError):
        on_stop()
    assert order == ["stop", "release", "close"]


def test_local_composition_releases_on_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """L7 at build level: supervisor.close() stops the runtime, then releases."""

    from app.core.config import Environment, get_settings
    from app.workers.paper_worker import build_paper_worker_supervisor

    order: list[str] = []

    class _Runtime:
        def run_cycle(self):
            raise AssertionError("not called")

        def wait_publishing_monitor(self, wait: float, stop: threading.Event) -> bool:
            return True

        def stop(self) -> None:
            order.append("stop")

        def release_held_leases(self) -> int:
            order.append("release")
            return 1

    class _Telegram:
        def run_cycle(self):
            raise AssertionError("not called")

        def stop(self) -> None:
            order.append("telegram-stop")

    settings = get_settings().model_copy(deep=True)
    settings.environment = Environment.LOCAL
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: object())
    monkeypatch.setattr(
        "app.workers.paper_worker.paper_runtime_enabled", lambda _s: True, raising=False
    )
    monkeypatch.setattr("app.workers.watcher_paper.paper_runtime_enabled", lambda _s: True)
    monkeypatch.setattr(
        "app.workers.watcher_paper.build_watcher_paper_runtime", lambda *_a, **_k: _Runtime()
    )
    monkeypatch.setattr(
        "app.telegram_activation.runtime.build_telegram_runtime", lambda *_a, **_k: _Telegram()
    )
    supervisor = build_paper_worker_supervisor(settings)
    supervisor.close()
    assert order == ["stop", "telegram-stop", "release"]


def test_skipped_release_still_refuses_later_cycles(
    session_factory: sessionmaker[Session],
) -> None:
    store, clock, _ = _seeded(session_factory)
    rt, *_ = _runtime(
        session_factory, store=store, clock=clock, worker_id="old", lease_ttl_seconds=90
    )
    rt.run_cycle()
    rt._release_wait_seconds = 0.05
    rt._cycle_lock.acquire()
    try:
        assert rt.release_held_leases() == 0
    finally:
        rt._cycle_lock.release()
    with pytest.raises(RuntimeError):
        rt.run_cycle()


def test_run_forever_releases_on_exit(session_factory: sessionmaker[Session]) -> None:
    store, clock, _ = _seeded(session_factory)
    rt, *_ = _runtime(
        session_factory, store=store, clock=clock, worker_id="old", lease_ttl_seconds=90
    )
    calls: list[str] = []
    rt.run_loop = lambda **_k: calls.append("loop") or 0  # type: ignore[method-assign]
    orig = rt.release_held_leases
    rt.release_held_leases = lambda: calls.append("release") or orig()  # type: ignore[method-assign]
    if threading.current_thread() is threading.main_thread():
        rt.run_forever()
        assert calls == ["loop", "release"]


# --- Store fencing guard (L5 memory, L6 Postgres) ---------------------------


def _memory_store():
    return InMemoryWatcherStore()


def _postgres_store():
    from app.persistence.composition import build_postgres_watcher_store
    from tests.support.postgres_persistence import persistence_session_factory

    return build_postgres_watcher_store(persistence_session_factory())


def _pg_param():
    from tests.support.postgres_persistence import requires_postgres

    return pytest.param(_postgres_store, id="postgres", marks=requires_postgres)


@pytest.mark.parametrize("make_store", [pytest.param(_memory_store, id="memory"), _pg_param()])
def test_release_fencing_guard(make_store) -> None:
    store, clock = make_store(), FakeClock()
    org, scope = uuid4(), "release-guard"

    def claim(owner: str):
        return store.claim_lease(
            organization_id=org, scan_scope=scope, owner_id=owner, ttl_seconds=60, now=clock.now()
        )

    def release(owner: str, token: int) -> bool:
        return store.release_lease(
            organization_id=org,
            scan_scope=scope,
            owner_id=owner,
            fencing_token=token,
            now=clock.now(),
        )

    assert release("a", 1) is False  # no row
    ok, lease, _ = claim("a")
    assert ok
    before = store.get_lease(org, scope)
    assert release("intruder", lease.fencing_token) is False
    assert release("a", lease.fencing_token + 1) is False
    assert release("a", lease.fencing_token - 1) is False
    after = store.get_lease(org, scope)
    assert after.expires_at == before.expires_at and after.owner_id == "a"
    assert claim("b")[2] == "lease_held"

    assert release("a", lease.fencing_token) is True
    assert release("a", lease.fencing_token) is False  # already expired
    released = store.get_lease(org, scope)
    assert (released.owner_id, released.fencing_token) == ("a", lease.fencing_token)
    ok_b, lease_b, reason = claim("b")
    assert ok_b and reason == "claimed" and lease_b.fencing_token > lease.fencing_token
    held = store.get_lease(org, scope)
    assert release("a", lease.fencing_token) is False  # stale owner, old token
    assert release("a", lease_b.fencing_token) is False  # stale owner, new token
    assert store.get_lease(org, scope) == held
