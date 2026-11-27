"""Worker OOM (2026-11-20/21): the live monitor's catch-up read must stay bounded.

After a slow Watcher cycle, a rate-limit backoff or a reconnect, one monitor
tick read the whole gap since its last trade (capped at 10 minutes) as one
list, about 3.3 KB of Python objects per aggTrade. A busy 10-minute BTC window
is tens of thousands of trades, so the spike alone could fill the 512 MiB
worker. Open windows were also parked in the closed-window raw cache, where no
later read can hit them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.cvd import accumulate_signed_quote, event_set_hash
from app.market_contracts.enums import ReconnectState
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.identity import binance_usdm_btcusdt
from app.market_monitor import runtime as runtime_module
from app.market_monitor.backoff import BackoffPolicy
from app.market_monitor.runtime import SymbolMonitorRuntime
from app.market_monitor.types import MonitorReason
from app.observability.process_memory import ProcessMemory, read_process_memory
from app.schemas.common import Timeframe
from tests.test_paper_worker_memory import _AggBook, _row


class _CountingSource:
    """Real Binance adapter; records the size of every trade batch it returns."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.book = _AggBook(rows)
        self.inner = BinanceUsdmPerpetualSource(
            transport=httpx.MockTransport(self.book.handler),
            weight_per_minute=10**9,
        )
        self.name = self.inner.name
        self.batch_sizes: list[int] = []

    def fetch_ordered_trades(self, **kwargs: Any):
        batch = self.inner.fetch_ordered_trades(**kwargs)
        self.batch_sizes.append(len(batch.trades))
        return batch

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _tape(start: datetime, *, seconds: int, per_second: int, first_id: int = 1) -> list[dict]:
    step = timedelta(milliseconds=1000 // per_second)
    return [_row(first_id + index, start + step * index) for index in range(seconds * per_second)]


def _runtime(source: object, connected_at: datetime) -> SymbolMonitorRuntime:
    return SymbolMonitorRuntime(
        instrument=binance_usdm_btcusdt(),
        source=source,  # type: ignore[arg-type]
        replay=False,
        backoff=BackoffPolicy(),
        connected_at=connected_at,
    )


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=2)


def test_catch_up_after_a_long_pause_reads_bounded_spans() -> None:
    now = _now()
    paused_at = now - timedelta(minutes=9)
    rows = _tape(paused_at - timedelta(seconds=5), seconds=9 * 60 + 5, per_second=10)
    source = _CountingSource(rows)
    runtime = _runtime(source, paused_at - timedelta(seconds=10))
    runtime._fetch_and_ingest(paused_at)
    source.batch_sizes.clear()

    runtime._fetch_and_ingest(now)

    assert sum(source.batch_sizes) >= 9 * 60 * 10
    # One 60 s span of a 10 trades/s tape (+ the boundary trade), not 5,400.
    assert max(source.batch_sizes) <= 60 * 10 + 1
    assert runtime._reason is MonitorReason.OK
    assert len(runtime._assembler.accepted_trades()) == 1


def test_span_reads_report_the_same_window_as_one_read() -> None:
    now = _now()
    paused_at = now - timedelta(minutes=4, seconds=17)
    rows = _tape(paused_at - timedelta(seconds=5), seconds=4 * 60 + 25, per_second=4)
    for index, row in enumerate(rows):
        row["m"] = index % 3 == 0
        row["q"] = f"0.0{1 + index % 7}"
    source = _CountingSource(rows)
    runtime = _runtime(source, paused_at - timedelta(seconds=10))
    runtime._fetch_and_ingest(paused_at)
    start = runtime._assembler.cursor.last_event_at
    assert start is not None

    runtime._fetch_and_ingest(now)
    assert len(source.batch_sizes) > 2

    reference = BinanceUsdmPerpetualSource(
        transport=httpx.MockTransport(_AggBook(rows).handler),
        weight_per_minute=10**9,
    ).fetch_ordered_trades(
        identity=first_slice_identity(timeframe=Timeframe.M15, replay=False, is_live=True),
        instrument=binance_usdm_btcusdt(),
        start=start,
        end=now,
        source_connection_id=runtime._assembler.connection_identity,
        receive_at=now,
    )
    signed, total = accumulate_signed_quote(reference.trades)
    assert runtime._window_coverage == reference.coverage
    assert runtime._window_signed == signed
    assert runtime._window_total == total
    assert runtime._window_count == len(reference.trades)
    assert runtime._window_event_hash == event_set_hash(reference.trades)


