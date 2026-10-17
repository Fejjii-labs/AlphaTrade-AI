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


def test_diagnostics_failure_does_not_stop_component_loop(monkeypatch):
    calls = []
    warnings = []

    def boom(*_args, **_kwargs):
        raise KeyError("indexing")

    monkeypatch.setattr(WorkerMemoryDiagnostics, "finish", boom)
    monkeypatch.setattr(
        "app.workers.paper_worker.logger.warning",
        lambda name, **fields: warnings.append((name, fields)),
    )
    supervisor = PaperWorkerSupervisor(
        watcher_cycle=lambda: CycleOutcome(status="running"),
        telegram_cycle=lambda: CycleOutcome(status="disarmed"),
        poll_seconds=0.01,
        memory_diagnostics_enabled=True,
    )
    done = threading.Event()

    def indexing():
        calls.append(1)
        if len(calls) >= 3:
            done.set()
        return CycleOutcome(status="running")

    supervisor.attach_indexing(indexing, close=lambda: None)
    indexing_component = supervisor._indexing
    thread = threading.Thread(target=supervisor._loop, args=(indexing_component,), daemon=True)
    thread.start()
    try:
        assert done.wait(5), "indexing loop died after a diagnostics failure"
    finally:
        supervisor.request_stop()
        thread.join(5)
    assert len(calls) >= 3
    assert not thread.is_alive()
    assert any(
        name == "paper_worker_memory_diagnostics_failed" and fields["component"] == "indexing"
        for name, fields in warnings
    )
    assert not any(t.name == "paper-memory-sampler" for t in threading.enumerate())
