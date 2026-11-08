"""A failed paper close backs off per trade, and a legacy trade keeps its venue mode."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import structlog
from structlog.testing import capture_logs

from app.core.config import ExchangeMode
from app.core.errors import ValidationAppError
from app.learning_attribution.contracts import LearningVenueMode
from app.learning_attribution.errors import LearningAttributionConflictError
from app.schemas.common import Timeframe, TradeDirection
from app.schemas.execution_protocol import ClosePaperPlanRequest
from app.services import canonical_execution_learning as learning
from app.services.paper_exit_monitor import (
    CLOSE_FAILURE_BACKOFF_BASE,
    CLOSE_FAILURE_BACKOFF_MAX,
    CloseFailureBackoff,
    ExitBar,
    FetchedBars,
    OpenInternalPaperTrade,
    PaperExitCloser,
    next_close_backoff,
    run_paper_exit_monitor,
)

ORG = UUID("10000000-0000-4000-8000-000000000001")
START = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
EVALUATED = START + timedelta(minutes=15)


@pytest.fixture(autouse=True)
def _fresh_loggers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A logger bound by an earlier test bypasses ``capture_logs``."""

    from app.services import paper_exit_monitor as monitor

    for module in (monitor, learning):
        monkeypatch.setattr(module, "logger", structlog.get_logger(module.__name__))


def _trade(journal_trade_id: UUID | None = None) -> OpenInternalPaperTrade:
    return OpenInternalPaperTrade(
        journal_trade_id=journal_trade_id or uuid4(),
        organization_id=ORG,
        user_id=uuid4(),
        account_id=uuid4(),
        revision_id=uuid4(),
        command_id=uuid4(),
        symbol="BTCUSDT",
        timeframe=Timeframe.M15,
        direction=TradeDirection.LONG,
        entry_price=Decimal("100"),
        entry_time=START,
        stop=Decimal("95"),
        target=Decimal("110"),
        fees=Decimal("0"),
        funding=Decimal("0"),
        slippage=Decimal("0"),
    )


def _bar(start: datetime, low: str, high: str) -> ExitBar:
    return ExitBar(
        interval_start=start,
        interval_end=start + timedelta(minutes=15),
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("100"),
        source_event_id=f"bar-{start.isoformat()}",
        content_hash="a" * 64,
    )


class _Counting:
    """Closed 15m bars from entry to ``until``. The first bar touches the stop."""

    def __init__(self) -> None:
        self.reads = 0
        self.until = EVALUATED

    def __call__(self, _trade: OpenInternalPaperTrade) -> FetchedBars:
        self.reads += 1
        step = timedelta(minutes=15)
        bars = [_bar(START, "94", "101")]
        cursor = START + step
        while cursor + step <= self.until:
            bars.append(_bar(cursor, "96", "109"))
            cursor += step
        return FetchedBars(bars=tuple(bars), covers_entry=True, source_name="test-perpetual")


class _FailingCloser(PaperExitCloser):
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    def close(self, request: ClosePaperPlanRequest) -> object:
        self.calls += 1
        raise self.exc


class _OkCloser(PaperExitCloser):
    def close(self, request: ClosePaperPlanRequest) -> object:
        return SimpleNamespace(replayed=False)


def _venue_conflict() -> LearningAttributionConflictError:
    return LearningAttributionConflictError(
        "Learning venue mode conflict for this attribution aggregate.",
        details={"reason": "learning_venue_mode_conflict"},
    )


def _pass(
    trade: OpenInternalPaperTrade,
    closer: PaperExitCloser,
    candles: _Counting,
    failures: dict[UUID, CloseFailureBackoff],
    at: datetime,
):
    candles.until = at
    return run_paper_exit_monitor(
        enabled=True,
        dry_run=False,
        trades=(trade,),
        candles=candles,
        closer=closer,
        evaluated_at=at,
        close_failures=failures,
    )


def test_backoff_doubles_per_failure_and_caps() -> None:
    first = next_close_backoff(None, failed_at=EVALUATED, reason="OperationalError")
    assert first.failures == 1 and not first.quarantined
    assert first.retry_at == EVALUATED + CLOSE_FAILURE_BACKOFF_BASE
    second = next_close_backoff(first, failed_at=EVALUATED, reason="OperationalError")
    assert second.retry_at == EVALUATED + 2 * CLOSE_FAILURE_BACKOFF_BASE
    state = second
    for _ in range(40):
        state = next_close_backoff(state, failed_at=EVALUATED, reason="OperationalError")
    assert state.retry_at == EVALUATED + CLOSE_FAILURE_BACKOFF_MAX


