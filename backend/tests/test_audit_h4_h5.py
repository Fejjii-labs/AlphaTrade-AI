"""Audit H-4 (one failing command never stops the cycle) and H-5 (BloFin's
``partially_canceled`` order state: partly filled, rest cancelled)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
from tests.test_demo_attached_tpsl_exit import attached_world
from tests.test_partial_exit_demo_wiring import _adapter


def test_a_failing_command_is_isolated_and_rolled_back() -> None:
    loop = GovernedBloFinDemoLoop(None, SimpleNamespace(), SimpleNamespace())  # type: ignore[arg-type]
    bad, good = uuid4(), uuid4()
    rolled: list[bool] = []

    def reconcile(session: Any, *, command_id: Any) -> str:
        if command_id == bad:
            raise RuntimeError("projection failed")
        return "demo_ladder_managed"

    loop.reconcile_command = reconcile  # type: ignore[method-assign]
    session = SimpleNamespace(rollback=lambda: rolled.append(True))
    assert loop._reconcile_isolated(session, bad) == "demo_reconcile_failed"  # type: ignore[arg-type]
    assert loop._reconcile_isolated(session, good) == "demo_ladder_managed"  # type: ignore[arg-type]
    assert rolled == [True]


def test_the_worker_keeps_scanning_when_the_reconcile_raises(monkeypatch: Any) -> None:
    from app.workers import watcher_paper

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(watcher_paper, "reconcile_governed_demo_once", boom)
    runtime = SimpleNamespace(
        _enabled=True,
        _session_factory=object(),
        _settings=object(),
        _clock=object(),
        _canonical_runtime=None,
    )
    watcher_paper.WatcherPaperRuntime._reconcile_governed_demo_effects(runtime)  # type: ignore[arg-type]


def test_partially_canceled_closing_order_reads_as_cancelled_with_its_fills() -> None:
    venue, _recorder, _identity = _adapter(
        {
            "/api/v1/trade/orders-pending": [],
            "/api/v1/trade/orders-history": [
                {
                    "orderId": "c1",
                    "clientOrderId": "pe1t1n0",
                    "state": "partially_canceled",
                    "side": "sell",
                    "size": "0.300",
                    "filledSize": "0.200",
                    "reduceOnly": "true",
                }
            ],
        }
    )
    (order,) = venue.closing_orders("pe1")
    assert (order.state, order.filled, order.size) == (
        "canceled",
        Decimal("0.200"),
        Decimal("0.300"),
    )


def test_exit_proof_accepts_a_partially_canceled_close() -> None:
    plan, entry, provider, responses, _ = attached_world("tp")
    close = responses["orders-history"][0]
    close["size"] = str(Decimal(close["size"]) * 2)  # half of it cancelled
    close["state"] = "partially_canceled"
    closed = provider.reconcile_exit(plan=plan, entry=entry)
    assert closed is not None and closed.fills[0].quantity == plan.quantity.value