def test_reconnect_backfill_spans_resume_past_a_quiet_watermark_span() -> None:
    now = _now()
    paused_at = now - timedelta(minutes=5)
    # The watermark trade, then 90 quiet seconds, then a busy tape.
    rows = [_row(1, paused_at - timedelta(seconds=1))]
    rows += _tape(paused_at + timedelta(seconds=90), seconds=150, per_second=5, first_id=2)
    source = _CountingSource(rows)
    runtime = _runtime(source, paused_at - timedelta(seconds=10))
    runtime._fetch_and_ingest(paused_at)
    runtime._begin_reconnect(paused_at)
    assert runtime._assembler.cursor.reconnect_state is ReconnectState.RECONNECTING

    runtime._fetch_and_ingest(now)

    assert runtime._assembler.cursor.reconnect_state is not ReconnectState.RECONNECTING
    assert runtime._reason is MonitorReason.OK
    assert runtime._assembler.cursor.last_sequence == len(rows)
    assert max(source.batch_sizes) <= 60 * 5 + 1


def test_multi_span_catch_up_returns_freed_heap(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def release() -> ProcessMemory:
        calls.append(1)
        return read_process_memory()

    monkeypatch.setattr(runtime_module, "release_allocator_memory", release)
    now = _now()
    paused_at = now - timedelta(minutes=3)
    source = _CountingSource(_tape(paused_at - timedelta(seconds=5), seconds=190, per_second=2))
    runtime = _runtime(source, paused_at - timedelta(seconds=10))

    runtime._fetch_and_ingest(paused_at)
    assert calls == []
    runtime._fetch_and_ingest(now)
    assert calls == [1]


def test_open_trade_windows_skip_the_closed_window_cache() -> None:
    now = datetime.now(UTC)
    rows = _tape(now - timedelta(seconds=30), seconds=25, per_second=2)
    source = BinanceUsdmPerpetualSource(
        transport=httpx.MockTransport(_AggBook(rows).handler),
        weight_per_minute=10**9,
    )
    kwargs = {
        "identity": first_slice_identity(timeframe=Timeframe.M15, replay=False, is_live=True),
        "instrument": binance_usdm_btcusdt(),
        "source_connection_id": uuid4(),
        "receive_at": now,
    }
    source.fetch_ordered_trades(start=now - timedelta(seconds=40), end=now, **kwargs)
    assert len(source.evidence_cache) == 0

    source.fetch_ordered_trades(
        start=now - timedelta(seconds=40), end=now - timedelta(seconds=5), **kwargs
    )
    assert len(source.evidence_cache) == 1


def test_gap_longer_than_the_lookback_resyncs_instead_of_staying_stale() -> None:
    """Staging 27 Nov 2026: a 21-minute Watcher cycle left BTC more than 10 minutes
    behind. The catch-up read is capped at 10 minutes, so it could never reach the
    last trade and the monitor stayed stale (every scan refused) until a restart."""

    now = _now()
    paused_at = now - timedelta(minutes=15)
    rows = [_row(1, paused_at - timedelta(seconds=1))]
    rows += _tape(paused_at, seconds=15 * 60, per_second=1, first_id=2)
    source = _CountingSource(rows)
    runtime = _runtime(source, paused_at - timedelta(seconds=10))
    runtime._fetch_and_ingest(paused_at)
    before = runtime._assembler.connection_identity

    snapshot = runtime.tick(now)
    if snapshot.reason is not MonitorReason.OK:  # a warm-up tick is allowed
        snapshot = runtime.tick(now + timedelta(seconds=1))

    assert runtime._assembler.connection_identity != before  # a fresh epoch, like a restart
    assert runtime._reason is MonitorReason.OK
    assert runtime._assembler.cursor.reconnect_state is not ReconnectState.RECONNECTING
