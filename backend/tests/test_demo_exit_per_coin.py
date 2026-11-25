"""Demo data mode allows one bot entry per coin, so exit proof is per coin.

Staging opened a BTC short while a HYPE short was open. The account-wide exit
check read the second position as unexplained inventory and turned the kill
switch on. In demo data mode another coin's position, orders and protection
belong to another trade; this coin's rows are still checked exactly as before.
"""

from decimal import Decimal

import pytest

from app.providers.exchange.governed_blofin_exit import DemoPositionLineageError
from tests.test_demo_attached_tpsl_exit import attached_world

OTHER = "HYPE-USDT"


def _other_coin_trade(responses) -> None:
    responses["positions"] = [
        {"instId": OTHER, "positions": "-9800", "positionSide": "net", "marginMode": "cross"}
    ]
    responses["orders-tpsl-pending"] = [{"instId": OTHER, "tpslId": "hype-tpsl", "state": "live"}]
    responses["orders-history"].append(
        {
            "orderId": "hype-entry",
            "clientOrderId": "AThype",
            "instId": OTHER,
            "side": "sell",
            "positionSide": "net",
            "marginMode": "cross",
            "state": "filled",
            "reduceOnly": "false",
            "orderCategory": "normal",
            "size": "9800",
            "filledSize": "9800",
            "createTime": responses["orders-history"][0]["createTime"],
        }
    )


def _record_calls(provider):
    calls: list[tuple[str, dict]] = []
    request = provider._client.request

    def recording(method, path, *args, **kwargs):
        calls.append((path, dict(kwargs.get("params") or {})))
        return request(method, path, *args, **kwargs)

    provider._client.request = recording
    return calls


def test_account_wide_mode_still_holds_on_another_coins_position():
    plan, entry, provider, responses, _ = attached_world()
    _other_coin_trade(responses)
    with pytest.raises(DemoPositionLineageError):
        provider.reconcile_exit(plan=plan, entry=entry)


def test_per_coin_close_is_verified_while_another_coin_trade_is_open():
    plan, entry, provider, responses, _ = attached_world()
    _other_coin_trade(responses)
    provider.flat_per_instrument = True
    calls = _record_calls(provider)
    closed = provider.reconcile_exit(plan=plan, entry=entry)
    assert closed is not None
    assert closed.protection_ids == ("10004687495",)
    history = [params for path, params in calls if path.endswith("orders-history")]
    assert history and all(p.get("instId") == plan.execution_instrument for p in history)


def test_per_coin_open_trade_stays_open_beside_another_coin():
    plan, entry, provider, responses, _ = attached_world()
    _other_coin_trade(responses)
    responses["positions"].append(
        {
            "instId": plan.execution_instrument,
            "positions": str(-plan.quantity.value),
            "positionSide": "net",
            "marginMode": "cross",
        }
    )
    provider.flat_per_instrument = True
    assert provider.reconcile_exit(plan=plan, entry=entry) is None


def test_per_coin_still_holds_when_this_coins_position_exceeds_our_fills():
    plan, entry, provider, responses, _ = attached_world()
    _other_coin_trade(responses)
    responses["positions"].append(
        {
            "instId": plan.execution_instrument,
            "positions": str(-plan.quantity.value * 2),
            "positionSide": "net",
            "marginMode": "cross",
        }
    )
    provider.flat_per_instrument = True
    with pytest.raises(DemoPositionLineageError):
        provider.reconcile_exit(plan=plan, entry=entry)


def test_per_coin_never_skips_a_position_without_a_coin():
    plan, entry, provider, responses, _ = attached_world()
    responses["positions"] = [{"positions": "-1", "positionSide": "net", "marginMode": "cross"}]
    provider.flat_per_instrument = True
    with pytest.raises(DemoPositionLineageError):
        provider.reconcile_exit(plan=plan, entry=entry)


def test_per_coin_pending_order_on_this_coin_keeps_the_exit_open():
    plan, entry, provider, responses, _ = attached_world()
    _other_coin_trade(responses)
    responses["orders-pending"] = [{"instId": plan.execution_instrument, "orderId": "x"}]
    provider.flat_per_instrument = True
    assert provider.reconcile_exit(plan=plan, entry=entry) is None


def test_partial_exit_position_ignores_other_coins_only_per_coin():
    plan, _, provider, responses, _ = attached_world()
    _other_coin_trade(responses)
    responses["positions"].append(
        {"instId": plan.execution_instrument, "positions": "-3", "positionSide": "net"}
    )
    provider.partial_exits_enabled = True
    with pytest.raises(ValueError, match="Unrelated demo account inventory"):
        provider.partial_exit_venue(plan).position_quantity()
    provider.flat_per_instrument = True
    assert provider.partial_exit_venue(plan).position_quantity() == Decimal("-3")
