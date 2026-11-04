"""Repeated demo mirror cycles keep memory flat. Fixture payloads only; no venue calls."""

from __future__ import annotations

import gc
import tracemalloc
from datetime import timedelta
from typing import Any

import pytest

from app.providers.exchange.errors import ExchangeRateLimitError
from app.workers.blofin_demo_snapshot import BloFinDemoSnapshotCycle
from tests.test_blofin_demo_manual_mirror import (  # noqa: F401 (autouse fixture)
    _FIXTURE,
    NOW,
    ORG,
    USER,
    Organization,
    User,
    _Client,
    _engine,
    _paths,
    _pinned_mirror_account,
    _settings,
)

_FILLS = "/api/v1/trade/fills-history"
# Cycle plan, repeated: ok, ok after one 429, exhausted 429s, fills page cap,
# a malformed page. Full 7-day reads fall in between on the 600 s re-read.
_PLAN = ("ok", "retry", "exhausted", "capped", "malformed")


class _NullLogger:
    def __getattr__(self, _name: str) -> Any:
        return lambda *_args, **_kwargs: None


class _VenueClient(_Client):
    """The fixture account; ``mode`` picks how the fills endpoint behaves."""

    def __init__(self) -> None:
        super().__init__(_paths(_FIXTURE["long"]))
        self.mode = "ok"
        self.failures = 0
        self.served = 0

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if path != _FILLS or self.mode == "ok":
            return super().request(method, path, **kwargs)
        if self.mode in {"retry", "exhausted"} and (self.mode == "exhausted" or not self.failures):
            self.failures += 1
            raise ExchangeRateLimitError("BloFin rate limit exceeded.")
        if self.mode == "malformed":
            return {"not": "a list"}
        if self.mode == "capped":
            # Fresh, wide rows on every page: an endless, fast-growing history.
            start, self.served = self.served, self.served + 100
            return [
                {"tradeId": f"t{start + index}", "instId": "BTC-USDT", "pad": "p" * 512}
                for index in range(100)
            ]
        return super().request(method, path, **kwargs)


def test_two_hundred_mirror_cycles_do_not_grow_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.blofin_demo_manual_mirror.logger", _NullLogger())
    monkeypatch.setattr("app.workers.blofin_demo_snapshot.logger", _NullLogger())
    alerts: list[str] = []
    monkeypatch.setattr(
        "app.services.safety_alerts.deliver_safety_alert",
        lambda _settings, alert, **_kwargs: alerts.append(alert.code) or True,
    )
    client = _VenueClient()
    monkeypatch.setattr(
        "app.core.blofin_readonly_access.get_readonly_client", lambda *_a, **_k: client
    )
    settings = _settings(
        blofin_demo_manual_mirror_interval_seconds=300,
        blofin_demo_manual_mirror_full_reread_seconds=600,
        blofin_demo_manual_mirror_page_pause_seconds=0.0,
        blofin_demo_manual_mirror_page_retries=1,
        blofin_demo_manual_mirror_page_retry_backoff_seconds=0.0,
        blofin_demo_manual_mirror_max_pages=5,
    )
    clock = [NOW]
    cycle = BloFinDemoSnapshotCycle(settings, clock=lambda: clock[0])
    maker = _engine()
    with maker() as setup:
        setup.add(Organization(id=ORG, name="Mirror Org"))
        setup.add(User(id=USER, email="mirror@test.example", hashed_password="not-a-real-hash"))
        setup.commit()

    advanced = held = 0

    def run(index: int) -> None:
        nonlocal advanced, held
        client.mode = _PLAN[index % len(_PLAN)]
        client.failures = 0
        client.calls.clear()  # the fake client's own request log
        before = cycle._mirror_cursor
        clock[0] = NOW + timedelta(minutes=5 * index)
        session = maker()
        try:
            cycle._mirror_without_backing_off(session, ORG, USER)  # type: ignore[arg-type]
        finally:
            session.close()
        if client.mode in {"ok", "retry"}:
            assert cycle._mirror_cursor is not None and cycle._mirror_cursor is not before
            advanced += 1
        else:
            # A failed or capped read never moves the cursor.
            assert cycle._mirror_cursor is before
            held += 1

    tracemalloc.start()
    try:
        for index in range(40):
            run(index)
        gc.collect()
        baseline = tracemalloc.get_traced_memory()[0]
        tracemalloc.reset_peak()
        for index in range(40, 200):
            run(index)
        gc.collect()
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert advanced == 80 and held == 120
    assert client.served > 0 and "blofin_demo_mirror_page_cap" in alerts
    # A leak of one capped page per cycle would add ~8 MB over 160 cycles.
    assert current - baseline < 64 * 1024, (baseline, current)
    # Peak in any one cycle stays well under one capped read of raw wide pages.
    assert peak - baseline < 1024 * 1024, (baseline, peak)
