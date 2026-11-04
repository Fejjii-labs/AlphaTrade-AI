"""2026-11-04 staging OOM: aggTrade batch hashing peak, and probe monitor close.

The worker had no steady leak; it died on transient peaks after the US open,
when the market monitor caught up 10 min windows of tens of thousands of
aggTrades. Hashing one such batch held its canonical preimage up to five times.
"""

from __future__ import annotations

import gc
import hashlib
import tracemalloc
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

import httpx
import pytest

from app.market_contracts.adapters.aggtrades import datetime_to_ms
from app.market_contracts.adapters.binance_usdm import BinanceUsdmPerpetualSource
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.hashing import _omit_legacy_default_false, semantic_content_hash
from app.market_contracts.identity import binance_usdm_btcusdt
from app.schemas.common import Timeframe
from app.services.canonical_serialization import (
    CanonicalSerializationError,
    canonical_json_bytes,
    canonical_sha256,
)

START = datetime(2026, 11, 4, 14, 40, tzinfo=UTC)
END = START + timedelta(minutes=10)


class _Side(Enum):
    BUY = "buy"


def _one_shot(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _big(n: int) -> list[dict[str, Any]]:
    return [
        {
            "z": Decimal("3412.55") + i,
            "a": i,
            "id": UUID(int=i),
            "t": START + timedelta(milliseconds=i),
            "side": _Side.BUY,
            "nested": {"k": (i, "\u00e9"), "empty": []},
        }
        for i in range(n)
    ]


@pytest.mark.parametrize(
    "value",
    [
        {"trades": _big(300), "hash": "x", "count": 300},
        {"trades": tuple(_big(257)), "meta": {"a": None, "b": True}},
        {"trades": [], "x": _big(3)},  # small: one-shot path
        {"rows": [[1, 2], [], [Decimal("0")]] * 100},
        # NFC collision: last key wins, as in _canonicalize.
        {"cafe\u0301": 1, "caf\u00e9": 2, "rows": list(range(400))},
        {"z": {}, "a": [{}] * 300, "m": "\u00fc\u2028"},
    ],
)
def test_streamed_hash_is_byte_identical_to_one_shot_bytes(value: dict[str, Any]) -> None:
    assert canonical_sha256(value) == _one_shot(value)


def test_streamed_hash_still_rejects_non_canonical_values() -> None:
    rows: list[Any] = list(range(400))
    rows[399] = 1.5
    with pytest.raises(CanonicalSerializationError):
        canonical_sha256({"rows": rows})
    with pytest.raises(CanonicalSerializationError):
        canonical_sha256({"rows": [{1: "int key"}] * 300})


def test_legacy_false_omit_copies_only_what_it_changes() -> None:
    untouched = {"trades": [{"a": 1}] * 3, "flag": False}
    assert _omit_legacy_default_false(untouched) is untouched
    nested = {"rows": [{"autonomous_approval": False, "b": 1}, {"c": 2}], "d": 3}
    cleaned = _omit_legacy_default_false(nested)
    assert cleaned == {"rows": [{"b": 1}, {"c": 2}], "d": 3}
    assert cleaned["rows"][1] is nested["rows"][1]
    assert nested["rows"][0] == {"autonomous_approval": False, "b": 1}  # input not mutated
    assert semantic_content_hash({"x": 1, "autonomous_approval": False}) == (
        semantic_content_hash({"x": 1})
    )


def _rows(n: int) -> tuple[dict[str, Any], ...]:
    step = 600_000 / n
    return tuple(
        {
            "a": i,
            "p": "3412.55",
            "q": "0.153",
            "f": i,
            "l": i,
            "T": datetime_to_ms(START) + int((i - 1) * step),
            "m": bool(i % 2),
        }
        for i in range(1, n + 1)
    )


def test_large_aggtrade_window_fetch_peak_stays_bounded() -> None:
    """A 10 min catch-up window: peak was ~8.3 KB per trade, now ~3.2 KB."""

    n = 5000
    source = BinanceUsdmPerpetualSource(
        base_url="https://fapi.binance.com",
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
    )
    source._cached_agg_trades = lambda **_kwargs: _rows(n)  # type: ignore[method-assign]
    gc.collect()
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        batch = source.fetch_ordered_trades(
            identity=first_slice_identity(timeframe=Timeframe.M15, replay=False, is_live=True),
            instrument=binance_usdm_btcusdt(),
            start=START,
            end=END,
            source_connection_id=UUID(int=7),
            receive_at=END,
        )
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    assert len(batch.trades) == n
    assert peak / n < 5_000, f"peak {peak / n:.0f} bytes per trade"


def test_probe_provider_closes_the_monitor_it_built(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from app.core.config import Settings
    from app.workers import watcher_activation

    closed: list[str] = []

    class _Source:
        def close(self) -> None:
            closed.append("source")

    class _Monitor:
        replay = False
        _source = _Source()

        def latest(self, _symbol: str) -> object:
            return SimpleNamespace(
                availability=SimpleNamespace(value="fresh"),
                reason=SimpleNamespace(value="ok"),
                is_live=True,
                last_update=None,
                evaluated_at=None,
            )

    monkeypatch.setattr(watcher_activation, "perpetual_source_is_replay", lambda _s: False)
    monkeypatch.setattr(watcher_activation, "_build_monitor", lambda _s: _Monitor())
    settings = Settings()
    assert watcher_activation._probe_provider(settings, None) == "available"
    assert closed == ["source"]

    given = _Monitor()
    closed.clear()
    watcher_activation._probe_provider(settings, given)
    assert closed == []  # a caller-owned monitor stays open


def test_failover_source_close_closes_both_venues() -> None:
    from app.market_contracts.adapters.failover import FailoverPerpetualSource

    closed: list[str] = []
    source = FailoverPerpetualSource.__new__(FailoverPerpetualSource)
    source._primary = type("P", (), {"close": lambda self: closed.append("binance")})()  # type: ignore[attr-defined]
    source._secondary = type("S", (), {"close": lambda self: closed.append("bybit")})()  # type: ignore[attr-defined]
    source.close()
    assert closed == ["binance", "bybit"]


def test_build_monitor_closes_the_source_when_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.market_contracts.adapters import factory as adapters_factory
    from app.market_monitor import factory as monitor_factory
    from app.workers import watcher_activation

    closed: list[str] = []

    class _Source:
        def close(self) -> None:
            closed.append("source")

    source = _Source()

    def failing_build(_settings: Settings, *, source: object) -> object:
        raise ValueError("bad backoff settings")

    monkeypatch.setattr(adapters_factory, "resolve_perpetual_evidence_source", lambda _s: source)
    monkeypatch.setattr(monitor_factory, "build_perpetual_market_monitor", failing_build)
    with pytest.raises(ValueError, match="bad backoff settings"):
        watcher_activation._build_monitor(Settings())
    assert closed == ["source"]

    # A failing close must not mask the original construction error.
    def failing_close() -> None:
        raise RuntimeError("close failed")

    source.close = failing_close  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="bad backoff settings"):
        watcher_activation._build_monitor(Settings())


def test_build_monitor_keeps_the_source_open_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.market_contracts.adapters import factory as adapters_factory
    from app.market_monitor import factory as monitor_factory
    from app.workers import watcher_activation

    closed: list[str] = []
    source = type("S", (), {"close": lambda self: closed.append("source")})()
    built = object()
    monkeypatch.setattr(adapters_factory, "resolve_perpetual_evidence_source", lambda _s: source)
    monkeypatch.setattr(
        monitor_factory, "build_perpetual_market_monitor", lambda _s, *, source: built
    )
    assert watcher_activation._build_monitor(Settings()) is built
    assert closed == []