def test_backoff_cap_is_exactly_one_hour() -> None:
    # Literal values, not the constant, so a changed cap fails here.
    cap = CLOSE_FAILURE_BACKOFF_MAX
    assert cap == timedelta(hours=1)
    assert cap.total_seconds() == 3600
    state = None
    for _ in range(50):
        state = next_close_backoff(state, failed_at=EVALUATED, reason="OperationalError")
        assert state.retry_at - EVALUATED <= timedelta(seconds=3600)
    assert state is not None
    assert state.retry_at == EVALUATED + timedelta(hours=1)
    assert (state.retry_at - EVALUATED).total_seconds() == 3600
    quarantined = next_close_backoff(
        None, failed_at=EVALUATED, reason="learning_venue_mode_conflict"
    )
    assert quarantined.retry_at == EVALUATED + timedelta(hours=1)


def test_identity_conflict_quarantines_for_the_maximum_wait() -> None:
    state = next_close_backoff(None, failed_at=EVALUATED, reason="learning_venue_mode_conflict")
    assert state.quarantined
    assert state.retry_at == EVALUATED + CLOSE_FAILURE_BACKOFF_MAX


def test_venue_conflict_does_not_hot_loop_across_16_second_cycles() -> None:
    trade = _trade()
    closer = _FailingCloser(_venue_conflict())
    candles = _Counting()
    failures: dict[UUID, CloseFailureBackoff] = {}
    with capture_logs() as logs:
        reports = [
            _pass(trade, closer, candles, failures, EVALUATED + timedelta(seconds=16 * i))
            for i in range(200)  # ~53 minutes of 16 s cycles
        ]
    assert closer.calls == 1
    assert candles.reads == 1
    assert reports[0].errors == 1 and reports[0].error == "close_failed"
    # Waiting out the quarantine is reported, not re-failed every pass (M5, L7).
    assert all(r.status == "backing_off" and r.backed_off == 1 for r in reports[1:])
    assert all(r.error == "close_quarantined" and r.quarantined == 1 for r in reports[1:])
    assert [a.code for a in reports[0].alerts] == ["close_quarantined"]
    assert all(r.alerts == () for r in reports[1:])
    events = [entry["event"] for entry in logs]
    assert events.count("paper_exit_monitor_close_failed") == 1
    assert events.count("paper_exit_monitor_close_quarantined") == 1
    failed = next(e for e in logs if e["event"] == "paper_exit_monitor_close_failed")
    assert failed["reason"] == "learning_venue_mode_conflict"
    # The hourly retry still runs so a code or data repair is picked up.
    later = _pass(trade, closer, candles, failures, EVALUATED + CLOSE_FAILURE_BACKOFF_MAX)
    assert closer.calls == 2 and later.errors == 1
    assert failures[trade.journal_trade_id].failures == 2


def test_quarantine_alert_is_logged_once_per_streak() -> None:
    trade = _trade()
    closer = _FailingCloser(_venue_conflict())
    failures: dict[UUID, CloseFailureBackoff] = {}
    with capture_logs() as logs:
        for hour in range(3):
            _pass(
                trade, closer, _Counting(), failures, EVALUATED + hour * CLOSE_FAILURE_BACKOFF_MAX
            )
    assert closer.calls == 3
    events = [entry["event"] for entry in logs]
    assert events.count("paper_exit_monitor_close_quarantined") == 1


def test_transient_failure_retries_after_the_wait_and_success_clears_it() -> None:
    trade = _trade()
    candles = _Counting()
    failures: dict[UUID, CloseFailureBackoff] = {}
    _pass(trade, _FailingCloser(RuntimeError("db gone")), candles, failures, EVALUATED)
    assert failures[trade.journal_trade_id].reason == "RuntimeError"
    waiting = _pass(trade, _OkCloser(), candles, failures, EVALUATED + timedelta(seconds=30))
    assert waiting.backed_off == 1 and waiting.closed == 0
    done = _pass(trade, _OkCloser(), candles, failures, EVALUATED + CLOSE_FAILURE_BACKOFF_BASE)
    assert done.status == "closed" and done.closed == 1
    assert trade.journal_trade_id not in failures


