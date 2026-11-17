"""Governed BloFin demo partial exits: bot-managed reduce-only market closes.

BloFin does not document how an entry's attached TP/SL is sized after the
position is partly reduced, so a ladder entry carries no exchange-side TP/SL.
The manager watches the price and closes each leg, the breakeven stop and the
runner trail with market reduce-only orders. Against a simulated venue:

- TP1 is closed at its exact size; the stop then moves to breakeven plus costs;
- TP2-TP4 are closed once each, also on a gap that reaches two levels;
- the runner trails the closed-candle swing and is closed when the mark crosses it;
- a restart mid-ladder never sends a second close for the same leg;
- a partly filled close is completed by a new attempt for the remainder only;
- no close is ever larger than the venue position, and a venue position that
  differs from the recorded state holds with no order;
- the flag, the demo mode, the kill switch, the worker pause and the daily loss
  lock gate every venue call.
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
    VenueCloseOrder,
    VenueExitFill,
)
from app.services.partial_exits.paper_terms import PartialExitTerms, parse_partial_exit_terms

ORG = UUID("10000000-0000-4000-8000-0000000000d1")
COMMAND = UUID("50000000-0000-4000-8000-0000000000d5")
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
STEP = timedelta(minutes=15)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"governed_demo_partial_exits_enabled": True}
    values.update(overrides)
    settings = Settings(_env_file=None, **values)  # type: ignore[call-arg]
    return settings.model_copy(update={"exchange_mode": ExchangeMode.PAPER_EXCHANGE_DEMO})


def four_target_payload(direction: TradeDirection = TradeDirection.LONG) -> dict[str, object]:
    """TP1-TP4 at 30/20/20/10 % plus a 20 % closed-candle runner; entry 100, stop 95/105."""

    long = direction is TradeDirection.LONG
    prices = ("110", "120", "130", "140") if long else ("90", "80", "70", "60")
    fractions = ("0.3", "0.2", "0.2", "0.1")
    return {
        "timeframe": "15m",
        "instrument_rules": {
            "contract_multiplier": "1",
            "tick_size": "0.1",
            "lot_size": "0.001",
            "minimum_quantity": "0.001",
            "minimum_notional": "0",
        },
        "risk_and_exits": {
            "stop": {"value": "95" if long else "105", "unit": "USDT"},
            "targets": [
                {
                    "order": index,
                    "price": {"value": price, "unit": "USDT"},
                    "quantity_fraction": fraction,
                    "derivation": {"formula_id": "fixture", "formula_version": "1"},
                }
                for index, (price, fraction) in enumerate(
                    zip(prices, fractions, strict=True), start=1
                )
            ],
            "runner": {
                "enabled": True,
                "activation_target_order": 4,
                "remaining_quantity_fraction": "0.2",
                "rule_id": "closed-candle-trail",
                "rule_version": "1",
                "expression": "lookback_bars=2",
            },
            "fee_allowance": {"value": "0.5", "unit": "USDT"},
            "funding_allowance": {"value": "0", "unit": "USDT"},
            "slippage_allowance": {"value": "0", "unit": "USDT"},
        },
    }


def _terms(direction: TradeDirection = TradeDirection.LONG) -> PartialExitTerms:
    terms = parse_partial_exit_terms(
        four_target_payload(direction),
        direction=direction,
        entry_price=Decimal("100"),
        position_size=Decimal("1.000"),
    )
    assert isinstance(terms, PartialExitTerms)
    return terms


@dataclass
class SimVenue:
    """Net-mode demo account holding one position; market closes are reduce-only.

    ``fill_ratio`` fills that share of each close and cancels the rest (IOC
    remainder). ``defer`` leaves a placed close live and unfilled.
    """

    long: bool = True
    mark: Decimal = Decimal("105")
    position: Decimal = Decimal("1.000")
    fill_ratio: Decimal = Decimal("1")
    defer: bool = False
    fail_place: bool = False
    orders: dict[str, VenueCloseOrder] = field(default_factory=dict)
    fills: list[VenueExitFill] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)
    placed: list[tuple[str, str, Decimal]] = field(default_factory=list)
    minute: int = 0

    def __post_init__(self) -> None:
        if not self.long:
            self.position = -self.position

    @property
    def close_side(self) -> str:
        return "sell" if self.long else "buy"

    def closing_orders(self, client_prefix: str) -> tuple[VenueCloseOrder, ...]:
        self.calls.append(("closing_orders", client_prefix))
        return tuple(
            order
            for order in self.orders.values()
            if order.client_order_id.startswith(client_prefix)
        )

    def exit_fills(self, order_ids: tuple[str, ...]) -> tuple[VenueExitFill, ...]:
        self.calls.append(("exit_fills", ",".join(order_ids)))
        return tuple(fill for fill in self.fills if fill.order_id in order_ids)

    def position_quantity(self) -> Decimal:
        self.calls.append(("position", ""))
        return self.position

    def mark_price(self) -> Decimal:
        self.calls.append(("mark", ""))
        return self.mark

    def place_reduce_only_market(self, *, client_order_id: str, side: str, size: Decimal) -> str:
        self.calls.append(("place", client_order_id))
        if self.fail_place:
            raise RuntimeError("venue rejected close")
        # A reduce-only close can never exceed the open position or flip it.
        assert side == self.close_side
        assert 0 < size <= abs(self.position)
        order_id = f"ord-{len(self.orders) + 1}"
        self.placed.append((client_order_id, side, size))
        self.orders[order_id] = VenueCloseOrder(
            order_id, client_order_id, side, size, Decimal("0"), True, "live"
        )
        if not self.defer:
            self.settle(order_id)
        return order_id

    def settle(self, order_id: str, *, price: Decimal | None = None) -> None:
        order = self.orders[order_id]
        filled = (order.size * self.fill_ratio).quantize(Decimal("0.001"))
        self.minute += 1
        if filled > 0:
            self.fills.append(
                VenueExitFill(
                    order_id=order_id,
                    trade_id=f"tr-{order_id}",
                    client_order_id=order.client_order_id,
                    quantity=filled,
                    price=price or self.mark,
                    occurred_at=T0 + timedelta(minutes=self.minute),
                )
            )
            self.position += -filled if self.long else filled
        self.orders[order_id] = VenueCloseOrder(
            order_id,
            order.client_order_id,
            order.side,
            order.size,
            filled,
            True,
            "filled" if filled == order.size else "canceled",
        )


@dataclass
class MemoryStore:
    records: dict[UUID, DemoExitRecord] = field(default_factory=dict)

    def load(self, command_id: UUID) -> DemoExitRecord:
        return self.records.get(command_id, DemoExitRecord())

    def save(self, command_id: UUID, record: DemoExitRecord) -> None:
        self.records[command_id] = record


def _manager(
    venue: SimVenue,
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
    return DemoLadderPosition(organization_id=ORG, command_id=COMMAND, terms=_terms(direction))


def _client(leg: str, attempt: int = 0) -> str:
    return GovernedDemoPartialExitManager.close_client_id(COMMAND, leg, attempt)


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


def _stop(store: MemoryStore) -> Decimal:
    moves = store.load(COMMAND).stop_moves
    return moves[-1].new_stop if moves else Decimal("95")


# Gates: flag off, live configuration, kill switch, pause, loss lock.


@pytest.mark.parametrize(
    "settings",
    [
        Settings(_env_file=None),  # type: ignore[call-arg]
        Settings(_env_file=None, governed_demo_partial_exits_enabled=True),  # type: ignore[call-arg]
        _settings().model_copy(update={"enable_real_trading": True}),
    ],
    ids=["flag_off", "not_demo_mode", "real_trading"],
)
def test_flag_off_or_live_configuration_sends_no_closing_order(settings: Settings) -> None:
    venue = SimVenue(mark=Decimal("150"))
    report = _manager(venue, MemoryStore(), settings=settings).step(_position())
    assert report.status == "disabled"
    assert venue.calls == []


@pytest.mark.parametrize("reason", ["kill_switch_active", "worker_paused", "daily_loss_lock"])
def test_kill_switch_pause_and_loss_lock_hold_every_venue_call(reason: str) -> None:
    venue = SimVenue(mark=Decimal("150"))
    store = MemoryStore()
    report = _manager(venue, store, hold=reason).step(_position())
    assert (report.status, report.reason) == ("held", reason)
    assert venue.calls == []
    assert store.records == {}
    # Released: management resumes from the venue state, nothing was lost.
    assert _manager(venue, store).step(_position()).status == "managed"
    assert venue.placed[0][0] == _client("t1")


# A hold stops new risk, never risk reduction (Griller PR 467 B1). A ladder has
# no exchange-side stop, so a crossed stop must still close the remainder.


@pytest.mark.parametrize("reason", ["kill_switch_active", "worker_paused", "daily_loss_lock"])
def test_hold_still_closes_the_remainder_when_the_stop_is_crossed(reason: str) -> None:
    venue = SimVenue(mark=Decimal("80"))  # far through the 95 stop
    report = _manager(venue, MemoryStore(), hold=reason).step(_position())
    assert venue.placed == [(_client("x"), "sell", Decimal("1.000"))]
    assert report.placed == (_client("x"),)


def test_no_level_reached_sends_nothing() -> None:
    venue = SimVenue(mark=Decimal("105"))
    report = _manager(venue, MemoryStore()).step(_position())
    assert report.status == "managed"
    assert venue.placed == []


# TP1 then breakeven plus fees.


def test_tp1_closes_its_exact_size_then_the_stop_moves_to_breakeven_plus_fees() -> None:
    venue = SimVenue(mark=Decimal("110"))
    store = MemoryStore()
    manager = _manager(venue, store)
    report = manager.step(_position())
    assert venue.placed == [(_client("t1"), "sell", Decimal("0.300"))]
    assert report.placed == (_client("t1"),)
    # The fill is read on the next step; only then is TP1 confirmed.
    manager.step(_position())
    assert _stop(store) == Decimal("100.5")  # entry 100 + 0.5 fees per unit
    moves = store.load(COMMAND).stop_moves
    assert [(move.key, move.previous_stop, move.new_stop) for move in moves] == [
        ("breakeven", Decimal("95"), Decimal("100.5"))
    ]
    assert len(venue.placed) == 1
    # The mark falls back through breakeven: the remainder is closed once.
    venue.mark = Decimal("100.5")
    manager.step(_position())
    assert venue.placed[1] == (_client("x"), "sell", Decimal("0.700"))
    assert manager.step(_position()).status == "closed"
    assert len(venue.placed) == 2


def test_initial_stop_closes_everything_before_tp1() -> None:
    venue = SimVenue(mark=Decimal("94.9"))
    store = MemoryStore()
    manager = _manager(venue, store)
    manager.step(_position())
    assert venue.placed == [(_client("x"), "sell", Decimal("1.000"))]
    assert manager.step(_position()).status == "closed"
    assert [fill.kind for fill in store.load(COMMAND).fills] == ["stop"]


# TP2-TP4 partials.


def test_tp2_to_tp4_close_once_each_with_exact_sizes() -> None:
    venue = SimVenue()
    store = MemoryStore()
    manager = _manager(venue, store)
    for mark in ("110", "110", "120", "120", "130", "130", "140", "140", "140"):
        venue.mark = Decimal(mark)
        manager.step(_position())
    assert venue.placed == [
        (_client("t1"), "sell", Decimal("0.300")),
        (_client("t2"), "sell", Decimal("0.200")),
        (_client("t3"), "sell", Decimal("0.200")),
        (_client("t4"), "sell", Decimal("0.100")),
    ]
    assert venue.position == Decimal("0.200")  # the runner stays open
    state_fills = store.load(COMMAND).fills
    assert [(fill.leg_order, fill.quantity) for fill in state_fills] == [
        (1, Decimal("0.300")),
        (2, Decimal("0.200")),
        (3, Decimal("0.200")),
        (4, Decimal("0.100")),
    ]


def test_a_gap_through_two_targets_closes_both_legs_without_exceeding_the_position() -> None:
    venue = SimVenue(mark=Decimal("125"))
    manager = _manager(venue, MemoryStore())
    manager.step(_position())
    assert venue.placed == [
        (_client("t1"), "sell", Decimal("0.300")),
        (_client("t2"), "sell", Decimal("0.200")),
    ]


# Runner trails the last closed swing.


def test_runner_trails_the_last_closed_swing_and_closes_when_crossed() -> None:
    venue = SimVenue()
    store = MemoryStore()
    manager = _manager(venue, store)
    for mark in ("140", "140"):
        venue.mark = Decimal(mark)
        manager.step(_position())
    assert venue.position == Decimal("0.200")
    bars = (_bar(0, "128", "141"), _bar(1, "133", "142"), _bar(2, "120", "143"))
    evaluated_at = bars[1].interval_end + STEP / 2  # bar 2 is still forming
    venue.mark = Decimal("135")
    manager.step(_position(), bars=bars, evaluated_at=evaluated_at)
    # Lowest low of the last two closed bars; the forming bar's 120 is ignored.
    assert _stop(store) == Decimal("128")
    assert len(venue.placed) == 4
    venue.mark = Decimal("127.9")
    manager.step(_position(), bars=bars, evaluated_at=evaluated_at)
    assert venue.placed[-1] == (_client("x"), "sell", Decimal("0.200"))
    assert manager.step(_position()).status == "closed"
    assert store.load(COMMAND).fills[-1].kind == "runner_stop"


def test_trail_never_loosens() -> None:
    venue = SimVenue()
    store = MemoryStore()
    manager = _manager(venue, store)
    for _ in range(2):
        venue.mark = Decimal("140")
        manager.step(_position())
    high = (_bar(0, "130", "141"), _bar(1, "131", "142"))
    manager.step(_position(), bars=high, evaluated_at=high[1].interval_end)
    low = (*high, _bar(2, "110", "143"), _bar(3, "111", "144"))
    manager.step(_position(), bars=low, evaluated_at=low[3].interval_end)
    assert _stop(store) == Decimal("130")


# Restart and retries.


def test_restart_mid_ladder_with_a_close_in_flight_never_sends_a_duplicate() -> None:
    venue = SimVenue(mark=Decimal("110"), defer=True)
    store = MemoryStore()
    _manager(venue, store).step(_position())
    assert len(venue.placed) == 1
    # Worker restarts while the TP1 close is still live at the venue.
    report = _manager(venue, store).step(_position())
    assert (report.status, report.reason) == ("held", "close_in_flight")
    assert len(venue.placed) == 1
    venue.settle(next(iter(venue.orders)))
    _manager(venue, store).step(_position())
    assert len(venue.placed) == 1
    assert _stop(store) == Decimal("100.5")


def test_restart_with_an_empty_store_rebuilds_from_venue_fills_without_reclosing() -> None:
    venue = SimVenue(mark=Decimal("120"))
    manager = _manager(venue, MemoryStore())
    manager.step(_position())
    manager.step(_position())
    placed = list(venue.placed)
    fresh = MemoryStore()
    _manager(venue, fresh).step(_position())
    assert venue.placed == placed
    assert [fill.leg_order for fill in fresh.load(COMMAND).fills] == [1, 2]
    assert _stop(fresh) == Decimal("100.5")


def test_partial_fill_of_a_close_is_completed_by_a_second_attempt_for_the_remainder() -> None:
    venue = SimVenue(mark=Decimal("110"), fill_ratio=Decimal("1") / Decimal("3"))
    store = MemoryStore()
    manager = _manager(venue, store)
    manager.step(_position())
    assert venue.placed == [(_client("t1"), "sell", Decimal("0.300"))]
    assert venue.position == Decimal("0.900")
    venue.fill_ratio = Decimal("1")
    manager.step(_position())
    # TP1 is not complete yet, so the stop has not moved.
    assert venue.placed[1] == (_client("t1", 1), "sell", Decimal("0.200"))
    assert store.load(COMMAND).stop_moves == ()
    manager.step(_position())
    assert _stop(store) == Decimal("100.5")
    assert len(venue.placed) == 2


def test_failed_placement_records_nothing_and_retries_the_same_client_id() -> None:
    venue = SimVenue(mark=Decimal("110"), fail_place=True)
    store = MemoryStore()
    report = _manager(venue, store).step(_position())
    assert "close_place_failed" in report.alerts
    assert store.load(COMMAND).fills == ()
    venue.fail_place = False
    _manager(venue, store).step(_position())
    assert venue.placed == [(_client("t1"), "sell", Decimal("0.300"))]
    assert [call for call in venue.calls if call[0] == "place"] == [
        ("place", _client("t1")),
        ("place", _client("t1")),
    ]


# Never increase exposure.


@pytest.mark.parametrize("venue_position", ["1.200", "0.700", "0", "-0.500"])
def test_venue_position_that_differs_from_the_record_holds_with_no_order(
    venue_position: str,
) -> None:
    venue = SimVenue(mark=Decimal("150"))
    venue.position = Decimal(venue_position)
    report = _manager(venue, MemoryStore()).step(_position())
    assert (report.status, report.reason) == ("held", "venue_position_mismatch")
    assert venue.placed == []


def test_an_unknown_order_under_our_prefix_holds() -> None:
    venue = SimVenue(mark=Decimal("150"))
    prefix = GovernedDemoPartialExitManager.close_client_id(COMMAND, "", 0)[:-2]
    venue.orders["odd"] = VenueCloseOrder(
        "odd", prefix + "zz", "sell", Decimal("0.1"), Decimal("0"), True, "canceled"
    )
    report = _manager(venue, MemoryStore()).step(_position())
    assert (report.status, report.reason) == ("held", "unexplained_order")
    assert venue.placed == []


def test_a_non_reduce_only_or_wrong_side_order_of_ours_holds() -> None:
    venue = SimVenue(mark=Decimal("150"))
    venue.orders["bad"] = VenueCloseOrder(
        "bad", _client("t1"), "buy", Decimal("0.3"), Decimal("0"), False, "canceled"
    )
    report = _manager(venue, MemoryStore()).step(_position())
    assert (report.status, report.reason) == ("held", "order_shape_unexpected")
    assert venue.placed == []


def test_client_ids_are_short_alphanumeric_and_derived_from_command_leg_and_attempt() -> None:
    ids = {_client("t1"), _client("t1", 1), _client("t4"), _client("x"), _client("x", 2)}
    assert len(ids) == 5
    assert all(value.isalnum() and len(value) <= 32 for value in ids)
    other = GovernedDemoPartialExitManager.close_client_id(
        UUID("50000000-0000-4000-8000-0000000000d6"), "t1", 0
    )
    assert other != _client("t1")


# Shorts mirror.


def test_short_ladder_closes_with_buy_and_moves_breakeven_down() -> None:
    venue = SimVenue(long=False, mark=Decimal("90"))
    store = MemoryStore()
    manager = _manager(venue, store)
    position = _position(TradeDirection.SHORT)
    manager.step(position)
    assert venue.placed == [(_client("t1"), "buy", Decimal("0.300"))]
    manager.step(position)
    assert _stop(store) == Decimal("99.5")
    venue.mark = Decimal("99.5")
    manager.step(position)
    assert venue.placed[-1] == (_client("x"), "buy", Decimal("0.700"))
