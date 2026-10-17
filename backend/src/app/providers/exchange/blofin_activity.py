"""Strict parser for the two official native futures history GET endpoints."""

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.core.blofin_readonly_access import BloFinReadOnlyClient
from app.providers.exchange.blofin_account import parse_account_permissions
from app.providers.exchange.errors import ExchangeRequestError
from app.schemas.blofin_activity import NativeActivityFact

HISTORY_PATHS = {
    "order": "/api/v1/trade/orders-history",
    "fill": "/api/v1/trade/fills-history",
}


class ActivityIdentityError(ExchangeRequestError):
    """No verified UID binding; never fall back to credential identity."""


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ExchangeRequestError("Invalid native activity identifier.")
    if not value.isascii() or any(not (c.isalnum() or c in "-_.") for c in value):
        raise ExchangeRequestError("Invalid native activity identifier.")
    return value


def timestamp(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdecimal()
        or len(value) > 15
    ):
        raise ExchangeRequestError("Invalid native millisecond timestamp.")
    if not 0 <= int(value) <= 253402300799999:
        raise ExchangeRequestError("Native timestamp outside supported range.")
    return value


def decimal_string(value: Any, *, optional: bool = False, positive: bool = False) -> str | None:
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or not value or len(value) > 128 or value != value.strip():
        raise ExchangeRequestError("Native decimal must be an exact string.")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ExchangeRequestError("Invalid native decimal.") from exc
    if not number.is_finite() or (positive and number <= 0):
        raise ExchangeRequestError("Invalid native decimal.")
    return value


def parse_fact(kind: Literal["order", "fill"], row: dict[str, Any]) -> NativeActivityFact:
    order_id = identifier(row.get("orderId"))
    side, position_side = row.get("side"), row.get("positionSide")
    if side not in ("buy", "sell") or position_side not in ("net", "long", "short"):
        raise ExchangeRequestError("Invalid native side.")
    common: dict[str, Any] = {
        "kind": kind,
        "order_id": order_id,
        "instrument": identifier(row.get("instId")),
        "side": side,
        "position_side": position_side,
        "fee": decimal_string(row.get("fee"), optional=True),
        # Currency is absent from the documented history shape. Never assume USDT.
        "fee_currency": identifier(row["feeCurrency"]) if row.get("feeCurrency") else None,
    }
    if kind == "fill":
        trade_id = identifier(row.get("tradeId"))
        return NativeActivityFact(
            **common,
            native_id=trade_id,
            trade_id=trade_id,
            occurred_at_ms=timestamp(row.get("ts")),
            quantity=decimal_string(row.get("fillSize"), positive=True),
            price=decimal_string(row.get("fillPrice"), positive=True),
            realized_pnl=decimal_string(row.get("fillPnl"), optional=True),
        )
    created, updated = timestamp(row.get("createTime")), timestamp(row.get("updateTime"))
    if int(updated) < int(created) or row.get("state") not in (
        "filled",
        "canceled",
        "partially_canceled",
    ):
        raise ExchangeRequestError("Invalid completed native order.")
    quantity = decimal_string(row.get("size"), positive=True)
    filled = decimal_string(row.get("filledSize"))
    if quantity is None or filled is None or not 0 <= Decimal(filled) <= Decimal(quantity):
        raise ExchangeRequestError("Invalid accumulated fill quantity.")
    reduce_only = row.get("reduceOnly")
    if reduce_only not in (None, "true", "false"):
        raise ExchangeRequestError("Invalid native reduceOnly flag.")
    return NativeActivityFact(
        **common,
        native_id=order_id,
        client_order_id=identifier(row["clientOrderId"]) if row.get("clientOrderId") else None,
        occurred_at_ms=updated,
        created_at_ms=created,
        updated_at_ms=updated,
        quantity=quantity,
        filled_quantity=filled,
        price=decimal_string(row.get("price"), optional=True),
        average_price=decimal_string(row.get("averagePrice"), optional=True),
        state=row["state"],
        order_type=identifier(row.get("orderType")),
        reduce_only=reduce_only,
        realized_pnl=decimal_string(row.get("pnl"), optional=True),
    )


class BloFinActivityProvider:
    def __init__(self, client: BloFinReadOnlyClient) -> None:
        if not isinstance(client, BloFinReadOnlyClient):
            raise TypeError("Native activity requires the restricted read-only transport.")
        self.client = client

    def verify_identity(self, expected_uid: str, before_send: Callable[[], None]) -> str:
        data = self.client.request(
            "GET", "/api/v1/user/query-apikey", signed=True, before_send=before_send
        )
        if not isinstance(data, dict):
            raise ActivityIdentityError("Native account identity unavailable.")
        permissions = parse_account_permissions(data)
        if (
            data.get("uid") != expected_uid
            or not permissions.can_read
            or permissions.can_trade
            or permissions.can_withdraw
            or permissions.can_transfer
        ):
            raise ActivityIdentityError("Native account identity or read permissions mismatch.")
        return identifier(data["uid"])

    def metadata(self, before_send: Callable[[], None]) -> dict[str, dict[str, str | None]]:
        data = self.client.request("GET", "/api/v1/market/instruments", before_send=before_send)
        if not isinstance(data, list) or len(data) > 5000:
            raise ExchangeRequestError("Invalid instrument metadata.")
        result: dict[str, dict[str, str | None]] = {}
        for row in data:
            if not isinstance(row, dict):
                raise ExchangeRequestError("Invalid instrument metadata.")
            inst = identifier(row.get("instId"))
            if inst in result:
                raise ExchangeRequestError("Conflicting instrument metadata identities.")
            result[inst] = {
                "contract_multiplier": decimal_string(row.get("contractValue"), optional=True),
                "contract_type": identifier(row["contractType"])
                if row.get("contractType")
                else None,
                "base_currency": identifier(row["baseCurrency"])
                if row.get("baseCurrency")
                else None,
                "settlement_currency": (
                    identifier(row["settleCurrency"]) if row.get("settleCurrency") else None
                ),
            }
        return result

    def page(
        self,
        kind: Literal["order", "fill"],
        *,
        begin_ms: int,
        end_ms: int,
        after: str | None,
        limit: int,
        expected_uid: str,
        before_send: Callable[[], None],
    ) -> tuple[NativeActivityFact, ...]:
        self.verify_identity(expected_uid, before_send)
        # Completed orders may have been created years before completion. A recurring
        # cursor sweep avoids assuming what the optional begin/end timestamps filter.
        params = {"limit": str(limit)}
        if kind == "fill":
            params.update(begin=str(begin_ms), end=str(end_ms))
        if after:
            params["after"] = identifier(after)
        data = self.client.request(
            "GET", HISTORY_PATHS[kind], params=params, signed=True, before_send=before_send
        )
        if not isinstance(data, list) or len(data) > limit:
            raise ExchangeRequestError("Invalid native history page.")
        facts = []
        for row in data:
            if not isinstance(row, dict):
                raise ExchangeRequestError("Invalid native history row.")
            fact = parse_fact(kind, row)
            if kind == "fill" and not begin_ms <= int(fact.occurred_at_ms) <= end_ms:
                raise ExchangeRequestError("Native history outside requested window.")
            facts.append(fact)
        return tuple(facts)
