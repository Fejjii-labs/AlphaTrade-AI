"""BloFin demo venue port for bot-managed partial exits.

The only mutation is ``POST /api/v1/trade/order`` with ``orderType=market``,
``reduceOnly=true``, ``positionSide=net`` and the closing side of the plan,
sent only to an allowlisted demo host after the account identity is checked.
No exchange-side TP/SL is placed, replaced or cancelled: BloFin does not
document how an attached or standalone TP/SL is sized after the position is
partly reduced (https://docs.blofin.com/index.html, POST /api/v1/trade/order
and POST /api/v1/trade/order-tpsl).

Reads: our closing orders (pending + bounded history since ``valid_from``,
filtered by client id prefix), their fills, the net position, the last traded
price (``GET /api/v1/market/tickers``) and public candles for the runner.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.core.exchange_safety import is_allowlisted_demo_host
from app.market_contracts.identity import interval_timedelta
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import (
    _account_rows,
    _positive,
    _rows,
    _time,
    demo_order_margin_mode,
)
from app.providers.exchange.mapping import timeframe_to_bar
from app.schemas.common import Timeframe
from app.schemas.trade_plan import EntrySide, TradePlanRevision
from app.services.partial_exits.demo import CloseState, VenueCloseOrder, VenueExitFill

_STATES: dict[str, CloseState] = {
    "live": "live",
    "partially_filled": "partially_filled",
    "filled": "filled",
    "canceled": "canceled",
    "cancelled": "canceled",
    "expired": "canceled",
}


@dataclass(frozen=True, slots=True)
class DemoClosedBar:
    interval_start: datetime
    interval_end: datetime
    high: Decimal
    low: Decimal


class BloFinDemoReduceOnlyVenue:
    def __init__(
        self,
        client: BloFinClient,
        *,
        plan: TradePlanRevision,
        require_identity: Callable[[], None],
        per_instrument: bool = False,
    ) -> None:
        self._client = client
        self._plan = plan
        self._require_identity = require_identity
        self._client_ids: dict[str, str] = {}
        # Demo data mode: other coins' positions belong to other bot trades.
        self._per_instrument = per_instrument

    @property
    def _instrument(self) -> str:
        return self._plan.execution_instrument

    @property
    def close_side(self) -> str:
        return "sell" if self._plan.side is EntrySide.BUY else "buy"

    def closing_orders(self, client_prefix: str) -> tuple[VenueCloseOrder, ...]:
        if not client_prefix:
            raise ValueError("A closing-order prefix is required.")
        pending = _account_rows(
            self._client.request(
                "GET",
                "/api/v1/trade/orders-pending",
                params={"instId": self._instrument, "limit": "100"},
                signed=True,
            )
        )
        history = _account_rows(
            self._client.request(
                "GET",
                "/api/v1/trade/orders-history",
                params={
                    "instId": self._instrument,
                    "begin": str(int(self._plan.valid_from.timestamp() * 1000)),
                    "limit": "100",
                },
                signed=True,
            )
        )
        orders: dict[str, VenueCloseOrder] = {}
        for row in (*pending, *history):
            client_id = row.get("clientOrderId")
            if not isinstance(client_id, str) or not client_id.startswith(client_prefix):
                continue
            order_id = str(row.get("orderId") or "")
            state = _STATES.get(str(row.get("state")))
            if not order_id or state is None:
                raise ValueError("Unreadable demo closing order.")
            if order_id in orders:
                continue  # pending rows are read first and win
            filled = Decimal(str(row.get("filledSize") or "0"))
            if not filled.is_finite() or filled < 0:
                raise ValueError("Unreadable demo closing fill total.")
            orders[order_id] = VenueCloseOrder(
                order_id=order_id,
                client_order_id=client_id,
                side=str(row.get("side")),
                size=_positive(row.get("size")),
                filled=filled,
                reduce_only=row.get("reduceOnly") is True or row.get("reduceOnly") == "true",
                state=state,
            )
            self._client_ids[order_id] = client_id
        return tuple(orders.values())

    def exit_fills(self, order_ids: tuple[str, ...]) -> tuple[VenueExitFill, ...]:
        fills: list[VenueExitFill] = []
        for order_id in order_ids:
            client_id = self._client_ids.get(order_id)
            if client_id is None:
                raise ValueError("Fills requested for an order not read as ours.")
            rows = _account_rows(
                self._client.request(
                    "GET",
                    "/api/v1/trade/fills-history",
                    params={"instId": self._instrument, "orderId": order_id, "limit": "100"},
                    signed=True,
                )
            )
            for row in rows:
                if row.get("orderId") != order_id or not row.get("tradeId"):
                    raise ValueError("Demo closing fill identity mismatch.")
                fills.append(
                    VenueExitFill(
                        order_id=order_id,
                        trade_id=str(row["tradeId"]),
                        client_order_id=client_id,
                        quantity=_positive(row.get("fillSize")),
                        price=_positive(row.get("fillPrice")),
                        occurred_at=_time(row.get("ts")),
                    )
                )
        return tuple(fills)

    def position_quantity(self) -> Decimal:
        rows = _account_rows(self._client.request("GET", "/api/v1/account/positions", signed=True))
        total = Decimal("0")
        for row in rows:
            quantity = Decimal(str(row.get("positions")))
            if not quantity.is_finite():
                raise ValueError("Unreadable demo position.")
            if quantity == 0:
                continue
            inst = row.get("instId")
            if self._per_instrument and isinstance(inst, str) and inst != self._instrument:
                continue
            if inst != self._instrument or row.get("positionSide") != "net":
                raise ValueError("Unrelated demo account inventory.")
            total += quantity
        return total

    def mark_price(self) -> Decimal:
        rows = _account_rows(
            self._client.request(
                "GET", "/api/v1/market/tickers", params={"instId": self._instrument}
            )
        )
        if len(rows) != 1 or rows[0].get("instId", self._instrument) != self._instrument:
            raise ValueError("Ambiguous demo ticker.")
        return _positive(rows[0].get("last"))

    def closed_bars(self, *, evaluated_at: datetime, limit: int) -> tuple[DemoClosedBar, ...]:
        timeframe = Timeframe(self._plan.timeframe)
        interval = interval_timedelta(timeframe)
        data: Any = self._client.request(
            "GET",
            "/api/v1/market/candles",
            params={
                "instId": self._instrument,
                "bar": timeframe_to_bar(timeframe),
                "limit": str(min(max(limit, 1), 100)),
            },
        )
        if not isinstance(data, list):
            raise ValueError("Unexpected demo candles payload.")
        bars: list[DemoClosedBar] = []
        for row in data:
            start = _time(row[0])
            bar = DemoClosedBar(start, start + interval, _positive(row[2]), _positive(row[3]))
            if bar.interval_end <= evaluated_at:
                bars.append(bar)
        return tuple(sorted(bars, key=lambda bar: bar.interval_start))

    def place_reduce_only_market(self, *, client_order_id: str, side: str, size: Decimal) -> str:
        if not is_allowlisted_demo_host(self._client.base_url):
            raise ValueError("Partial-exit closes are allowed on the BloFin demo host only.")
        if side != self.close_side:
            raise ValueError("A partial-exit close must be on the closing side.")
        if not size.is_finite() or size <= 0 or not client_order_id.isalnum():
            raise ValueError("Invalid partial-exit close.")
        self._require_identity()
        body = {
            "instId": self._instrument,
            "marginMode": demo_order_margin_mode(
                self._plan.risk_and_exits.margin_assumption_id, str(self._plan.margin_mode.value)
            ),
            "positionSide": "net",
            "side": side,
            "orderType": "market",
            "size": str(size),
            "reduceOnly": "true",
            "clientOrderId": client_order_id,
        }
        rows = _rows(self._client.request("POST", "/api/v1/trade/order", body=body, signed=True))
        if len(rows) != 1 or str(rows[0].get("code", "0")) != "0":
            raise ValueError("Demo partial-exit close rejected.")
        order_id = str(rows[0].get("orderId") or "")
        if not order_id or rows[0].get("clientOrderId", client_order_id) != client_order_id:
            raise ValueError("Ambiguous demo partial-exit close identity.")
        return order_id
