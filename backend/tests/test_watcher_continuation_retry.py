"""H4: a confirmed setup whose continuation raised is resumed on the replayed bar."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.workers.watcher_paper import (
    CONTINUATION_MAX_RETRIES,
    WatcherPaperRuntime,
    WatcherPaperScanReport,
)


def _runtime(continue_loop: object) -> SimpleNamespace:
    runtime = SimpleNamespace(
        _pending_continuations=OrderedDict(),
        _fence_capacity=8,
        _continue_paper_loop=continue_loop,
    )
    runtime._remember_continuation = lambda *a, **k: WatcherPaperRuntime._remember_continuation(
        runtime,  # type: ignore[arg-type]
        *a,
        **k,
    )
    return runtime


def _fresh_report(*, kill: bool = False) -> WatcherPaperScanReport:
    candidate_id = uuid4()
    return WatcherPaperScanReport(
        organization_id=uuid4(),
        scan_scope="scope",
        symbol="BTCUSDT",
        status="succeeded",
        reason_code="confirmed",
        replayed=False,
        published=True,
        candidate_ids=(candidate_id,),
        kill_switch_active=kill,
        discussion=SimpleNamespace(candidate=SimpleNamespace(candidate_id=candidate_id)),  # type: ignore[arg-type]
    )


def _replay_of(report: WatcherPaperScanReport, *, kill: bool = False) -> WatcherPaperScanReport:
    return replace(
        report,
        reason_code="replay",
        replayed=True,
        candidate_ids=(),
        discussion=None,
        kill_switch_active=kill,
    )


def _call(runtime: SimpleNamespace, report: WatcherPaperScanReport, key: str, evidence: object):
    target = SimpleNamespace(organization_id=report.organization_id, scan_scope="scope")
    return WatcherPaperRuntime._continue_or_resume(
        runtime,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        target,  # type: ignore[arg-type]
        report,
        evidence,  # type: ignore[arg-type]
        idempotency_key=key,
    )


def test_replay_resumes_continuation_after_a_transient_failure() -> None:
    calls: list[tuple[WatcherPaperScanReport, object]] = []
    assembly = (object(), object())

    def continue_loop(_session, _target, report, evidence):
        calls.append((report, evidence))
        if len(calls) == 1:
            raise RuntimeError("transient db error")
        return replace(report, paper_loop_stage="filled", paper_loop_reason="paper_filled")

    runtime = _runtime(continue_loop)
    fresh = _fresh_report()
    evidence = SimpleNamespace(last_assembly=lambda: assembly, market_source=lambda: "binance")
    with pytest.raises(RuntimeError):
        _call(runtime, fresh, "bar-1", evidence)
    assert "scope" in runtime._pending_continuations

    # Next cycle, same closed bar: the orchestrator replays with no discussion.
    replay = _replay_of(fresh, kill=True)
    resumed = _call(runtime, replay, "bar-1", SimpleNamespace(last_assembly=lambda: None))
    assert resumed.paper_loop_stage == "filled"
    assert resumed.replayed is True
    second_report, second_evidence = calls[1]
    assert second_report.discussion is fresh.discussion
    assert second_report.candidate_ids == fresh.candidate_ids
    # The kill switch is re-read on the replay, never taken from the stash.
    assert second_report.kill_switch_active is True
    assert second_evidence.last_assembly() is assembly
    assert second_evidence.market_source() == "binance"
    assert runtime._pending_continuations == {}


def test_a_new_bar_abandons_the_pending_continuation() -> None:
    def failing(*_args):
        raise RuntimeError("boom")

    runtime = _runtime(failing)
    fresh = _fresh_report()
    evidence = SimpleNamespace(last_assembly=lambda: (1, 2), market_source=lambda: None)
    with pytest.raises(RuntimeError):
        _call(runtime, fresh, "bar-1", evidence)
    runtime._continue_paper_loop = lambda _s, _t, report, _e: report
    replay = _replay_of(fresh)
    out = _call(runtime, replay, "bar-2", evidence)
    assert out == replay  # passed through as a plain replay, no stashed resume
    assert runtime._pending_continuations == {}


def test_continuation_retries_are_bounded_per_bar() -> None:
    calls = {"n": 0}

    def failing(*_args):
        calls["n"] += 1
        raise RuntimeError("still failing")

    runtime = _runtime(failing)
    fresh = _fresh_report()
    evidence = SimpleNamespace(last_assembly=lambda: (1, 2), market_source=lambda: None)
    with pytest.raises(RuntimeError):
        _call(runtime, fresh, "bar-1", evidence)
    for _ in range(CONTINUATION_MAX_RETRIES):
        with pytest.raises(RuntimeError):
            _call(runtime, _replay_of(fresh), "bar-1", evidence)
    assert runtime._pending_continuations == {}
    runtime._continue_paper_loop = lambda _s, _t, report, _e: report
    replay = _replay_of(fresh)
    assert _call(runtime, replay, "bar-1", evidence) == replay  # nothing left to resume
    assert calls["n"] == 1 + CONTINUATION_MAX_RETRIES


def test_a_failure_without_a_confirmed_setup_is_not_stashed() -> None:
    def failing(*_args):
        raise RuntimeError("boom")

    runtime = _runtime(failing)
    report = replace(_fresh_report(), discussion=None)
    with pytest.raises(RuntimeError):
        _call(runtime, report, "bar-1", SimpleNamespace(last_assembly=lambda: (1, 2)))
    assert runtime._pending_continuations == {}
