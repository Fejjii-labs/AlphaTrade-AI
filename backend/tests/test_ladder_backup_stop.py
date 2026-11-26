"""BloFin backup stop for stepped-exit (ladder) demo trades.

A ladder is closed by the bot (market reduce-only), so no stop rests on BloFin
while the bot is down. With ``GOVERNED_DEMO_LADDER_BACKUP_STOP`` on, the manager
keeps one whole-position stop order (``size`` -1) on BloFin at its current stop:
placed when missing, moved only to tighten (break-even after TP1, the trail).
BloFin behaviour checked on the demo on 26 Nov 2026 (SOL): size -1 follows the
position after a partial close, amend-tpsl moves it in place, and a full close
cancels it. If the backup stop closes the trade, the exit proof accepts it only
at the approved stop or tighter, under the client id derived from the entry.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.providers.exchange.governed_blofin_partial_exits import backup_stop_client_id
from app.schemas.common import TradeDirection
from app.services.partial_exits.demo import GovernedDemoPartialExitManager
from tests.test_partial_exit_demo import (
    COMMAND,
    MemoryStore,
    SimVenue,
    _client,
    _position,
    _settings,
)
from tests.test_partial_exit_demo_wiring import _adapter

TICK = Decimal("0.1")


@dataclass
class BackupVenue(SimVenue):
    resting: tuple[str, Decimal] | None = None
    fail_backup: bool = False
    backup_calls: list[tuple[str, Decimal]] = field(default_factory=list)

    def backup_trigger(self, stop: Decimal) -> Decimal:
        from decimal import ROUND_CEILING, ROUND_FLOOR

        rounding = ROUND_CEILING if self.long else ROUND_FLOOR
        return (stop / TICK).to_integral_value(rounding=rounding) * TICK

    def backup_stop(self) -> tuple[str, Decimal] | None:
        if self.fail_backup:
            raise RuntimeError("tpsl read failed")
        return self.resting

    def place_backup_stop(self, trigger: Decimal) -> None:
        self.backup_calls.append(("place", trigger))
        self.resting = ("tpsl-1", trigger)

    def amend_backup_stop(self, tpsl_id: str, trigger: Decimal) -> None:
        assert tpsl_id == "tpsl-1"
        self.backup_calls.append(("amend", trigger))
        self.resting = (tpsl_id, trigger)


def _manager(venue: BackupVenue, store: MemoryStore, *, on: bool = True, hold: Any = None):
    return GovernedDemoPartialExitManager(
        settings=_settings(),
        venue=venue,
        store=store,
        hold=lambda organization_id: hold,
        backup_stop=on,
    )


def test_off_never_touches_a_backup_stop() -> None:
    venue = BackupVenue(mark=Decimal("105"))
    _manager(venue, MemoryStore(), on=False).step(_position())
    assert venue.backup_calls == []


def test_placed_at_the_stop_once_then_left_alone() -> None:
    venue = BackupVenue(mark=Decimal("105"))
    store = MemoryStore()
    manager = _manager(venue, store)
    report = manager.step(_position())
    assert venue.backup_calls == [("place", Decimal("95"))]
    assert report.placed == ("backup_stop",) and report.status == "managed"
    manager.step(_position())
    assert venue.backup_calls == [("place", Decimal("95"))]


def test_moved_to_breakeven_after_tp1_and_never_loosened() -> None:
    venue = BackupVenue(mark=Decimal("110"))
    store = MemoryStore()
    manager = _manager(venue, store)
    first = manager.step(_position())
    # The step that sends a close does not touch the backup stop.
    assert first.placed == (_client("t1"),) and venue.backup_calls == []
    manager.step(_position())  # TP1 fill read: stop to breakeven 100.5
    assert venue.backup_calls == [("place", Decimal("100.5"))]
    venue.resting = ("tpsl-1", Decimal("95"))  # e.g. an older stop still resting
    manager.step(_position())
    assert venue.backup_calls[-1] == ("amend", Decimal("100.5"))
    venue.resting = ("tpsl-1", Decimal("101"))  # already tighter: left alone
    calls = len(venue.backup_calls)
    manager.step(_position())
    assert len(venue.backup_calls) == calls


def test_short_rounds_down_and_moves_down() -> None:
    venue = BackupVenue(long=False, mark=Decimal("95"))
    store = MemoryStore()
    manager = _manager(venue, store)
    manager.step(_position(TradeDirection.SHORT))
    assert venue.backup_calls == [("place", Decimal("105"))]
    venue.mark = Decimal("90")
    manager.step(_position(TradeDirection.SHORT))  # TP1 close
    manager.step(_position(TradeDirection.SHORT))  # breakeven 99.5
    assert venue.backup_calls[-1] == ("amend", Decimal("99.5"))


def test_a_failed_backup_stop_only_alerts() -> None:
    venue = BackupVenue(mark=Decimal("105"), fail_backup=True)
    report = _manager(venue, MemoryStore()).step(_position())
    assert report.status == "managed" and report.alerts == ("backup_stop_failed",)


def test_skipped_on_a_position_mismatch_or_a_crossed_stop() -> None:
    venue = BackupVenue(mark=Decimal("105"), position=Decimal("0.5"))
    assert _manager(venue, MemoryStore()).step(_position()).reason == "venue_position_mismatch"
    crossed = BackupVenue(mark=Decimal("94"))
    report = _manager(crossed, MemoryStore()).step(_position())
    assert report.placed == (_client("x"),) and crossed.backup_calls == []


@pytest.mark.parametrize("hold", ["kill_switch_active", "worker_paused", "daily_loss_lock"])
def test_a_risk_hold_still_keeps_the_protective_stop(hold: str) -> None:
    venue = BackupVenue(mark=Decimal("105"))
    report = _manager(venue, MemoryStore(), hold=hold).step(_position())
    assert report.status == "held" and venue.backup_calls == [("place", Decimal("95"))]


# The BloFin adapter.


def _backup_adapter(routes: dict[str, Any]):
    venue, recorder, identity = _adapter(routes)
    venue._backup_id = backup_stop_client_id("ATentry")
    venue._plan.instrument_rules = type("R", (), {"tick_size": TICK})()
    return venue, recorder, identity


def test_adapter_places_a_whole_position_reduce_only_stop() -> None:
    venue, recorder, identity = _backup_adapter(
        {"/api/v1/trade/order-tpsl": [{"tpslId": "t9", "code": "0"}]}
    )
    venue.place_backup_stop(Decimal("95.1"))
    identity.assert_called_once_with()
    method, path, _params, body = recorder.requests[-1]
    assert (method, path) == ("POST", "/api/v1/trade/order-tpsl")
    assert body == {
        "instId": "BTC-USDT",
        "marginMode": "isolated",
        "positionSide": "net",
        "side": "sell",
        "slTriggerPrice": "95.1",
        "slOrderPrice": "-1",
        "size": "-1",
        "reduceOnly": "true",
        "clientOrderId": backup_stop_client_id("ATentry"),
    }


def test_adapter_moves_reads_and_rounds() -> None:
    mine = backup_stop_client_id("ATentry")
    venue, recorder, _ = _backup_adapter(
        {
            "/api/v1/trade/amend-tpsl": [{"tpslId": "t9", "code": "0"}],
            "/api/v1/trade/orders-tpsl-pending": [
                {"tpslId": "other", "clientOrderId": "bsother", "slTriggerPrice": "90"},
                {"tpslId": "t9", "clientOrderId": mine, "slTriggerPrice": "95.1"},
            ],
        }
    )
    assert venue.backup_stop() == ("t9", Decimal("95.1"))
    venue.amend_backup_stop("t9", Decimal("100.5"))
    assert recorder.requests[-1][3] == {
        "instId": "BTC-USDT",
        "tpslId": "t9",
        "newSlTriggerPrice": "100.5",
        "newSlOrderPrice": "-1",
    }
    assert venue.backup_trigger(Decimal("100.52")) == Decimal("100.6")  # long: never looser


def test_adapter_refuses_without_an_entry_identity() -> None:
    venue, recorder, identity = _adapter({})
    with pytest.raises(ValueError):
        venue.place_backup_stop(Decimal("95"))
    assert recorder.requests == [] and not identity.called


def test_client_id_is_short_alphanumeric_and_stable() -> None:
    cid = backup_stop_client_id("ATentry")
    assert cid == backup_stop_client_id("ATentry") != backup_stop_client_id("ATother")
    assert cid.isalnum() and cid.startswith("bs") and len(cid) == 26
    assert not cid.startswith(GovernedDemoPartialExitManager.client_prefix(COMMAND)[:2])


# Exit proof: a ladder closed by TP1 (bot) and then by the backup stop.


def _ladder_world(trigger_offset: Decimal = Decimal("-50")):
    from tests.test_demo_attached_tpsl_exit import _ms, attached_world
    from tests.test_partial_exit_spec_gate import _multi_target

    plan, _entry, provider, responses, tpsl_params = attached_world("sl")
    plan = _multi_target(plan, runner=False)  # a short: stop above the entry
    quantity = plan.quantity.value
    half = quantity / 2
    entry = _entry  # the same full-size entry, client id ATentry
    close, parent = responses["orders-history"]
    for key in ("tpTriggerPrice", "slTriggerPrice", "tpOrderPrice", "slOrderPrice"):
        parent[key] = None  # a ladder entry carries no attached TP/SL
    tp1 = {**deepcopy(close), "orderId": "tp1", "clientOrderId": "pe1t1n0", "algoId": "",
           "orderCategory": "normal", "size": str(half), "filledSize": str(half)}  # fmt: skip
    close.update(size=str(quantity - half), filledSize=str(quantity - half),
                 createTime=_ms(timedelta(seconds=5)))  # fmt: skip
    fill = responses["fills-history"][0]
    responses["fills-history"] = [
        {**fill, "orderId": "tp1", "tradeId": "tp1-fill", "fillSize": str(half)},
        {**fill, "fillSize": str(quantity - half), "ts": _ms(timedelta(seconds=5))},
    ]
    responses["orders-history"] = [close, tp1, parent]
    stop = plan.risk_and_exits.stop.value
    responses["orders-tpsl-history"] = [
        {
            **responses["orders-tpsl-history"][0],
            "clientOrderId": backup_stop_client_id("ATentry"),
            "size": None,
            "actualSize": str(quantity - half),
            "tpTriggerPrice": None,
            "slTriggerPrice": str(stop + trigger_offset),  # breakeven: tighter for a short
        }
    ]
    original = provider._client.request

    def request(method, path, *args, **kwargs):
        if path.endswith("/fills-history"):
            order = (kwargs.get("params") or {}).get("orderId")
            data = [f for f in responses["fills-history"] if f["orderId"] == order]
            return deepcopy(data)
        return original(method, path, *args, **kwargs)

    provider._client.request = request
    provider._clock = lambda: entry.fills[0].occurred_at + timedelta(seconds=10)
    return plan, entry, provider, responses, tpsl_params


def test_exit_proof_accepts_the_backup_stop_at_breakeven() -> None:
    plan, entry, provider, _responses, tpsl_params = _ladder_world()
    closed = provider.reconcile_exit(plan=plan, entry=entry)
    assert closed is not None
    assert closed.protection_ids == ("10004687495",)
    assert sorted(f.category for f in closed.fills) == ["normal", "sl"]
    assert tpsl_params == [{"clientOrderId": backup_stop_client_id("ATentry"), "limit": "100"}]


@pytest.mark.parametrize(
    "reason",
    [
        "looser",
        "foreign_client",
        "size_mismatch",
        "two_effective",
        "not_reduce_only",
        "tp",
        "close_names_another_stop",
    ],
)
def test_exit_proof_holds_anything_else(reason: str) -> None:
    offset = Decimal("1") if reason == "looser" else Decimal("-50")
    plan, entry, provider, responses, _ = _ladder_world(offset)
    row = responses["orders-tpsl-history"][0]
    if reason == "foreign_client":
        row["clientOrderId"] = "bsforeign"
    elif reason == "size_mismatch":
        row["actualSize"] = str(plan.quantity.value)
    elif reason == "two_effective":
        responses["orders-tpsl-history"].append(deepcopy(row))
    elif reason == "not_reduce_only":
        row["reduceOnly"] = "false"
    elif reason == "tp":
        responses["orders-history"][0]["orderCategory"] = "tp"
    elif reason == "close_names_another_stop":
        responses["orders-history"][0]["algoId"] = "10004687496"
    with pytest.raises(ValueError):
        provider.reconcile_exit(plan=plan, entry=entry)
