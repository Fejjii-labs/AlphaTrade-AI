"""Transient DB errors retry a cycle in place, only for audited idempotent components."""

from __future__ import annotations

import threading
import time

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, PendingRollbackError

from app.workers import paper_worker
from app.workers.paper_worker import (
    ComponentHealth,
    CycleOutcome,
    PaperWorkerSupervisor,
    is_transient_db_error,
)


class _InstantStop(threading.Event):
    """Stop event whose backoff wait returns at once and records each delay."""

    def __init__(self) -> None:
        super().__init__()
        self.waits: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout)
        return self.is_set()


def _operational() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("server closed the connection"))


def test_transient_db_error_classification() -> None:
    invalidated = DBAPIError("SELECT 1", {}, Exception("x"), connection_invalidated=True)
    wrapped = RuntimeError("scan failed")
    wrapped.__cause__ = PendingRollbackError("rollback first")
    assert is_transient_db_error(_operational())
    assert is_transient_db_error(invalidated)
    assert is_transient_db_error(wrapped)
    assert not is_transient_db_error(DBAPIError("SELECT 1", {}, Exception("x")))
    assert not is_transient_db_error(IntegrityError("INSERT", {}, Exception("dup")))
    assert not is_transient_db_error(ValueError("bad"))


def test_allowlist_is_exact() -> None:
    assert frozenset({"activity", "indexing"}) == paper_worker._DB_RETRY_COMPONENTS


def _supervisor(
    cycle: object, *, component: str = "activity"
) -> tuple[PaperWorkerSupervisor, _InstantStop]:
    """``cycle`` runs as ``component``; every other component is a no-op.

    The backoff tuple is not patched: the stop event's wait returns at once,
    so the real bound and the real delays are what the tests observe.
    """

    idle = lambda: CycleOutcome("disarmed")  # noqa: E731
    supervisor = PaperWorkerSupervisor(
        watcher_cycle=cycle if component == "watcher" else idle,  # type: ignore[arg-type]
        telegram_cycle=cycle if component == "telegram" else idle,  # type: ignore[arg-type]
    )
    stop = _InstantStop()
    supervisor._stop = stop
    if component == "activity":
        supervisor.attach_activity(cycle, poll_seconds=1.0, request_stop=lambda: None)  # type: ignore[arg-type]
    elif component == "indexing":
        supervisor.attach_indexing(cycle, close=lambda: None)  # type: ignore[arg-type]
    elif component == "exit_monitor":
        supervisor.attach_exit_monitor(cycle, poll_seconds=1.0, request_stop=lambda: None)  # type: ignore[arg-type]
    elif component == "demo_snapshot":
        supervisor.attach_demo_snapshot(cycle, poll_seconds=1.0, request_stop=lambda: None)  # type: ignore[arg-type]
    return supervisor, stop


def _health(supervisor: PaperWorkerSupervisor, component: str) -> ComponentHealth:
    health = getattr(supervisor.run_round(), component)
    assert health is not None
    return health


@pytest.mark.parametrize("component", ["activity", "indexing"])
def test_allowlisted_component_retries_then_succeeds(component: str) -> None:
    calls: list[int] = []

    def cycle() -> CycleOutcome:
        calls.append(1)
        if len(calls) < 3:
            raise _operational()
        return CycleOutcome("running")

    supervisor, stop = _supervisor(cycle, component=component)
    health = _health(supervisor, component)
    assert len(calls) == 3
    assert stop.waits == [0.5, 2.0]
    assert (health.status, health.restart_count) == ("running", 0)


@pytest.mark.parametrize("component", ["activity", "indexing"])
def test_retry_bound_is_one_attempt_plus_two_retries(component: str) -> None:
    """The bound itself, not a patched tuple: exactly 1 + 2 attempts, then failed."""

    calls: list[int] = []

    def cycle() -> CycleOutcome:
        calls.append(1)
        raise _operational()

    supervisor, stop = _supervisor(cycle, component=component)
    health = _health(supervisor, component)
    assert len(calls) == 1 + 2
    assert stop.waits == [0.5, 2.0]
    assert sum(w or 0.0 for w in stop.waits) <= 2.5
    assert (health.status, health.restart_count) == ("failed", 1)


def test_authority_is_reverted_before_each_retry() -> None:
    """A failed attempt that drifted authority is restored before the next attempt runs."""

    state = {"armed": False}
    seen_at_entry: list[bool] = []
    restores: list[int] = []

    def restore() -> None:
        restores.append(1)
        state["armed"] = False

    def cycle() -> CycleOutcome:
        seen_at_entry.append(state["armed"])
        if len(seen_at_entry) < 3:
            state["armed"] = True  # drift, then a transient DB error
            raise _operational()
        return CycleOutcome("running")

    supervisor, _stop = _supervisor(cycle)
    assert supervisor._activity is not None
    supervisor._activity.guard = paper_worker._AuthorityGuard(
        probe=lambda: (state["armed"],), restore=restore, baseline=(False,)
    )
    health = _health(supervisor, "activity")
    assert seen_at_entry == [False, False, False]
    assert len(restores) == 2
    assert (health.status, health.restart_count) == ("running", 0)
    assert supervisor.snapshot().authority_intact is True


def test_stop_during_backoff_ends_retries() -> None:
    """A real 0.5 s backoff is cut short by request_stop; no further attempt runs."""

    calls: list[int] = []

    def cycle() -> CycleOutcome:
        calls.append(1)
        raise _operational()

    supervisor = PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome("disarmed"),
        telegram_cycle=lambda: CycleOutcome("disarmed"),
    )
    supervisor.attach_activity(cycle, poll_seconds=1.0, request_stop=lambda: None)
    component = supervisor._activity
    assert component is not None
    timer = threading.Timer(0.05, supervisor.request_stop)
    started = time.monotonic()
    timer.start()
    try:
        with pytest.raises(OperationalError):
            supervisor._cycle_with_db_retry(component)
    finally:
        timer.cancel()
    assert len(calls) == 1
    assert time.monotonic() - started < 0.45


def test_non_transient_error_is_not_retried() -> None:
    calls: list[int] = []

    def cycle() -> CycleOutcome:
        calls.append(1)
        raise ValueError("risk refused")

    supervisor, stop = _supervisor(cycle)
    assert _health(supervisor, "activity").status == "failed"
    assert len(calls) == 1
    assert stop.waits == []


@pytest.mark.parametrize("component", ["watcher", "telegram", "exit_monitor", "demo_snapshot"])
def test_side_effect_component_is_not_retried(component: str) -> None:
    """Order, claim, journal, kill-switch and message paths fail once, as before."""

    calls: list[int] = []

    def cycle() -> CycleOutcome:
        calls.append(1)
        raise _operational()

    supervisor, stop = _supervisor(cycle, component=component)
    health = _health(supervisor, component)
    assert len(calls) == 1
    assert stop.waits == []
    assert (health.status, health.restart_count) == ("failed", 1)


def test_unknown_component_defaults_to_no_retry() -> None:
    supervisor, stop = _supervisor(lambda: CycleOutcome("disarmed"), component="watcher")
    calls: list[int] = []

    def cycle() -> CycleOutcome:
        calls.append(1)
        raise _operational()

    component = paper_worker._Component("new_component", cycle, None)
    with pytest.raises(OperationalError):
        supervisor._cycle_with_db_retry(component)
    assert len(calls) == 1
    assert stop.waits == []
    assert component.name not in paper_worker._DB_RETRY_COMPONENTS