def test_skip_reason_clears_backoff_and_closed_trades_are_pruned() -> None:
    trade = _trade()
    failures: dict[UUID, CloseFailureBackoff] = {}
    _pass(trade, _FailingCloser(RuntimeError("x")), _Counting(), failures, EVALUATED)
    already = ValidationAppError("closed", details={"reason": "already_closed"})
    report = _pass(
        trade,
        _FailingCloser(already),
        _Counting(),
        failures,
        EVALUATED + CLOSE_FAILURE_BACKOFF_BASE,
    )
    assert report.skipped == 1 and trade.journal_trade_id not in failures
    _pass(trade, _FailingCloser(RuntimeError("x")), _Counting(), failures, EVALUATED)
    other = _trade()
    _pass(other, _OkCloser(), _Counting(), failures, EVALUATED)
    assert trade.journal_trade_id not in failures


def test_one_stuck_trade_does_not_block_another() -> None:
    stuck = _trade()
    healthy = _trade()
    failures: dict[UUID, CloseFailureBackoff] = {}

    class Closer(PaperExitCloser):
        def close(self, request: ClosePaperPlanRequest) -> object:
            if request.command_id == stuck.command_id:
                raise _venue_conflict()
            return SimpleNamespace(replayed=False)

    report = run_paper_exit_monitor(
        enabled=True,
        dry_run=False,
        trades=(stuck, healthy),
        candles=_Counting(),
        closer=Closer(),
        evaluated_at=EVALUATED,
        close_failures=failures,
    )
    assert report.closed == 1 and report.errors == 1
    assert set(failures) == {stuck.journal_trade_id}


# --- venue mode on later events of an existing attribution aggregate ---


def _settings(mode: ExchangeMode) -> SimpleNamespace:
    return SimpleNamespace(exchange_mode=mode)


@pytest.mark.parametrize(
    ("recorded", "mode", "expected"),
    [
        (None, ExchangeMode.PAPER_INTERNAL, LearningVenueMode.PAPER_INTERNAL),
        (None, ExchangeMode.PAPER_EXCHANGE_DEMO, LearningVenueMode.PAPER_EXCHANGE_DEMO),
        (
            LearningVenueMode.PAPER_INTERNAL,
            ExchangeMode.PAPER_EXCHANGE_DEMO,
            LearningVenueMode.PAPER_INTERNAL,
        ),
        (
            LearningVenueMode.PAPER_EXCHANGE_DEMO,
            ExchangeMode.PAPER_INTERNAL,
            LearningVenueMode.PAPER_EXCHANGE_DEMO,
        ),
    ],
)
def test_resolve_learning_venue_mode(recorded, mode, expected) -> None:
    assert learning.resolve_learning_venue_mode(_settings(mode), recorded) is expected


def _attribute(monkeypatch: pytest.MonkeyPatch, previous: object | None) -> dict[str, object]:
    candidate_id = uuid4()
    seen: dict[str, object] = {}

    class Store:
        def __init__(self, _session: object) -> None:
            pass

        def get(self, *, organization_id: UUID, candidate_id: UUID) -> object | None:
            seen["lookup"] = (organization_id, candidate_id)
            return previous

    class Service:
        def __init__(self, *_args: object) -> None:
            pass

        def apply_projected(self, **kwargs: object) -> None:
            seen["mode"] = kwargs["learning_venue_mode"]

    lineage = SimpleNamespace(
        candidate=SimpleNamespace(candidate_id=candidate_id),
        evaluation=SimpleNamespace(setup_assessment_content_hash="b" * 64),
    )
    monkeypatch.setattr(learning, "PostgresAttributionStore", Store)
    monkeypatch.setattr(learning, "LearningAttributionService", Service)
    monkeypatch.setattr(
        learning, "require_canonical_attribution_lineage", lambda **_kwargs: lineage
    )
    from app.schemas.journal_lifecycle import JournalLifecycleEventType

    envelope = SimpleNamespace(
        plan=SimpleNamespace(
            revision_id=uuid4(),
            content_hash="c" * 64,
            organization_id=ORG,
            account_id=uuid4(),
        ),
        lineage=SimpleNamespace(
            assessment_id=uuid4(),
            evidence_window_hash="d" * 64,
            candidate_id=candidate_id,
            strategy_version_id=uuid4(),
            setup_definition_id=uuid4(),
        ),
    )
    learning.attribute_canonical_paper_event(
        session=object(),
        projector=object(),
        runtime=object(),
        settings=SimpleNamespace(exchange_mode=ExchangeMode.PAPER_EXCHANGE_DEMO),
        envelope=envelope,
        event=SimpleNamespace(event_type=JournalLifecycleEventType.CLOSE),
        projection=object(),
        organization_id=ORG,
        user_id=uuid4(),
    )
    assert seen["lookup"] == (ORG, candidate_id)
    return seen


