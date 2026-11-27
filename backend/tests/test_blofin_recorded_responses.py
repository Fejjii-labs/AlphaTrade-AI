"""Replay recorded BloFin demo responses through the real HTTP client (audit M-13).

The fixtures are real demo replies (27 Nov 2026) with ids and keys removed. They go
through ``BloFinClient`` with the full ``{code, msg, data}`` envelope, so the parsing
code sees the venue's real field names and types instead of hand-written fakes.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import structlog

from app.providers.exchange.blofin_account import BloFinAccountProvider
from app.providers.exchange.blofin_client import BloFinClient
from app.services.demo_lifecycle_resolution import demo_fill_fee_cost
from app.services.trade_review import bars_from_blofin

FIXTURE = Path(__file__).parent / "fixtures" / "blofin_demo_recorded" / "responses.json"


def _endpoints() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text())["_endpoints"]
    return data


def _client() -> BloFinClient:
    by_path = {e["path"]: e["response"] for e in _endpoints().values()}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"  # replay is read-only
        return httpx.Response(200, json=by_path[request.url.path])

    return BloFinClient(
        base_url="https://demo-trading-openapi.blofin.com",
        api_key="k",
        api_secret="s",
        api_passphrase="p",
        transport=httpx.MockTransport(handler),
        sleeper=lambda _: None,
    )


def test_fixture_holds_no_ids_or_keys() -> None:
    for endpoint in _endpoints().values():
        rows = endpoint["response"]["data"]
        for row in rows if isinstance(rows, list) else [rows]:
            if not isinstance(row, dict):
                continue
            for key in ("apiKey", "uid", "orderId", "tradeId", "tpslId", "clientOrderId"):
                assert row.get(key) in (None, "", f"<{key}>")


def test_real_key_reply_reports_trade_but_not_money_movement_scope() -> None:
    permissions = BloFinAccountProvider(_client()).get_account_permissions()
    assert permissions.can_read and permissions.can_trade
    assert not permissions.can_transfer and not permissions.can_withdraw
    # The real reply only says read-only or not: transfer/withdraw cannot be seen.
    assert permissions.money_scope_known is False
    assert "readOnly" in permissions.response_keys


def test_startup_check_warns_when_the_money_scope_is_not_reported() -> None:
    from app.providers.exchange.factory import _verify_no_money_movement as check

    with structlog.testing.capture_logs() as logs:
        check(BloFinAccountProvider(_client()))
    assert any(e["event"] == "blofin_key_money_scope_not_reported" for e in logs)


def test_real_fills_reply_has_positive_paid_fees_and_string_numbers() -> None:
    rows = _client().request("GET", "/api/v1/trade/fills-history", signed=True)
    assert rows and all(isinstance(r["fee"], str) for r in rows)
    assert all(demo_fill_fee_cost(Decimal(r["fee"])) > 0 for r in rows)
    assert {r["positionSide"] for r in rows} == {"net"}


def test_real_backup_stop_row_is_a_whole_position_sl_with_market_close() -> None:
    rows = _client().request("GET", "/api/v1/trade/orders-tpsl-history", signed=True)
    stop = next(r for r in rows if r["triggerType"] == "sl")
    assert stop["slOrderPrice"] == "-1" and stop["reduceOnly"] == "true"
    assert stop["tpTriggerPrice"] is None


def test_real_positions_reply_with_no_open_position() -> None:
    assert BloFinAccountProvider(_client()).get_positions() == []


def test_real_candles_feed_the_trade_review() -> None:
    rows = _client().request("GET", "/api/v1/market/candles")
    bars = bars_from_blofin(rows)
    assert len(bars) == len(rows) == 3
    assert bars == sorted(bars, key=lambda b: b.start)
    assert all(b.high >= b.low for b in bars)
