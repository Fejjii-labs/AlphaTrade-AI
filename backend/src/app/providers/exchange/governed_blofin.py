"""Strict BloFin demo adapter for the canonical durable execution protocol.

All prices and fills come from venue responses. Submission only acknowledges an
order: reconciliation reads fills and attached protection separately. No retry,
account-setting mutation, internal fill fallback, or real venue is supported.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from typing import TYPE_CHECKING, Any

from app.providers.exchange.blofin_account import BloFinAccountProvider
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.demo_order_book import DemoOrderBook, parse_demo_book, venue_decimal
from app.providers.exchange.demo_preflight import preflight_stage
from app.providers.exchange.errors import ExchangeRequestError
from app.providers.exchange.mapping import to_blofin_inst_id
from app.schemas.manual_demo import DemoReconciliationDiagnostic
from app.schemas.trade_plan import EntrySide, TradePlanRevision

if TYPE_CHECKING:
    from app.providers.exchange.governed_blofin_exit import VerifiedDemoExit


@dataclass(frozen=True)
class DemoVenueSnapshot:
    instrument: str
    price: Decimal
    observed_at: datetime
    tick: Decimal
    lot: Decimal
    minimum: Decimal
    multiplier: Decimal
    equity: Decimal
    available: Decimal
    maximum: Decimal
    book: DemoOrderBook
    account_observed_at: datetime
    native_account_uid: str | None = None


@dataclass(frozen=True)
class DemoFill:
    identity: str
    quantity: Decimal
    price: Decimal
    occurred_at: datetime
    fee: Decimal
    # Preserve the provider's explicit currency. Instrument settlement does not
    # prove the currency of an omitted historical fee.
    fee_currency: str | None = None


@dataclass(frozen=True)
class DemoOrderEvidence:
    order_id: str
    client_order_id: str
    status: str
    fills: tuple[DemoFill, ...]
    protected: bool
    protection_status: str
    protection_order_ids: tuple[str, ...] = ()
    diagnostics: tuple[DemoReconciliationDiagnostic, ...] = ()
    native_tpsl_id: str | None = None
    protection_configured: bool = False
    native_account_uid: str | None = None


def _rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        return [data]
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _account_rows(data: Any) -> list[dict[str, Any]]:
    """A malformed or possibly truncated account read cannot prove absence."""
    if (
        not isinstance(data, list)
        or len(data) >= 100
        or any(not isinstance(row, dict) for row in data)
    ):
        raise ValueError("Demo account state incomplete or unreadable.")
    return data


def _positive(value: Any) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError("Positive finite venue value required.")
    return parsed


def _time(value: Any) -> datetime:
    # Venue occurrence timestamps are mandatory. Observation time cannot replace them.
    return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC)


class GovernedBloFinDemoProvider:
    """Uses an allowlisted demo client and verifies current permissions before send."""

    def __init__(
        self, client: BloFinClient, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._client = client
        self._account = BloFinAccountProvider(client)
        self._clock = clock

    def snapshot(
        self, *, symbol: str, now: datetime, side: EntrySide, quantity: Decimal | None = None
    ) -> DemoVenueSnapshot:
        self.verify_permissions()
        with preflight_stage("position_mode", "GET /api/v1/account/position-mode"):
            if self._account.get_position_mode().position_mode != "net_mode":
                raise ValueError("Governed demo requires the existing NET account mode.")
        with preflight_stage("instrument", "GET /api/v1/market/instruments"):
            instrument = to_blofin_inst_id(symbol)
            rows = _rows(
                self._client.request(
                    "GET", "/api/v1/market/instruments", params={"instId": instrument}
                )
            )
            matches = [r for r in rows if r.get("instId") == instrument]
            if len(matches) != 1 or matches[0].get("state") != "live":
                raise ValueError("Demo instrument unavailable.")
            row = matches[0]
            if (
                row.get("quoteCurrency") != "USDT"
                or row.get("baseCurrency") != symbol.removesuffix("USDT")
                or row.get("contractType") != "linear"
            ):
                raise ValueError("Only base-valued linear USDT contracts are supported.")
            if row.get("instType") not in {"SWAP", "PERPETUAL"}:
                raise ValueError("Demo perpetual instrument required.")
            tick = venue_decimal(row.get("tickSize"))
            lot = venue_decimal(row.get("lotSize"))
            minimum = venue_decimal(row.get("minSize"))
            multiplier = venue_decimal(row.get("contractValue"))
            maximum = venue_decimal(row.get("maxMarketSize"))
        with preflight_stage("leverage", "GET /api/v1/account/leverage-info"):
            leverage = self._account.get_leverage_info(inst_id=instrument, margin_mode="cross")
            if leverage.leverage != Decimal("1"):
                raise ValueError("Demo account must already use leverage 1; no leverage mutation.")
        account_observed_at = self._clock()
        self.verify_flat_account()
        with preflight_stage("balance", "GET /api/v1/account/balance"):
            balances = self._account.get_balances()
            balance = next((b for b in balances if b.asset == "USDT"), None)
            if (
                balance is None
                or len([b for b in balances if b.asset == "USDT"]) != 1
                or not balance.available.is_finite()
                or not balance.total.is_finite()
                or balance.available <= 0
                or balance.total <= 0
            ):
                raise ValueError("Demo USDT equity unavailable.")
        with preflight_stage("quote", "GET /api/v1/market/books"):
            started = monotonic()
            data = self._client.request(
                "GET", "/api/v1/market/books", params={"instId": instrument, "size": "100"}
            )
            # Preflight IO can outlast the caller's timestamp. Check freshness at receipt.
            now = self._clock()
            book = parse_demo_book(
                data,
                instrument=instrument,
                side=side,
                tick=tick,
                lot=lot,
                received_at=now,
                request_duration_ms=round((monotonic() - started) * 1000, 3),
            )
            book.worst_price(
                minimum if quantity is None else quantity, lot=lot, minimum=minimum, maximum=maximum
            )
        return DemoVenueSnapshot(
            instrument=instrument,
            price=book.price,
            observed_at=book.observed_at,
            tick=tick,
            lot=lot,
            minimum=minimum,
            multiplier=multiplier,
            equity=balance.total,
            available=balance.available,
            maximum=maximum,
            book=book,
            account_observed_at=account_observed_at,
            native_account_uid=self._account.native_account_uid,
        )

    def verify_flat_account(self) -> None:
        """Account-wide proof, including orders on other watchlist markets."""
        with preflight_stage("positions", "GET /api/v1/account/positions"):
            positions = _account_rows(
                self._client.request("GET", "/api/v1/account/positions", signed=True)
            )
            for position in positions:
                quantity = Decimal(str(position.get("positions")))
                if not quantity.is_finite() or quantity != 0:
                    raise ValueError("Demo position already open or unreadable; new entry refused.")
        for stage, endpoint in (
            ("pending_orders", "orders-pending"),
            ("pending_protection", "orders-tpsl-pending"),
        ):
            with preflight_stage(stage, f"GET /api/v1/trade/{endpoint}"):
                pending = _account_rows(
                    self._client.request(
                        "GET", f"/api/v1/trade/{endpoint}", params={"limit": "100"}, signed=True
                    )
                )
                if pending:
                    raise ValueError("Demo pending orders or protection; new entry refused.")

    def verify_permissions(self) -> None:
        with preflight_stage("permissions", "GET /api/v1/user/query-apikey"):
            permissions = self._account.get_account_permissions()
            if (
                not permissions.can_read
                or not permissions.can_trade
                or permissions.can_withdraw
                or permissions.can_transfer
            ):
                raise ValueError("Verified read/trade-only demo permissions required.")

    def reconcile_exit(
        self, *, plan: TradePlanRevision, entry: DemoOrderEvidence
    ) -> VerifiedDemoExit | None:
        from app.providers.exchange.governed_blofin_exit import reconcile_demo_exit

        return reconcile_demo_exit(self._client, plan=plan, entry=entry, clock=self._clock)

    def submit(
        self, *, plan: TradePlanRevision, client_order_id: str, before_post: Callable[[], None]
    ) -> str:
        """One POST, with stop and target attached to the entry order."""
        snapshot = self.snapshot(
            symbol=plan.execution_instrument.replace("-", ""),
            now=self._clock(),
            side=plan.side,
            quantity=plan.quantity.value,
        )
        rules = plan.instrument_rules
        if (snapshot.tick, snapshot.lot, snapshot.minimum, snapshot.multiplier) != (
            rules.tick_size,
            rules.lot_size,
            rules.minimum_quantity,
            rules.contract_multiplier,
        ) or plan.quantity.value > snapshot.maximum:
            raise ValueError("Demo instrument constraints changed before dispatch.")
        worst_price = snapshot.book.worst_price(
            plan.quantity.value,
            lot=snapshot.lot,
            minimum=snapshot.minimum,
            maximum=snapshot.maximum,
        )
        if any(
            not plan.entry_zone.lower <= price <= plan.entry_zone.upper
            for price in (snapshot.price, worst_price)
        ):
            raise ValueError("Executable demo depth is outside the authorized entry range.")
        if (
            max(
                abs(price - plan.basis_policy.execution_price.value)
                for price in (snapshot.price, worst_price)
            )
            / plan.basis_policy.execution_price.value
            * Decimal("10000")
            > plan.slippage_policy.maximum_bps
        ):
            raise ValueError("Demo execution quote moved beyond the authorized slippage bound.")
        if (
            abs(snapshot.price - plan.basis_policy.evidence_price.value)
            / plan.basis_policy.evidence_price.value
            * Decimal("10000")
            > plan.basis_policy.tolerance_bps
        ):
            raise ValueError("Demo cross-venue basis moved beyond the authorized bound.")
        if plan.schema_version == "ManualDemoTradePlanV1":
            from app.services.manual_demo_policy import validate_manual_demo

            validate_manual_demo(plan, snapshot, now=self._clock())
        body = {
            "instId": plan.execution_instrument,
            "marginMode": "cross",
            "positionSide": "net",
            "side": "buy" if plan.side is EntrySide.BUY else "sell",
            "orderType": "market",
            "size": str(plan.quantity.value),
            "clientOrderId": client_order_id,
            "slTriggerPrice": str(plan.risk_and_exits.stop.value),
            "slOrderPrice": "-1",
            "tpTriggerPrice": str(plan.risk_and_exits.targets[0].price.value),
            "tpOrderPrice": "-1",
        }

        def verify_deadline() -> None:
            now = self._clock()
            if now >= plan.valid_until:
                raise ValueError("Demo plan expired during dispatch preflight.")
            if not 0 <= (now - snapshot.observed_at).total_seconds() < 10:
                raise ValueError("Demo quote stale or future dated.")
            if plan.schema_version == "ManualDemoTradePlanV1":
                validate_manual_demo(plan, snapshot, now=now)

        verify_deadline()
        before_post()
        # Check after the safety callback and transport throttle, immediately before native send.
        rows = _rows(
            self._client.request(
                "POST", "/api/v1/trade/order", body=body, signed=True, before_send=verify_deadline
            )
        )
        if len(rows) != 1:
            raise ValueError("Ambiguous demo submit response.")
        row = rows[0]
        if str(row.get("code", "0")) != "0":
            raise ExchangeRequestError("Demo entry/protection rejected.")
        order_id = str(row.get("orderId", ""))
        if not order_id or row.get("clientOrderId", client_order_id) != client_order_id:
            raise ValueError("Ambiguous demo submit identity.")
        return order_id

    def cancel_entry(self, *, plan: TradePlanRevision, evidence: DemoOrderEvidence) -> None:
        """Cancel only an identity-verified live entry, never its protective TPSL."""
        self.verify_permissions()
        if (
            plan.schema_version != "ManualDemoTradePlanV1"
            or evidence.status not in {"live", "partially_filled"}
            or not evidence.order_id
            or not evidence.client_order_id
        ):
            raise ValueError("Verified pending manual demo entry required for cancellation.")
        self._client.request(
            "POST",
            "/api/v1/trade/cancel-order",
            signed=True,
            body={"instId": plan.execution_instrument, "orderId": evidence.order_id},
        )
        # An acknowledgment is not proof of cancellation. Caller must read again.

    def reconcile(
        self, *, plan: TradePlanRevision, client_order_id: str
    ) -> DemoOrderEvidence | None:
        """Read by durable client id. Absence never authorizes another POST."""
        from app.providers.exchange.demo_reconciliation import reconcile_native

        # Read UID using this execution connection, never the activity key or an owner pin.
        self.verify_permissions()
        evidence = reconcile_native(
            self._client, plan=plan, client_order_id=client_order_id, clock=self._clock
        )
        return (
            replace(evidence, native_account_uid=self._account.native_account_uid)
            if evidence is not None
            else None
        )
