"""Worker diagnostics distinguish transient RSS from retained structure growth."""

import threading
from types import SimpleNamespace

import pytest

from app.observability.process_memory import ProcessMemory
from app.observability.worker_memory import (
    CycleMemorySampler,
    WorkerMemoryDiagnostics,
    container_memory_fields,
)
from app.workers.paper_worker import CycleOutcome, PaperWorkerSupervisor


def test_sampled_peak_and_cleanup_growth_are_separate(monkeypatch):
    events = []
    logger = SimpleNamespace(info=lambda name, **fields: events.append((name, fields)))
    monkeypatch.setattr("app.observability.worker_memory.logger", logger)
    diagnostics = WorkerMemoryDiagnostics()
    sampler = CycleMemorySampler(read=lambda: ProcessMemory(100, 900))
    sampler.stop()
    sampler.sampled_peak = 500
    sampler.before = ProcessMemory(400, 900)
    for _ in range(40):
        diagnostics.finish("watcher", sampler, ProcessMemory(120, 900))
    fields = events[-1][1]
    assert fields["rss_sampled_peak_bytes"] == 500
    assert fields["rss_before_cleanup_bytes"] == 400
    assert fields["rss_after_cleanup_bytes"] == 120
    assert fields["rss_peak_bytes"] == 900
    assert fields["growth_window_cycles"] == 32
    assert fields["growth_bytes_per_cycle"] == 0
    assert not sampler._thread.is_alive()


@pytest.mark.parametrize("fail", [False, True])
def test_supervisor_stops_sampler_and_cleans_up_on_success_or_failure(fail, monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.observability.worker_memory.logger",
        SimpleNamespace(info=lambda name, **fields: events.append((name, fields))),
    )

    def watcher():
        if fail:
            raise RuntimeError("provider_unavailable")
        return CycleOutcome(status="running", record_scan=True)

    supervisor = PaperWorkerSupervisor(
        watcher_cycle=watcher,
        telegram_cycle=lambda: CycleOutcome(status="disarmed"),
        memory_diagnostics_enabled=True,
    )
    health = supervisor.run_round()
    assert health.watcher.status == ("failed" if fail else "running")
    assert health.telegram.status == "disarmed"
    cycles = [fields for name, fields in events if name == "paper_worker_memory_cycle"]
    assert {fields["component"] for fields in cycles} == {"watcher", "telegram"}
    assert all(fields["rss_after_cleanup_bytes"] > 0 for fields in cycles)
    assert not any(t.name == "paper-memory-sampler" for t in threading.enumerate())


def test_disarmed_diagnostics_do_not_change_authority(monkeypatch):
    from app.core.config import Settings
    from app.core.disarmed_worker_boot import (
        WorkerBootRole,
        bind_worker_boot_role,
        reset_disarmed_worker_boot,
    )
    from app.workers.paper_worker import build_paper_worker_supervisor
    from tests.test_disarmed_render_worker_boot import _CONTRACT

    monkeypatch.setattr(
        "app.workers.paper_worker._build_armed_supervisor",
        lambda *_: pytest.fail("Disarmed diagnostics must not open operational dependencies"),
    )
    bind_worker_boot_role(WorkerBootRole.PAPER_WORKER)
    try:
        settings = Settings(
            _env_file=None, **_CONTRACT, paper_worker_memory_diagnostics_enabled=True
        )
        health = build_paper_worker_supervisor(settings).run_round()
    finally:
        reset_disarmed_worker_boot()
    assert health.watcher.status == health.telegram.status == "disarmed"
    assert health.authority_intact
    assert health.telegram.last_delivery_at is None
    assert settings.enable_real_trading is False
    assert settings.telegram_network_permitted is False


@pytest.mark.parametrize("raw_limit,expected", [("max", None), ("536870912", 536870912)])
def test_cgroup_limit_is_distinct_from_process_rss(raw_limit, expected, monkeypatch):
    def read(path):
        return "104857600" if str(path).endswith("memory.current") else raw_limit

    monkeypatch.setattr("app.observability.worker_memory.Path.read_text", read)
    assert container_memory_fields() == {
        "container_memory_bytes": 104857600,
        "container_memory_limit_bytes": expected,
    }


@pytest.mark.parametrize("component", ["indexing", "activity"])
def test_diagnostics_accept_components_beyond_watcher_and_telegram(component, monkeypatch):
    """Regression: staging worker died with KeyError: 'indexing' after one cycle."""
    events = []
    monkeypatch.setattr(
        "app.observability.worker_memory.logger",
        SimpleNamespace(info=lambda name, **fields: events.append((name, fields))),
    )
    diagnostics = WorkerMemoryDiagnostics()
    sampler = CycleMemorySampler(read=lambda: ProcessMemory(100, 900))
    sampler.stop()
    diagnostics.finish(component, sampler, ProcessMemory(120, 900))
    diagnostics.finish(component, sampler, ProcessMemory(130, 900))
    cycles = [fields for name, fields in events if name == "paper_worker_memory_cycle"]
    assert [fields["component"] for fields in cycles] == [component, component]
    assert cycles[-1]["cycle"] == 2
    assert cycles[-1]["growth_window_cycles"] == 2


