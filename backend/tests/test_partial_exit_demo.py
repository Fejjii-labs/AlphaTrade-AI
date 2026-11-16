"""Governed BloFin demo partial-exit manager (Griller conditions 2, 3, 4, 5, 8).

Against a simulated reduce-only venue: every order is reduce-only on the
closing side; TP legs are placed with exact leg sizes; the protective stop is
always replaced by placing the new one first and cancelling the old one only
after the new one exists; a failed placement keeps the old stop; a crossed
level is held and alerted, never market-closed; restarts adopt orders by
client id instead of placing twice; the flag, the demo exchange mode, the
kill switch and the worker pause gate every venue call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.core.config import ExchangeMode, Settings
from app.schemas.common import TradeDirection
from app.services.paper_exit_monitor import ExitBar
from app.services.partial_exits.demo import (
    DemoExitRecord,
    DemoLadderPosition,
    GovernedDemoPartialExitManager,
    VenueExitFill,
    VenueOrder,
)
from app.services.partial_exits.paper_terms import PartialExitTerms, parse_partial_exit_terms
from tests.test_partial_exit_paper_monitor import _ladder_payload

ORG = UUID("10000000-0000-4000-8000-0000000000d1")
COMMAND = UUID("50000000-0000-4000-8000-0000000000d5")
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
STEP = timedelta(minutes=15)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"governed_demo_partial_exits_enabled": True}
    values.update(overrides)
    settings = Settings(_env_file=None, **values)  # type: ignore[call-arg]
    return settings.model_copy(update={"exchange_mode": ExchangeMode.PAPER_EXCHANGE_DEMO})


def _terms(direction: TradeDirection = TradeDirection.LONG) -> PartialExitTerms:
    terms = parse_partial_exit_terms(
        _ladder_payload(direction=direction),
        direction=direction,
        entry_price=Decimal("100"),
        position_size=Decimal("1.000"),
    )
    assert isinstance(terms, PartialExitTerms)
    return terms


@dataclass
class FakeVenue:
    side_long: bool = True
    mark: Decimal = Decimal("105")
    fail_place_stop: bool = False
    fail_cancel: bool = False
    orders: dict[str, VenueOrder] = field(default_factory=dict)
    fills: list[VenueExitFill] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    _next: int = 0

    def __post_init__(self) -> None:
        stop = Decimal("95") if self.side_long else Decimal("105")
        self.orders["entry-sl"] = VenueOrder(
            order_id="entry-sl",
            client_order_id="entry-attached",
            kind="stop",
            side=self.close_side,
            size=Decimal("1.000"),
            price=stop,
            reduce_only=True,
        )

    @property
    def close_side(self) -> str:
        return "sell" if self.side_long else "buy"

    def _id(self) -> str:
        self._next += 1
        return f"ord-{self._next}"

    def open_orders(self) -> tuple[VenueOrder, ...]:
        return tuple(self.orders.values())

    def exit_fills(self) -> tuple[VenueExitFill, ...]:
        return tuple(self.fills)

    def mark_price(self) -> Decimal:
        return self.mark

    def place_reduce_only_limit(
        self, *, client_order_id: str, side: str, size: Decimal, price: Decimal
    ) -> str:
        order_id = self._id()
        self.calls.append(("place_limit", client_order_id))
        self.orders[order_id] = VenueOrder(
            order_id, client_order_id, "limit", side, size, price, True
        )
        return order_id

    def place_reduce_only_stop(
        self, *, client_order_id: str, side: str, size: Decimal, trigger_price: Decimal
    ) -> str:
        self.calls.append(("place_stop", client_order_id))
        if self.fail_place_stop:
            raise RuntimeError("venue rejected stop")
        order_id = self._id()
        self.orders[order_id] = VenueOrder(
            order_id, client_order_id, "stop", side, size, trigger_price, True
        )
        return order_id

    def cancel_stop(self, *, order_id: str) -> None:
        self.calls.append(("cancel_stop", order_id))
        if self.fail_cancel:
            raise RuntimeError("cancel failed")
        self.orders.pop(order_id, None)

    def fill(
        self, client_order_id: str, quantity: str, price: str, minutes: int, trade: str
    ) -> None:
        order = next(o for o in self.orders.values() if o.client_order_id == client_order_id)
        self.fills.append(
            VenueExitFill(
                order_id=order.order_id,
                trade_id=trade,
                client_order_id=client_order_id,
                quantity=Decimal(quantity),
                price=Decimal(price),
                occurred_at=T0 + timedelta(minutes=minutes),
            )
        )
        remaining = order.size - Decimal(quantity)
        if remaining == 0:
            del self.orders[order.order_id]
        else:
            self.orders[order.order_id] = VenueOrder(
                order.order_id,
                order.client_order_id,
                order.kind,
                order.side,
                remaining,
                order.price,
                True,
            )


@dataclass
class MemoryStore:
    records: dict[UUID, DemoExitRecord] = field(default_factory=dict)

    def load(self, command_id: UUID) -> DemoExitRecord:
        return self.records.get(command_id, DemoExitRecord())

    def save(self, command_id: UUID, record: DemoExitRecord) -> None:
        self.records[command_id] = record


def _manager(
    venue: FakeVenue,
    store: MemoryStore,
    *,
    settings: Settings | None = None,
    hold: str | None = None,
) -> GovernedDemoPartialExitManager:
    return GovernedDemoPartialExitManager(
        settings=settings or _settings(),
        venue=venue,
        store=store,
        hold=lambda organization_id: hold,
    )


def _position(direction: TradeDirection = TradeDirection.LONG) -> DemoLadderPosition:
    return DemoLadderPosition(
        organization_id=ORG,
        command_id=COMMAND,
        terms=_terms(direction),
        entry_stop_order_id="entry-sl",
    )


def _tp_client(manager: GovernedDemoPartialExitManager, order: int) -> str:
    return manager.client_order_id(COMMAND, f"tp{order}")


# Condition 8: gates before any venue call.


@pytest.mark.parametrize(
    "settings",
    [
        Settings(_env_file=None),  # type: ignore[call-arg]
        Settings(_env_file=None, governed_demo_partial_exits_enabled=True),  # type: ignore[call-arg]
        _settings().model_copy(update={"enable_real_trading": True}),
    ],
    ids=["flag_off", "not_demo_mode", "real_trading"],
)
def test_disabled_or_live_configuration_makes_no_venue_call(settings: Settings) -> None:
    venue = FakeVenue()
    report = _manager(venue, MemoryStore(), settings=settings).step(_position())
    assert report.status == "disabled"
    assert venue.calls == []


@pytest.mark.parametrize("reason", ["kill_switch_active", "worker_paused"])
def test_kill_switch_and_pause_hold_every_venue_call(reason: str) -> None:
    venue = FakeVenue()
    report = _manager(venue, MemoryStore(), hold=reason).step(_position())
    assert report.status == "held"
    assert report.reason == reason
    assert venue.calls == []


# Condition 4: reduce-only legs, exact sizes, protection first.


def test_first_step_places_reduce_only_legs_with_exact_sizes() -> None:
    venue = FakeVenue()
    manager = _manager(venue, MemoryStore())
    report = manager.step(_position())
    assert report.status == "managed"
    limits = [order for order in venue.orders.values() if order.kind == "limit"]
    assert sorted((order.price, order.size) for order in limits) == [
        (Decimal("110"), Decimal("0.500")),
        (Decimal("120"), Decimal("0.300")),
    ]
    assert all(order.reduce_only and order.side == "sell" for order in venue.orders.values())
    # The entry stop still covers the whole position; nothing was cancelled.
    assert ("cancel_stop", "entry-sl") not in venue.calls


def test_second_step_is_idempotent() -> None:
    venue = FakeVenue()
    store = MemoryStore()
    _manager(venue, store).step(_position())
    calls = list(venue.calls)
    _manager(venue, store).step(_position())
    assert venue.calls == calls


def test_partial_tp1_fill_resizes_the_stop_without_moving_it() -> None:
    venue = FakeVenue()
    store = MemoryStore()
    manager = _manager(venue, store)
    manager.step(_position())
    venue.fill(_tp_client(manager, 1), "0.200", "110", 1, "t1")
    manager.step(_position())
    stops = [order for order in venue.orders.values() if order.kind == "stop"]
    assert [(stop.price, stop.size) for stop in stops] == [(Decimal("95"), Decimal("0.800"))]
    place = venue.calls.index(("place_stop", stops[0].client_order_id))
    cancel = venue.calls.index(("cancel_stop", "entry-sl"))
    assert place < cancel


def test_completed_tp1_places_breakeven_before_cancelling_the_old_stop() -> None:
    venue = FakeVenue()
    store = MemoryStore()
    manager = _manager(venue, store)
    manager.step(_position())
    venue.fill(_tp_client(manager, 1), "0.500", "110", 1, "t1")
    manager.step(_position())
    stops = [order for order in venue.orders.values() if order.kind == "stop"]
    assert [(stop.price, stop.size) for stop in stops] == [(Decimal("100.5"), Decimal("0.500"))]
    new_stop = stops[0]
    assert venue.calls.index(("place_stop", new_stop.client_order_id)) < venue.calls.index(
        ("cancel_stop", "entry-sl")
    )
    record = store.load(COMMAND)
    assert [move.key for move in record.stop_moves] == ["breakeven"]
    assert record.active_stop_order_id == new_stop.order_id


def test_failed_stop_placement_keeps_the_old_stop_and_records_nothing() -> None:
    venue = FakeVenue()
    store = MemoryStore()
    manager = _manager(venue, store)
    manager.step(_position())
    venue.fill(_tp_client(manager, 1), "0.500", "110", 1, "t1")
    venue.fail_place_stop = True
    report = manager.step(_position())
    assert "stop_place_failed" in report.alerts
    assert "entry-sl" in venue.orders
    assert ("cancel_stop", "entry-sl") not in venue.calls
    assert store.load(COMMAND).stop_moves == ()
    venue.fail_place_stop = False
    manager.step(_position())
    assert [move.key for move in store.load(COMMAND).stop_moves] == ["breakeven"]
    assert "entry-sl" not in venue.orders


def test_failed_cancel_leaves_two_reduce_only_stops_and_retries() -> None:
    venue = FakeVenue()
    store = MemoryStore()
    manager = _manager(venue, store)
    manager.step(_position())
    venue.fill(_tp_client(manager, 1), "0.500", "110", 1, "t1")
    venue.fail_cancel = True
    report = manager.step(_position())
    assert "stop_cancel_failed" in report.alerts
    stops = [order for order in venue.orders.values() if order.kind == "stop"]
    assert len(stops) == 2 and all(stop.reduce_only for stop in stops)
    venue.fail_cancel = False
    manager.step(_position())
    assert [order.price for order in venue.orders.values() if order.kind == "stop"] == [
        Decimal("100.5")
    ]


def test_restart_adopts_the_placed_breakeven_stop_instead_of_placing_twice() -> None:
    venue = FakeVenue()
    store = MemoryStore()
    manager = _manager(venue, store)
    manager.step(_position())
    venue.fill(_tp_client(manager, 1), "0.500", "110", 1, "t1")
    manager.step(_position())
    placed = [call for call in venue.calls if call[0] == "place_stop"]
    _manager(venue, store).step(_position())
    assert [call for call in venue.calls if call[0] == "place_stop"] == placed


def test_crossed_breakeven_is_held_and_never_market_closed() -> None:
    venue = FakeVenue(mark=Decimal("100.2"))
    store = MemoryStore()
    manager = _manager(venue, store)
    manager.step(_position())
    venue.fill(_tp_client(manager, 1), "0.500", "110", 1, "t1")
    report = manager.step(_position())
    assert "protection_crossed" in report.alerts
    stops = [order for order in venue.orders.values() if order.kind == "stop"]
    # The resize to the remaining size still happens at the old, uncrossed level.
    assert [(stop.price, stop.size) for stop in stops] == [(Decimal("95"), Decimal("0.500"))]
    assert all(call[0] != "place_market" for call in venue.calls)
    assert store.load(COMMAND).stop_moves == ()


# Condition 3: duplicate, out-of-order and overfilling venue fills.


def test_duplicate_and_out_of_order_fills_count_once() -> None:
    venue = FakeVenue()
    store = MemoryStore()
    manager = _manager(venue, store)
    manager.step(_position())
    venue.fill(_tp_client(manager, 2), "0.300", "120", 9, "t2")
    venue.fill(_tp_client(manager, 1), "0.500", "110", 3, "t1")
    venue.fills.append(venue.fills[0])
    manager.step(_position())
    manager.step(_position())
    record = store.load(COMMAND)
    assert sorted(fill.quantity for fill in record.fills) == [Decimal("0.300"), Decimal("0.500")]
    assert [move.effective_at for move in record.stop_moves][0] == T0 + timedelta(minutes=3)


def test_overfill_holds_without_any_venue_mutation() -> None:
    venue = FakeVenue()
    store = MemoryStore()
    manager = _manager(venue, store)
    manager.step(_position())
    tp1 = next(o for o in venue.orders.values() if o.client_order_id == _tp_client(manager, 1))
    venue.fills.append(
        VenueExitFill(
            tp1.order_id, "bad", tp1.client_order_id, Decimal("0.600"), Decimal("110"), T0
        )
    )
    calls = list(venue.calls)
    report = manager.step(_position())
    assert report.status == "held"
    assert report.reason == "fill_refused:leg_overfill"
    assert venue.calls == calls


# Condition 5 on demo: runner trail from closed bars only.


def _bar(index: int, low: str, high: str) -> ExitBar:
    start = T0 + STEP * index
    return ExitBar(
        start,
        start + STEP,
        Decimal(low),
        Decimal(high),
        Decimal(low),
        Decimal(high),
        f"b{index}",
        "c" * 64,
    )


def test_runner_trail_replaces_the_stop_from_closed_bars_only() -> None:
    venue = FakeVenue(mark=Decimal("125"))
    store = MemoryStore()
    manager = _manager(venue, store)
    manager.step(_position())
    venue.fill(_tp_client(manager, 1), "0.500", "110", 1, "t1")
    venue.fill(_tp_client(manager, 2), "0.300", "120", 2, "t2")
    bars = (_bar(0, "118", "125"), _bar(1, "121", "126"), _bar(2, "130", "140"))
    manager.step(_position(), bars=bars, evaluated_at=bars[1].interval_end + STEP / 2)
    stops = [order for order in venue.orders.values() if order.kind == "stop"]
    assert [(stop.price, stop.size) for stop in stops] == [(Decimal("118"), Decimal("0.200"))]


# Shorts mirror.


def test_short_ladder_uses_buy_side_reduce_only_and_rounds_breakeven_down() -> None:
    venue = FakeVenue(side_long=False, mark=Decimal("95"))
    store = MemoryStore()
    manager = _manager(venue, store)
    position = _position(TradeDirection.SHORT)
    manager.step(position)
    assert all(order.side == "buy" and order.reduce_only for order in venue.orders.values())
    venue.fill(_tp_client(manager, 1), "0.500", "90", 1, "t1")
    manager.step(position)
    stops = [order for order in venue.orders.values() if order.kind == "stop"]
    assert [(stop.price, stop.size) for stop in stops] == [(Decimal("99.5"), Decimal("0.500"))]
