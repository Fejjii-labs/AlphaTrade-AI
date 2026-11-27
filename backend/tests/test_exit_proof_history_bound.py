"""Audit M-3: orders after a trade is fully closed no longer block its exit proof."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.test_demo_attached_tpsl_exit import _ms, attached_world


def _later_trade_on_same_coin(responses, *, seconds: int = 60) -> None:
    close, parent = responses["orders-history"]
    responses["orders-history"] = [
        {
            **parent,
            "orderId": "next-entry",
            "clientOrderId": "ATnext",
            "createTime": _ms(timedelta(seconds=seconds)),
        },
        {
            **close,
            "orderId": "next-close",
            "createTime": _ms(timedelta(seconds=seconds + 1)),
        },
        close,
        parent,
    ]


def test_a_later_trade_on_the_same_coin_does_not_block_the_proof():
    plan, entry, provider, responses, _ = attached_world()
    _later_trade_on_same_coin(responses)
    closed = provider.reconcile_exit(plan=plan, entry=entry)
    assert closed is not None
    assert closed.protection_ids == ("10004687495",)


def test_an_unexplained_order_before_the_close_still_holds_the_exit():
    plan, entry, provider, responses, _ = attached_world()
    close, parent = responses["orders-history"]
    stray = {
        **parent,
        "orderId": "stray",
        "clientOrderId": "",
        "createTime": _ms(timedelta(milliseconds=500)),  # between entry and close
    }
    responses["orders-history"] = [close, stray, parent]
    with pytest.raises(ValueError):
        provider.reconcile_exit(plan=plan, entry=entry)


def test_a_full_history_page_is_paged_instead_of_refused():
    plan, entry, provider, responses, _ = attached_world()
    close, parent = responses["orders-history"]
    later = [
        {
            **parent,
            "orderId": f"later-{n}",
            "clientOrderId": f"ATlater{n}",
            "createTime": _ms(timedelta(minutes=5 + n)),
        }
        for n in range(99)
    ]
    pages = [[*later, close], [parent]]  # newest first, 100 then 1
    calls: list[dict] = []
    request = provider._client.request

    def paging(method, path, *args, **kwargs):
        if path.endswith("orders-history"):
            calls.append(dict(kwargs.get("params") or {}))
            return pages[len(calls) - 1]
        return request(method, path, *args, **kwargs)

    provider._client.request = paging
    closed = provider.reconcile_exit(plan=plan, entry=entry)
    assert closed is not None
    assert len(calls) == 2 and calls[1]["after"] == "close-1"