def test_supervisor_records_indexing_and_activity_cycles(monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.observability.worker_memory.logger",
        SimpleNamespace(info=lambda name, **fields: events.append((name, fields))),
    )
    supervisor = PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome(status="running"),
        telegram_cycle=lambda: CycleOutcome(status="disarmed"),
        memory_diagnostics_enabled=True,
    )
    supervisor.attach_indexing(lambda: CycleOutcome(status="running"), close=lambda: None)
    supervisor.attach_activity(
        lambda: CycleOutcome(status="running"), poll_seconds=0.01, request_stop=lambda: None
    )
    supervisor.run_round()
    health = supervisor.run_round()
    assert health.indexing is not None and health.indexing.status == "running"
    assert health.activity is not None and health.activity.status == "running"
    cycles = [fields for name, fields in events if name == "paper_worker_memory_cycle"]
    assert {fields["component"] for fields in cycles} == {
        "watcher",
        "telegram",
        "indexing",
        "activity",
    }


def _quiet_allocator(monkeypatch):
    """Drop process-wide GC and malloc_trim from the loop-survival checks.

    ``release_allocator_memory`` runs on every supervised step. Late in the full
    suite that walks a multi-gigabyte heap (the watchlist retention test recorded
    1,928,949,760 bytes just before this file) and a cycle no longer fits a wall-clock
    bound. These tests assert that the thread keeps running, not that the
    allocator is fast.
    """

    monkeypatch.setattr(
        "app.workers.paper_worker.release_allocator_memory",
        lambda: ProcessMemory(rss_bytes=1, peak_rss_bytes=1),
    )


def _capture_diagnostics_warnings(monkeypatch):
    warnings = []
    monkeypatch.setattr(
        "app.workers.paper_worker.logger.warning",
        lambda name, **fields: warnings.append((name, fields)),
    )
    return warnings


def _run_indexing_until_explicit_stop(supervisor):
    """Run the indexing loop until its third cycle requests stop.

    The poll wait is observed without sleeping, so a slow host cannot make
    three cycles miss a deadline. ``join`` only stops a wedged loop from
    hanging the suite.
    """

    calls = []
    stop = supervisor._stop
    stop.wait = lambda timeout=None: stop.is_set()

    def indexing():
        calls.append(1)
        if len(calls) >= 3:
            supervisor.request_stop()
        return CycleOutcome(status="running")

    supervisor.attach_indexing(indexing, close=lambda: None)
    component = supervisor._indexing
    assert component is not None
    thread = threading.Thread(target=supervisor._loop, args=(component,), daemon=True)
    thread.start()
    try:
        thread.join(5)
    finally:
        supervisor.request_stop()
        thread.join(5)
    assert calls == [1, 1, 1]
    assert not thread.is_alive()
    assert not any(t.name == "paper-memory-sampler" for t in threading.enumerate())


def _indexing_supervisor():
    return PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome(status="running"),
        telegram_cycle=lambda: CycleOutcome(status="disarmed"),
        poll_seconds=60,
        memory_diagnostics_enabled=True,
    )


def test_diagnostics_failure_does_not_stop_component_loop(monkeypatch):
    def boom(*_args, **_kwargs):
        raise KeyError("indexing")

    monkeypatch.setattr(WorkerMemoryDiagnostics, "finish", boom)
    warnings = _capture_diagnostics_warnings(monkeypatch)
    _quiet_allocator(monkeypatch)
    _run_indexing_until_explicit_stop(_indexing_supervisor())
    assert [fields["component"] for name, fields in warnings] == ["indexing"] * 3
    assert {fields["error"] for _name, fields in warnings} == {"KeyError: 'indexing'"}


def test_sampler_start_failure_does_not_stop_component_loop(monkeypatch):
    class BrokenSampler(CycleMemorySampler):
        def __init__(self, *args, **kwargs):
            raise RuntimeError("sampler_start_failed")

    monkeypatch.setattr("app.workers.paper_worker.CycleMemorySampler", BrokenSampler)
    warnings = _capture_diagnostics_warnings(monkeypatch)
    _quiet_allocator(monkeypatch)
    _run_indexing_until_explicit_stop(_indexing_supervisor())
    failed = [
        fields["error"]
        for name, fields in warnings
        if name == "paper_worker_memory_diagnostics_failed"
    ]
    assert failed == ["RuntimeError: sampler_start_failed"] * 3


def test_sampler_read_failure_stays_on_the_sampler(monkeypatch):
    events = []
    monkeypatch.setattr(
        "app.observability.worker_memory.logger",
        SimpleNamespace(
            info=lambda *_args, **_kwargs: None,
            warning=lambda name, **fields: events.append((name, fields)),
        ),
    )
    sampler = CycleMemorySampler.__new__(CycleMemorySampler)
    sampler.sampled_peak = 5

    def fail():
        raise RuntimeError("sample_failed")

    sampler._read = fail
    waits = {"count": 0}

    def wait(timeout=None):
        waits["count"] += 1
        return False

    sampler._stop = SimpleNamespace(wait=wait)
    sampler._sample()
    assert waits["count"] == 1
    assert sampler.sampled_peak == 5
    assert events == [
        (
            "paper_worker_memory_diagnostics_failed",
            {"component": "sampler", "error": "RuntimeError: sample_failed"},
        )
    ]
