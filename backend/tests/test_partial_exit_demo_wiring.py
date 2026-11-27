"""Wiring governed BloFin demo partial exits into submit, the venue and the loop.

- Flag off: demo submit still refuses ladders and the loop never builds a
  partial-exit venue, so behaviour is exactly today's.
- Flag on: a sizable ladder is sent as a plain market entry with no attached
  TP/SL (attached TP/SL sizing after partial reductions is undocumented); an
  unsizable ladder or a manual demo plan is refused before any POST.
- The BloFin adapter sends only market ``reduceOnly`` closes on the plan's
  instrument, net position side, only to the allowlisted demo host.
- The loop runs the manager instead of arming the kill switch for "protection
  missing" on a ladder entry. The kill switch, the worker pause and the daily
  loss lock hold target closes as entries are refused, but a crossed stop
  still closes; an unreadable hold gate touches no venue.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from app.core.config import ExchangeMode, Settings
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import DemoFill
from app.providers.exchange.governed_blofin_partial_exits import BloFinDemoReduceOnlyVenue
from app.schemas.trade_plan import EntrySide
from app.services.governed_blofin_demo import GovernedBloFinDemoLoop, demo_ladder_hold_reason
from app.services.partial_exits.demo import GovernedDemoPartialExitManager, VenueCloseOrder
from tests.test_partial_exit_demo import (
    COMMAND,
    ORG,
    T0,
    MemoryStore,
    SimVenue,
    four_target_payload,
)

DEMO_URL = "https://demo-trading-openapi.blofin.com"


def _demo_settings(enabled: bool = True) -> Settings:
    settings = Settings(_env_file=None, governed_demo_partial_exits_enabled=enabled)  # type: ignore[call-arg]
    return settings.model_copy(update={"exchange_mode": ExchangeMode.PAPER_EXCHANGE_DEMO})


# Submit.


def _ladder_plan(*, runner: bool) -> Any:
    from tests.test_governed_blofin_quote_freshness import _plan
    from tests.test_partial_exit_spec_gate import _multi_target

    plan = _multi_target(_plan(valid_for_ms=6000), runner=runner)
    if runner:
        risk = plan.risk_and_exits
        plan = plan.model_copy(
            update={
                "risk_and_exits": risk.model_copy(
                    update={
                        "runner": risk.runner.model_copy(
                            update={
                                "rule_id": "closed-candle-trail",
                                "rule_version": "1",
                                "expression": "lookback_bars=2",
                            }
                        )
                    }
                )
            }
        )
    return plan


def _submit_provider(venue_handle: Any, *, partial_exits: bool) -> Any:
    from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
    from tests.support.phase5_market import EVALUATED_AT

    return GovernedBloFinDemoProvider(
        BloFinClient(
            base_url=DEMO_URL,
            api_key="k",
            api_secret="s",
            api_passphrase="p",
            transport=httpx.MockTransport(venue_handle),
            max_retries=0,
            sleeper=lambda _: None,
        ),
        clock=lambda: EVALUATED_AT,
        partial_exits=partial_exits,
    )


@pytest.mark.parametrize("runner", [False, True])
def test_flag_on_submits_a_ladder_as_a_plain_market_entry_without_attached_tp_or_sl(
    runner: bool,
) -> None:
    from tests.test_governed_blofin_demo import Venue

    venue = Venue(Decimal("100000"))
    posts: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        # The shared simulated venue asserts attached protection on every
        # entry; a ladder entry deliberately has none, so capture it here.
        if request.method == "POST" and request.url.path == "/api/v1/trade/order":
            posts.append(json.loads(request.content))
            data = [{"orderId": "demo-1", "clientOrderId": posts[-1]["clientOrderId"]}]
            return httpx.Response(200, json={"code": "0", "data": data})
        return venue.handle(request)

    plan = _ladder_plan(runner=runner)
    provider = _submit_provider(handle, partial_exits=True)
    assert provider.submit(plan=plan, client_order_id="ladder-entry", before_post=Mock())
    (order,) = posts
    assert order["orderType"] == "market"
    assert order["positionSide"] == "net"
    assert order["size"] == str(plan.quantity.value)
    for key in ("tpTriggerPrice", "tpOrderPrice", "slTriggerPrice", "slOrderPrice"):
        assert key not in order
    assert "reduceOnly" not in order


@pytest.mark.parametrize("partial_exits", [False, True])
def test_single_target_submit_is_unchanged_by_the_flag(partial_exits: bool) -> None:
    from tests.test_governed_blofin_demo import Venue
    from tests.test_governed_blofin_quote_freshness import _plan

    venue = Venue(Decimal("100000"))
    plan = _plan(valid_for_ms=6000)
    _submit_provider(venue.handle, partial_exits=partial_exits).submit(
        plan=plan, client_order_id="single", before_post=Mock()
    )
    assert venue.order is not None
    assert venue.order["slTriggerPrice"] == str(plan.risk_and_exits.stop.value)
    assert venue.order["tpTriggerPrice"] == str(plan.risk_and_exits.targets[0].price.value)


def test_flag_off_submit_still_refuses_a_ladder() -> None:
    from tests.test_governed_blofin_demo import Venue

    venue = Venue(Decimal("100000"))
    before_post = Mock()
    with pytest.raises(ValueError, match="one full-size target"):
        _submit_provider(venue.handle, partial_exits=False).submit(
            plan=_ladder_plan(runner=False), client_order_id="x", before_post=before_post
        )
    before_post.assert_not_called()
    assert venue.post_count == 0


def test_flag_on_refuses_an_unmanageable_ladder_before_any_post() -> None:
    from tests.test_governed_blofin_demo import Venue
    from tests.test_governed_blofin_quote_freshness import _plan
    from tests.test_partial_exit_spec_gate import _multi_target

    venue = Venue(Decimal("100000"))
    plan = _multi_target(_plan(valid_for_ms=6000), runner=True)  # atr runner: unsupported
    before_post = Mock()
    with pytest.raises(ValueError, match="runner_rule_unsupported"):
        _submit_provider(venue.handle, partial_exits=True).submit(
            plan=plan, client_order_id="x", before_post=before_post
        )
    before_post.assert_not_called()
    assert venue.post_count == 0


def test_flag_on_still_refuses_a_ladder_on_a_manual_demo_plan() -> None:
    from tests.test_governed_blofin_demo import Venue

    venue = Venue(Decimal("100000"))
    plan = _ladder_plan(runner=False).model_copy(update={"schema_version": "ManualDemoTradePlanV1"})
    with pytest.raises(ValueError, match="one full-size target"):
        _submit_provider(venue.handle, partial_exits=True).submit(
            plan=plan, client_order_id="x", before_post=Mock()
        )
    assert venue.post_count == 0


def test_loop_builds_its_provider_with_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.governed_blofin_demo as module

    client = BloFinClient(
        base_url=DEMO_URL, api_key="k", api_secret="s", api_passphrase="p", sleeper=lambda _: None
    )
    monkeypatch.setattr(module, "build_blofin_client", lambda settings: client)
    clock = SimpleNamespace(now=lambda: T0)
    for enabled in (False, True):
        loop = GovernedBloFinDemoLoop(None, _demo_settings(enabled), clock)  # type: ignore[arg-type]
        assert loop._get_provider().partial_exits_enabled is enabled
    live = _demo_settings(True).model_copy(update={"exchange_mode": ExchangeMode.PAPER_INTERNAL})
    loop = GovernedBloFinDemoLoop(None, live, clock)  # type: ignore[arg-type]
    assert loop._get_provider().partial_exits_enabled is False


# BloFin adapter.


class Recorder:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, str, dict[str, str], Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, dict(request.url.params), body))
        data = self.routes.get(request.url.path, [])
        return httpx.Response(200, json={"code": "0", "msg": "", "data": data})


def _plan_stub(side: EntrySide = EntrySide.BUY) -> Any:
    return SimpleNamespace(
        execution_instrument="BTC-USDT",
        side=side,
        timeframe="15m",
        valid_from=T0 - timedelta(hours=1),
        margin_mode=SimpleNamespace(value="isolated"),
        risk_and_exits=SimpleNamespace(margin_assumption_id="configured-demo-leverage"),
    )


def _adapter(
    routes: dict[str, Any], *, base_url: str = DEMO_URL, side: EntrySide = EntrySide.BUY
) -> tuple[BloFinDemoReduceOnlyVenue, Recorder, Mock]:
    recorder = Recorder(routes)
    client = BloFinClient(
        base_url=base_url,
        api_key="k",
        api_secret="s",
        api_passphrase="p",
        transport=httpx.MockTransport(recorder),
        max_retries=0,
        sleeper=lambda _: None,
    )
    identity = Mock()
    return (
        BloFinDemoReduceOnlyVenue(client, plan=_plan_stub(side), require_identity=identity),
        recorder,
        identity,
    )


def test_adapter_places_only_market_reduce_only_net_closes() -> None:
    venue, recorder, identity = _adapter(
        {"/api/v1/trade/order": [{"orderId": "c1", "clientOrderId": "pe1t1n0", "code": "0"}]}
    )
    assert (
        venue.place_reduce_only_market(
            client_order_id="pe1t1n0", side="sell", size=Decimal("0.300")
        )
        == "c1"
    )
    identity.assert_called_once_with()
    method, path, _params, body = recorder.requests[-1]
    assert (method, path) == ("POST", "/api/v1/trade/order")
    assert body == {
        "instId": "BTC-USDT",
        "marginMode": "isolated",
        "positionSide": "net",
        "side": "sell",
        "orderType": "market",
        "size": "0.300",
        "reduceOnly": "true",
        "clientOrderId": "pe1t1n0",
    }


@pytest.mark.parametrize("side", ["buy", "BUY", ""])
def test_adapter_refuses_the_entry_side_for_a_long(side: str) -> None:
    venue, recorder, _identity = _adapter({})
    with pytest.raises(ValueError):
        venue.place_reduce_only_market(client_order_id="pe1t1n0", side=side, size=Decimal("1"))
    assert recorder.requests == []


def test_adapter_refuses_a_non_demo_host_before_any_request() -> None:
    # BloFinClient itself refuses a production host in demo mode; the adapter
    # checks again so a differently built client still cannot send a close.
    client = Mock(base_url="https://openapi.blofin.com")
    identity = Mock()
    venue = BloFinDemoReduceOnlyVenue(client, plan=_plan_stub(), require_identity=identity)
    with pytest.raises(ValueError):
        venue.place_reduce_only_market(client_order_id="pe1t1n0", side="sell", size=Decimal("1"))
    client.request.assert_not_called()
    identity.assert_not_called()


def test_adapter_reads_our_closing_orders_and_their_fills() -> None:
    venue, recorder, _identity = _adapter(
        {
            "/api/v1/trade/orders-pending": [
                {
                    "orderId": "c2",
                    "clientOrderId": "pe1t2n0",
                    "side": "sell",
                    "size": "0.2",
                    "filledSize": "0",
                    "reduceOnly": "true",
                    "state": "live",
                },
                {"orderId": "z", "clientOrderId": "other", "state": "live"},
            ],
            "/api/v1/trade/orders-history": [
                {
                    "orderId": "c1",
                    "clientOrderId": "pe1t1n0",
                    "side": "sell",
                    "size": "0.3",
                    "filledSize": "0.1",
                    "reduceOnly": "true",
                    "state": "canceled",
                },
                {"orderId": "entry", "clientOrderId": "entry-client", "state": "filled"},
            ],
            "/api/v1/trade/fills-history": [
                {
                    "orderId": "c1",
                    "tradeId": "t9",
                    "fillSize": "0.1",
                    "fillPrice": "110",
                    "ts": str(int((T0 + timedelta(minutes=1)).timestamp() * 1000)),
                }
            ],
        }
    )
    orders = venue.closing_orders("pe1")
    assert [(o.order_id, o.state, o.filled, o.reduce_only) for o in orders] == [
        ("c2", "live", Decimal("0"), True),
        ("c1", "canceled", Decimal("0.1"), True),
    ]
    history = next(r for r in recorder.requests if r[1].endswith("orders-history"))
    assert history[2]["instId"] == "BTC-USDT"
    assert history[2]["begin"] == str(int((T0 - timedelta(hours=1)).timestamp() * 1000))
    (fill,) = venue.exit_fills(("c1",))
    assert (fill.order_id, fill.trade_id, fill.client_order_id) == ("c1", "t9", "pe1t1n0")
    assert (fill.quantity, fill.price) == (Decimal("0.1"), Decimal("110"))


def test_adapter_position_is_signed_and_refuses_other_inventory() -> None:
    venue, _recorder, _identity = _adapter(
        {
            "/api/v1/account/positions": [
                {"instId": "BTC-USDT", "positionSide": "net", "positions": "-0.7"}
            ]
        }
    )
    assert venue.position_quantity() == Decimal("-0.7")
    other, _recorder, _identity = _adapter(
        {
            "/api/v1/account/positions": [
                {"instId": "ETH-USDT", "positionSide": "net", "positions": "1"}
            ]
        }
    )
    with pytest.raises(ValueError):
        other.position_quantity()


def test_adapter_mark_and_closed_bars() -> None:
    start = int(T0.timestamp() * 1000)
    venue, recorder, _identity = _adapter(
        {
            "/api/v1/market/tickers": [{"instId": "BTC-USDT", "last": "123.4"}],
            "/api/v1/market/candles": [
                [str(start + 900_000), "2", "3", "1", "2", "5"],
                [str(start), "1", "2", "0.5", "1.5", "5"],
            ],
        }
    )
    assert venue.mark_price() == Decimal("123.4")
    bars = venue.closed_bars(evaluated_at=T0 + timedelta(minutes=20), limit=10)
    # Oldest first; the bar ending after evaluated_at is still forming.
    assert [(bar.interval_start, bar.low) for bar in bars] == [(T0, Decimal("0.5"))]
    candles = next(r for r in recorder.requests if r[1].endswith("candles"))
    assert candles[2]["bar"] == "15m"


# Loop.


def test_hold_reason_matches_the_entry_gates_and_fails_closed() -> None:
    def no() -> bool:
        return False

    def yes() -> bool:
        return True

    def boom() -> bool:
        raise RuntimeError("db down")

    assert demo_ladder_hold_reason(kill_blocked=no, paused=no, daily_locked=no) is None
    assert demo_ladder_hold_reason(kill_blocked=yes, paused=no, daily_locked=no) == (
        "kill_switch_active"
    )
    assert demo_ladder_hold_reason(kill_blocked=no, paused=yes, daily_locked=no) == (
        "worker_paused"
    )
    assert demo_ladder_hold_reason(kill_blocked=no, paused=no, daily_locked=yes) == (
        "daily_loss_lock"
    )
    assert demo_ladder_hold_reason(kill_blocked=boom, paused=no, daily_locked=no) == (
        "hold_state_unavailable"
    )


class LoopVenue(SimVenue):
    def closed_bars(self, *, evaluated_at: datetime, limit: int) -> tuple[Any, ...]:
        return ()


def _loop(settings: Settings, venue: SimVenue | None, *, hold: str | None = None) -> Any:
    provider = SimpleNamespace(partial_exit_venue=lambda plan: venue)
    loop = GovernedBloFinDemoLoop(
        None,  # type: ignore[arg-type]
        settings,
        SimpleNamespace(now=lambda: T0 + timedelta(hours=1)),  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
    )
    store = MemoryStore()
    loop._demo_ladder_store = lambda session, command, terms: store  # type: ignore[method-assign]
    loop._demo_ladder_hold = lambda session, command: hold  # type: ignore[method-assign]
    return loop, store


def _ladder_inputs(payload: dict[str, object] | None = None) -> tuple[Any, Any, Any]:
    plan = SimpleNamespace(
        side=EntrySide.BUY,
        schema_version="TradePlanRevisionV1",
        model_dump=lambda mode="json": payload or four_target_payload(),
    )
    command = SimpleNamespace(id=COMMAND, organization_id=ORG)
    evidence = SimpleNamespace(
        status="filled",
        fills=(
            DemoFill("f1", Decimal("0.500"), Decimal("99"), T0, Decimal("0")),
            DemoFill("f2", Decimal("0.500"), Decimal("101"), T0, Decimal("0")),
        ),
    )
    return plan, command, evidence


def test_loop_flag_off_leaves_the_existing_path_and_touches_no_venue() -> None:
    loop, store = _loop(_demo_settings(False), None)
    loop._provider = SimpleNamespace()  # any venue access would raise AttributeError
    plan, command, evidence = _ladder_inputs()
    assert loop._manage_demo_ladder(None, command, plan, evidence) is None
    assert store.records == {}


def test_loop_ignores_a_single_target_plan() -> None:
    loop, _store = _loop(_demo_settings(True), LoopVenue())
    payload = four_target_payload()
    risk = dict(payload["risk_and_exits"])  # type: ignore[arg-type]
    risk["targets"] = risk["targets"][:1]  # type: ignore[index]
    risk["targets"][0] = {**risk["targets"][0], "quantity_fraction": "1"}  # type: ignore[index]
    risk["runner"] = {**risk["runner"], "enabled": False, "remaining_quantity_fraction": "0"}  # type: ignore[dict-item]
    plan, command, evidence = _ladder_inputs({**payload, "risk_and_exits": risk})
    assert loop._manage_demo_ladder(None, command, plan, evidence) is None


def test_loop_manages_a_ladder_from_the_filled_entry() -> None:
    venue = LoopVenue(mark=Decimal("110"))
    loop, store = _loop(_demo_settings(True), venue)
    plan, command, evidence = _ladder_inputs()
    assert loop._manage_demo_ladder(None, command, plan, evidence) == "demo_ladder_managed"
    assert venue.placed[0][1:] == ("sell", Decimal("0.300"))
    assert loop._manage_demo_ladder(None, command, plan, evidence) == "demo_ladder_managed"
    # Breakeven uses the quantity-weighted entry fill (100) plus 0.5 fees.
    assert store.load(COMMAND).stop_moves[-1].new_stop == Decimal("100.5")


@pytest.mark.parametrize("reason", ["kill_switch_active", "worker_paused", "daily_loss_lock"])
def test_loop_hold_sends_no_target_close(reason: str) -> None:
    venue = LoopVenue(mark=Decimal("150"))
    loop, _store = _loop(_demo_settings(True), venue, hold=reason)
    plan, command, evidence = _ladder_inputs()
    assert loop._manage_demo_ladder(None, command, plan, evidence) == (f"demo_ladder_held_{reason}")
    assert venue.placed == []


def test_loop_unreadable_hold_touches_no_venue_even_through_the_stop() -> None:
    venue = LoopVenue(mark=Decimal("80"))
    loop, _store = _loop(_demo_settings(True), venue, hold="hold_state_unavailable")
    plan, command, evidence = _ladder_inputs()
    assert loop._manage_demo_ladder(None, command, plan, evidence) == (
        "demo_ladder_held_hold_state_unavailable"
    )
    assert venue.calls == []


@pytest.mark.parametrize("reason", ["kill_switch_active", "worker_paused", "daily_loss_lock"])
def test_loop_hold_still_sends_the_stop_close(reason: str) -> None:
    venue = LoopVenue(mark=Decimal("80"))  # through the 95 stop
    loop, _store = _loop(_demo_settings(True), venue, hold=reason)
    plan, command, evidence = _ladder_inputs()
    loop._manage_demo_ladder(None, command, plan, evidence)
    assert [(side, size) for _client_id, side, size in venue.placed] == [("sell", Decimal("1.000"))]


def test_loop_operator_hold_arms_the_kill_switch_and_still_sends_the_stop_close() -> None:
    venue = LoopVenue(mark=Decimal("80"))
    prefix = GovernedDemoPartialExitManager.client_prefix(COMMAND)
    venue.orders["odd"] = VenueCloseOrder(
        "odd", prefix + "zz", "sell", Decimal("0.1"), Decimal("0"), True, "canceled"
    )
    loop, _store = _loop(_demo_settings(True), venue, hold="kill_switch_active")
    armed: list[str] = []
    loop._arm_ladder_kill_switch = lambda session, command, detail: armed.append(detail)
    plan, command, evidence = _ladder_inputs()
    assert loop._manage_demo_ladder(None, command, plan, evidence) == "demo_ladder_operator_hold"
    assert armed == ["ladder held: unexplained_order"]
    assert [(side, size) for _client_id, side, size in venue.placed] == [("sell", Decimal("1.000"))]


def test_loop_waits_for_a_terminal_entry_before_any_close() -> None:
    venue = LoopVenue(mark=Decimal("150"))
    loop, _store = _loop(_demo_settings(True), venue)
    plan, command, evidence = _ladder_inputs()
    evidence.status = "partially_filled"
    assert loop._manage_demo_ladder(None, command, plan, evidence) == "demo_ladder_entry_open"
    assert venue.calls == []


def test_loop_runs_the_ladder_before_the_missing_protection_kill_switch() -> None:
    source = inspect.getsource(GovernedBloFinDemoLoop.reconcile_command)
    managed = source.index("_manage_demo_ladder")
    assert managed < source.index("BloFin demo protection missing")
    assert source.index("reconcile_exit") < managed


# Audit H-2: a failed close, backup stop or exit step pages Telegram, not just a log line.


def _captured_alerts(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    sent: list[Any] = []
    monkeypatch.setattr(
        "app.services.safety_alerts.queue_safety_alert",
        lambda session, settings, alert: sent.append(alert),
    )
    return sent


def _named_inputs() -> tuple[Any, Any, Any]:
    plan, command, evidence = _ladder_inputs()
    plan.execution_instrument = "BTC-USDT"
    return plan, command, evidence


def test_a_refused_close_pages_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _captured_alerts(monkeypatch)
    venue = LoopVenue(mark=Decimal("110"), fail_place=True)
    loop, _store = _loop(_demo_settings(True), venue)
    plan, command, evidence = _named_inputs()
    assert loop._manage_demo_ladder(None, command, plan, evidence) == "demo_ladder_managed"
    assert [a.code for a in sent] == ["demo_trade_protection_failed"]
    alert = sent[0]
    assert alert.telegram_verbatim and alert.severity == "critical"
    assert alert.message.splitlines()[0].endswith("Demo trade needs a look: BTC long")
    assert "refused a closing order" in alert.message
    assert alert.fields["dedup_key"].endswith(":close_place_failed")


def test_a_failed_step_pages_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _captured_alerts(monkeypatch)

    class Broken(LoopVenue):
        def mark_price(self) -> Decimal:
            raise RuntimeError("tickers unavailable")

    loop, _store = _loop(_demo_settings(True), Broken(mark=Decimal("105")))
    plan, command, evidence = _named_inputs()
    session = SimpleNamespace(rollback=lambda: None)
    assert loop._manage_demo_ladder(session, command, plan, evidence) == "demo_ladder_unavailable"
    assert [a.fields["dedup_key"].rsplit(":", 1)[1] for a in sent] == ["step_failed"]
    assert "could not run its exit check" in sent[0].message


def test_a_healthy_step_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _captured_alerts(monkeypatch)
    loop, _store = _loop(_demo_settings(True), LoopVenue(mark=Decimal("105")))
    plan, command, evidence = _named_inputs()
    assert loop._manage_demo_ladder(None, command, plan, evidence) == "demo_ladder_managed"
    assert sent == []


def test_the_protection_alert_is_mandatory_and_repeats_every_15_minutes() -> None:
    from app.services import safety_alerts

    assert "demo_trade_protection_failed" in safety_alerts.TELEGRAM_SAFETY_MANDATORY_CODES
    assert safety_alerts._cooldown("demo_trade_protection_failed").total_seconds() == 900


def test_a_position_mismatch_pages_only_after_five_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit H-6: a stuck trade (BloFin flat or different, bot not) is not silent."""

    from app.services import governed_blofin_demo

    sent = _captured_alerts(monkeypatch)
    governed_blofin_demo._MISMATCH_SINCE.clear()
    venue = LoopVenue(mark=Decimal("105"), position=Decimal("0.4"))
    loop, _store = _loop(_demo_settings(True), venue)
    clock = {"now": T0 + timedelta(hours=1)}
    loop._clock = SimpleNamespace(now=lambda: clock["now"])
    plan, command, evidence = _named_inputs()
    held = "demo_ladder_held_venue_position_mismatch"
    assert loop._manage_demo_ladder(None, command, plan, evidence) == held
    clock["now"] += timedelta(minutes=4)
    assert loop._manage_demo_ladder(None, command, plan, evidence) == held
    assert sent == []
    clock["now"] += timedelta(minutes=1)
    loop._manage_demo_ladder(None, command, plan, evidence)
    assert [a.fields["dedup_key"].rsplit(":", 1)[1] for a in sent] == ["position_mismatch"]
    assert "has not matched" in sent[0].message
    # Back in line: the timer resets.
    venue.position = Decimal("1.000")
    loop._manage_demo_ladder(None, command, plan, evidence)
    assert command.id not in governed_blofin_demo._MISMATCH_SINCE