def test_legacy_paper_internal_close_keeps_its_mode_after_switch_to_demo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = SimpleNamespace(
        facts=SimpleNamespace(learning_venue_mode=LearningVenueMode.PAPER_INTERNAL)
    )
    with capture_logs() as logs:
        seen = _attribute(monkeypatch, previous)
    assert seen["mode"] is LearningVenueMode.PAPER_INTERNAL
    assert any(e["event"] == "learning_venue_mode_kept_from_aggregate" for e in logs)


def test_first_attribution_takes_the_current_exchange_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _attribute(monkeypatch, None)
    assert seen["mode"] is LearningVenueMode.PAPER_EXCHANGE_DEMO


def test_exit_candles_are_read_once_per_closed_bar_not_every_pass() -> None:
    """M6: a trade's candles are re-read after its bar closes, after a close
    failure, or every 5 minutes; not on every 15 s pass."""

    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from app.schemas.common import Timeframe
    from app.workers.paper_exit_monitor import _trades_due_for_candles

    trade = SimpleNamespace(journal_trade_id=uuid4(), timeframe=Timeframe("15m"))
    gate: dict = {}
    start = datetime(2026, 11, 7, 12, 0, 30, tzinfo=UTC)  # 30 s after a 15m close
    assert _trades_due_for_candles([trade], gate, None, start) == [trade]
    assert _trades_due_for_candles([trade], gate, None, start + timedelta(seconds=15)) == []
    assert _trades_due_for_candles([trade], gate, None, start + timedelta(minutes=4)) == []
    # Safety re-check after five minutes.
    assert _trades_due_for_candles([trade], gate, None, start + timedelta(minutes=5)) == [trade]
    assert _trades_due_for_candles([trade], gate, None, start + timedelta(minutes=9)) == []
    # Next 15m close (12:15:00) is due once settled (+20 s), before the re-check.
    first_after_close = start + timedelta(minutes=14, seconds=51)
    assert _trades_due_for_candles([trade], gate, None, first_after_close) == [trade]
    # A pending close retry is always due.
    failures = {trade.journal_trade_id: object()}
    later = start + timedelta(minutes=15)
    assert _trades_due_for_candles([trade], gate, failures, later) == [trade]  # type: ignore[arg-type]
    # Closed trades leave the gate.
    _trades_due_for_candles([], gate, None, later)
    assert gate == {}


def test_candle_loader_begin_pass_drops_bars_but_keeps_adapters() -> None:
    from app.core.config import Settings
    from app.services.paper_exit_candles import PaperExitCandleLoader

    adapter = object()
    loader = PaperExitCandleLoader(Settings(_env_file=None), sources={"replay": adapter})  # type: ignore[dict-item]
    loader._cache[("replay", "BTCUSDT", "15m")] = object()  # type: ignore[assignment]
    loader.begin_pass()
    assert loader._cache == {}
    assert loader._sources == {"replay": adapter}


def test_cycle_reports_quarantine_in_heartbeat_and_pages_once(monkeypatch) -> None:
    """M5/L7: a quarantined close shows as backing_off with a count; one alert."""

    from uuid import uuid4

    from app.core.config import Settings
    from app.services.paper_exit_monitor import ExitMonitorAlert, MonitorReport
    from app.workers import paper_exit_monitor as worker

    org = uuid4()
    alert = ExitMonitorAlert(
        code="close_quarantined",
        severity="warning",
        message="m",
        fields={"organization_id": str(org), "dedup_key": "close_quarantined:x"},
    )
    reports = iter(
        [
            MonitorReport(
                status="backing_off",
                error="close_quarantined",
                backed_off=1,
                quarantined=1,
                alerts=(alert,),
                organization_ids=(org,),
            ),
            MonitorReport(
                status="backing_off", error="close_quarantined", backed_off=1, quarantined=1
            ),
        ]
    )
    monkeypatch.setattr(worker, "run_configured_exit_monitor", lambda *_a, **_k: next(reports))
    beats: list[tuple[str, str]] = []
    monkeypatch.setattr(
        worker.PaperExitMonitorCycle,
        "_beat",
        lambda self, status, error: beats.append((status, error)),
    )
    sent: list[ExitMonitorAlert] = []
    cycle = worker.PaperExitMonitorCycle(
        Settings(_env_file=None, paper_exit_monitor_enabled=True), alert_sink=sent.append
    )
    monkeypatch.setattr(cycle, "_note_repetition", lambda *_a, **_k: None)
    first = cycle()
    second = cycle()
    assert first.status == second.status == "backing_off"
    assert first.error == "close_quarantined:1"
    assert [a.code for a in sent] == ["close_quarantined"]
    assert beats[-1] == ("backing_off", "close_quarantined:1")
