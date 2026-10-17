# Activity worker timing correction — PR237 handoff

Reported revision: `18e66dacfc0eb81f897c3f5a6eefbe0f4d29923f` in
[PR239](https://github.com/Fejjii/AlphaTrade-AI/pull/239), targeting
[PR237](https://github.com/Fejjii/AlphaTrade-AI/pull/237).
This handoff is included in the single correction commit directly following that
revision; its exact published SHA is recorded in PR239's description.
Verification ran October 10, 2026 with Python 3.12.14, from
`/workspace/blofin-native-activity/backend`.

## Finding and correction

The original assertion mixed thread isolation with a full-process allocator cleanup
performance assumption. It required three watcher calls with a 10 ms poll interval
to fit inside a one-second progress wait. Every `_step` calls
`release_allocator_memory` in `finally`, even with diagnostics disabled. Its
unconditional `gc.collect()` examines the process's tracked heap while holding the
CPython GIL. Watcher and Telegram cleanup therefore affect scheduling of the watcher,
activity and test thread. Initial libc discovery/trim can also overlap another
thread's GC. Cleanup elapsed time cannot be inferred from the poll interval.

Inspection found separate component health locks, cycle work outside those locks,
no activity join on the watcher loop, and a start lock used only to establish one
thread per component. Cleanup runs after the health update and outside that lock.
The activity wait releases its condition lock while blocked. There was no activity
operation serializing the watcher in the observed failure. Ordinary local baseline
runs passed; this finding does not claim to have captured the integration host's
exact heap or stack trace.

A bounded retained heap reproduced the reported assertion using real GC and no
injected sleeps. The probe retained one million nested lists (two million list
objects). Activity entered at 0.866 s. The watcher completed another cycle, but GC
delayed the next invocation past the progress deadline: the one-second wait returned
false after 1.349 s of wall time. Activity remained blocked until failure cleanup
released it, before its original two-second watchdog expired. First watcher cleanup
took 0.871 s; another took 0.420 s. These are overlapping wall times, not exclusive
GC CPU times to sum. The trace demonstrates cleanup/GIL contention, without assuming
that the environment is slow.

The corrected test controls allocator cleanup for thread isolation and uses explicit
events. Both watcher outcomes occur after activity reaches its blocking point;
the progress event is emitted from watcher cleanup after two health updates. Before
releasing activity it asserts completed scans, no watcher failure, activity still
blocked, one native thread identity, one activity call and exactly one live activity
thread. Variants block inside the activity cycle and inside its `_step` cleanup.
Shutdown must release activity, complete cleanup with no failure and stop every
component thread. The original **one-second entry and progress waits remain**;
five-second waits are failure watchdogs for the controlled worker callbacks.

Production worker scheduling, cleanup, polling, retry/concurrency limits and shutdown
code are unchanged. Real cleanup remains exercised by the successful/failed-cycle
memory diagnostics tests in the focused selection. A temporary mutation that puts
all components behind one shared `_step` lock fails both variants specifically at
the watcher-progress assertion, rather than being accepted by the new synchronization.

## Exact commands and results

Baseline at `18e66da`, before the test edit:

```sh
.venv/bin/pytest -o addopts='' -q tests/test_blofin_activity_worker.py::test_blocked_activity_does_not_delay_watcher_and_starts_only_one_thread
# 1 passed in 1.13s
.venv/bin/pytest -o addopts='' -q tests/test_blofin_activity_worker.py tests/test_paper_worker_supervisor.py -k 'not postgres'
# 20 passed, 2 deselected in 11.85s
PYTHONPATH=/tmp .venv/bin/pytest -o addopts='' -q -s -p activity_worker_trace --activity-trace-heap tests/test_blofin_activity_worker.py::test_blocked_activity_does_not_delay_watcher_and_starts_only_one_thread
# 1 failed in 8.64s: entered.wait(1) is True, watcher_progress.wait(1) is False
```

The tracing plugin is reproduced below. An ordinary traced isolated run passed in
1.03 s; collection with worker/supervisor/diagnostics/activity PostgreSQL/activation
modules, selecting only the failing node, passed (1 passed, 86 deselected, 1.49 s).
No PostgreSQL test or database connection ran in that collection-only probe.

Corrected tree:

```sh
.venv/bin/pytest -o addopts='' -q tests/test_blofin_activity_worker.py::test_blocked_activity_does_not_delay_watcher_and_starts_only_one_thread
# 2 passed in 0.10s
PYTHONPATH=/tmp .venv/bin/pytest -o addopts='' -q -s -p activity_worker_trace --activity-trace-heap tests/test_blofin_activity_worker.py::test_blocked_activity_does_not_delay_watcher_and_starts_only_one_thread
# 2 passed in 4.58s, including retained-heap fixture setup
.venv/bin/pytest -o addopts='' -q tests/test_blofin_activity_worker.py tests/test_paper_worker_supervisor.py tests/test_worker_memory_diagnostics.py tests/test_process_memory.py -k 'not postgres'
# 31 passed, 2 deselected in 11.84s; no skips
PYTHONPATH=/tmp .venv/bin/pytest -o addopts='' -q -p activity_worker_serialization_probe tests/test_blofin_activity_worker.py::test_blocked_activity_does_not_delay_watcher_and_starts_only_one_thread
# Expected negative control: 2 failed in 2.13s at watcher_progress.wait(1)
.venv/bin/ruff check tests/test_blofin_activity_worker.py
.venv/bin/ruff format --check tests/test_blofin_activity_worker.py
git diff --check
# All exit 0 after import sorting/formatting
```

The 31-case selection covers activity disabled defaults, explicit bounded tick wiring,
blocked cycle/cleanup, duplicate starts, failure isolation, cooperative stop, provider
retry/timeout caps, missing pins, supervisor restarts/authority fencing, SIGTERM shutdown,
real memory cleanup on success/failure, sampler shutdown and platform RSS behavior.
The two existing PostgreSQL lease cases were explicitly deselected: this correction
changes no persistence/lease behavior, and those cases' disposable-PostgreSQL evidence
remains in the preceding [historical ledger](blofin_native_activity_verification.md).

Small bounded repeat: five fresh pytest processes, each capped at 30 s by
`subprocess.run(..., timeout=30)`. Three repetitions of the corrected isolated command
above each passed both variants (0.09 s, 0.09 s, 0.07 s). Two repetitions with the same
four focused modules and `-k test_blocked_activity_does_not_delay_watcher_and_starts_only_one_thread`
each passed both variants (31 deselected, 0.32 s and 0.25 s). These ten repeated passes
are repetitions of the same two cases, not ten additional distinct tests.

## Integration-owner verification

Apply the single correction from PR239 and rerun the two corrected variants and the
31-case focused command in the integration environment. Bind that evidence to the
resulting PR237 candidate SHA; PR237's earlier green CI is not evidence for this
correction. The test retains its progress deadline and has no skip/retry policy change.
There are no API/schema changes or generated-client updates in this correction.

The guarantee is independence from an activity operation or cleanup that blocks
while releasing the GIL. This test does not establish a production one-second latency
SLA or immunity from process-wide GC, CPU starvation, or code holding the GIL.
Venue acceptance, full backend release acceptance and deployed behavior remain
unverified. The existing activity coverage limits and activation prerequisites in
the [package ledger](blofin_native_activity_verification.md) still apply: native
retention/sweep latency, bounded fill windows and missing ancestry; reviewed a10
migration/rollback, owner presentation integration, correct organization/UID pins,
authorized read-only demo credentials and separate explicit opt-in.

No frontend or CI files changed. No full backend CI, manual workflow dispatch,
deployment, runtime activation, exchange call/order, account mutation, Telegram
message or credential change occurred during this correction.

## Temporary reproduction probes

Save this as `/tmp/activity_worker_trace.py`. It works with the reported original
node and the corrected parametrized node. The heap is retained throughout each test;
the GC, cleanup and `_step` wrappers delegate to the original functions.

```python
import json
import threading
import time

import pytest


def pytest_addoption(parser):
    parser.addoption('--activity-trace-heap', action='store_true')


@pytest.fixture(autouse=True)
def trace_activity_worker(request, monkeypatch):
    if request.node.originalname != 'test_blocked_activity_does_not_delay_watcher_and_starts_only_one_thread':
        return
    from app.observability import process_memory
    from app.workers import paper_worker

    retained_heap = [[[]] for _ in range(1_000_000)] if request.config.getoption('--activity-trace-heap') else []
    origin = time.perf_counter()
    rows = [{'operation': 'retained_heap', 'start': 0, 'nested_lists': len(retained_heap)}]

    def wrap(name, original):
        def observed(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                rows.append({'operation': name, 'thread': threading.current_thread().name,
                             'start': round(started - origin, 6),
                             'seconds': round(time.perf_counter() - started, 6)})
        return observed

    monkeypatch.setattr(process_memory.gc, 'collect', wrap('gc.collect', process_memory.gc.collect))
    monkeypatch.setattr(process_memory, '_malloc_trim', wrap('malloc_trim', process_memory._malloc_trim))
    monkeypatch.setattr(paper_worker, 'release_allocator_memory',
                        wrap('release_allocator_memory', paper_worker.release_allocator_memory))
    monkeypatch.setattr(paper_worker.PaperWorkerSupervisor, '_step',
                        wrap('step', paper_worker.PaperWorkerSupervisor._step))
    real_event = threading.Event
    event_count = 0

    class ObservedEvent(real_event):
        def __init__(self):
            nonlocal event_count
            super().__init__()
            self.label = ['activity_entered', 'activity_release', 'watcher_progress'][event_count]
            event_count += 1

        def set(self):
            rows.append({'operation': self.label + '.set',
                         'thread': threading.current_thread().name,
                         'start': round(time.perf_counter() - origin, 6)})
            return super().set()

        def wait(self, timeout=None):
            return wrap(self.label + '.wait', super().wait)(timeout)

    if '[' not in request.node.name:
        monkeypatch.setattr(request.module, 'Event', ObservedEvent)
    yield
    print('\nACTIVITY_TRACE ' + json.dumps(sorted(rows, key=lambda row: row['start'])))
```

Save the negative control as `/tmp/activity_worker_serialization_probe.py`. This
probe is intentionally incorrect worker behavior and must not be used for acceptance:

```python
from threading import Lock

import pytest


@pytest.fixture(autouse=True)
def serialize_components(request, monkeypatch):
    if request.node.originalname != 'test_blocked_activity_does_not_delay_watcher_and_starts_only_one_thread':
        return
    from app.workers.paper_worker import PaperWorkerSupervisor

    mutex = Lock()
    original_step = PaperWorkerSupervisor._step
    original_components = PaperWorkerSupervisor._components

    def shared_step(self, component):
        with mutex:
            return original_step(self, component)

    def activity_first(self):
        return tuple(sorted(original_components(self), key=lambda c: c.name != 'activity'))

    monkeypatch.setattr(PaperWorkerSupervisor, '_step', shared_step)
    monkeypatch.setattr(PaperWorkerSupervisor, '_components', activity_first)
```
