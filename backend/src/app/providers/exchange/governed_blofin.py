"""Strict BloFin demo adapter for the canonical durable execution protocol.

All prices and fills come from venue responses. Submission only acknowledges an
order: reconciliation reads fills and attached protection separately. No retry,
internal fill fallback, or real venue is supported. The only account-setting
mutation is ``apply_configured_leverage``: the owner-configured leverage and
margin mode, on the allowlisted demo host, on a flat account, read back exactly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from time import monotonic
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from app.core.exchange_safety import BLOFIN_PRODUCTION_HOSTS, is_allowlisted_demo_host
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


# Autonomous demo plans sized without a risk profile, at the owner-configured
# leverage and margin mode that the app set and read back before planning.
CONFIGURED_DEMO_MARGIN_ASSUMPTION = "configured-demo-leverage"
_DEMO_LEVERAGE_CEILING = Decimal("20")


def demo_order_margin_mode(margin_assumption_id: str, plan_margin_mode: str | None = None) -> str:
    """Profile plans send the isolated mode that was read. Configured plans send
    their stored mode. Every other plan (manual demo) stays cross.

    This is the existing order field. It is not a leverage or margin-mode setting call.
    """

    if margin_assumption_id == "verified-demo-read-leverage":
        return "isolated"
    if margin_assumption_id == CONFIGURED_DEMO_MARGIN_ASSUMPTION:
        mode = (plan_margin_mode or "").lower()
        if mode not in {"isolated", "cross"}:
            raise ValueError("Configured demo plan margin mode missing.")
        return mode
    return "cross"


def demo_plan_leverage(plan: TradePlanRevision) -> tuple[Decimal, str]:
    """Leverage and margin mode the venue must already show before this plan's POST."""

    assumption = plan.risk_and_exits.margin_assumption_id
    if assumption == "verified-demo-read-leverage":
        return plan.risk_and_exits.leverage, "isolated"
    if assumption == CONFIGURED_DEMO_MARGIN_ASSUMPTION:
        return plan.risk_and_exits.leverage, demo_order_margin_mode(
            assumption, str(plan.margin_mode.value)
        )
    return Decimal("1"), "cross"


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
    # Read from the margin-mode endpoint and leverage-info. Written only by
    # apply_configured_leverage, before this read.
    account_leverage: Decimal = Decimal("1")
    margin_mode: str = "cross"
    # (notional floor, maintenance rate) from public position tiers. Empty refuses a profile plan.
    maintenance_tiers: tuple[tuple[Decimal, Decimal], ...] = ()


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


def tier_notional_floor(min_size: Decimal) -> Decimal:
    """Use a position-tier ``minSize`` as quote notional. Do not scale it.

    https://docs.blofin.com/index.html (GET /api/v1/market/position-tiers)
    calls ``minSize`` "Minimum position size" and does not say contracts.
    GET Instruments on that page does say order ``minSize`` is in contracts.
    The public BTC-USDT isolated ladder is 0, 2500, 5000, … USDT while
    ``contractValue`` is 0.001. Scaling by contract value and price would
    inflate those brackets and leave the lowest maintenance rate in force.
    The conservative reading uses the published number unchanged.
    """

    return min_size


def _tier_sizes(data: Any) -> tuple[tuple[Decimal, Decimal], ...]:
    """Public position tiers as (quote-notional floor, maintenance rate). No write."""

    message = "Demo maintenance tiers unavailable; no leverage mutation."
    if not isinstance(data, list) or not data:
        raise ValueError(message)
    tiers: list[tuple[Decimal, Decimal]] = []
    for row in data:
        if not isinstance(row, dict):
            raise ValueError(message)
        try:
            rate = Decimal(str(row.get("maintenanceMarginRate")))
            min_size = Decimal(str(row.get("minSize") or "0"))
        except Exception as exc:
            raise ValueError(message) from exc
        if (
            not rate.is_finite()
            or rate < 0
            or rate >= 1
            or not min_size.is_finite()
            or min_size < 0
        ):
            raise ValueError(message)
        tiers.append((min_size, rate))
    return tuple(tiers)


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
        self._expected_native_uid: str | None = None

    def require_account_identity(self, native_uid: str) -> None:
        """Pin explicit server experiment ownership before any execution IO."""
        if not native_uid or self._expected_native_uid not in {None, native_uid}:
            raise ValueError("Demo provider cannot change its expected native account.")
        self._expected_native_uid = native_uid

    def snapshot(
        self,
        *,
        symbol: str,
        now: datetime,
        side: EntrySide,
        quantity: Decimal | None = None,
        required_leverage: Decimal | None = Decimal("1"),
        margin_mode: str = "cross",
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
        with preflight_stage("margin_mode", "GET /api/v1/account/margin-mode"):
            # Read only. The only write is apply_configured_leverage, called earlier.
            account_mode = self._account.get_account_margin_mode()
            if not account_mode.supplied or account_mode.margin_mode not in {"cross", "isolated"}:
                raise ValueError("Demo margin-mode omitted; no margin-mode mutation.")
            observed_mode = account_mode.margin_mode
            if margin_mode == "isolated" and observed_mode != "isolated":
                raise ValueError(
                    "Demo account margin mode is not isolated; no margin-mode mutation."
                )
            if margin_mode == "cross" and observed_mode != "cross":
                raise ValueError("Demo account margin mode is not cross; no margin-mode mutation.")
        with preflight_stage("leverage", "GET /api/v1/account/batch-leverage-info"):
            # Ask with the mode the account endpoint reported. The payload must
            # repeat that mode. A missing field is not replaced by the request.
            leverage = self._account.get_leverage_info(
                inst_id=instrument, margin_mode=observed_mode
            )
            if not leverage.margin_mode_supplied:
                raise ValueError("Demo leverage-info omitted margin mode; no margin-mode mutation.")
            if leverage.margin_mode != observed_mode:
                raise ValueError(
                    "Demo leverage-info margin mode does not match the account; "
                    "no margin-mode mutation."
                )
            if required_leverage is not None and leverage.leverage != required_leverage:
                if required_leverage == Decimal("1"):
                    raise ValueError(
                        "Demo account must already use leverage 1; no leverage mutation."
                    )
                raise ValueError(
                    "Demo account leverage does not match required leverage; no leverage mutation."
                )
        with preflight_stage("position_tiers", "GET /api/v1/market/position-tiers"):
            tier_rows = self._client.request(
                "GET",
                "/api/v1/market/position-tiers",
                params={"instId": instrument, "marginMode": observed_mode},
            )
            tier_sizes = _tier_sizes(tier_rows)
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
        maintenance_tiers = tuple(
            (tier_notional_floor(min_size), rate) for min_size, rate in tier_sizes
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
            account_leverage=leverage.leverage,
            margin_mode=observed_mode,
            maintenance_tiers=maintenance_tiers,
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

    def apply_configured_leverage(
        self, *, symbol: str, leverage: Decimal, margin_mode: str
    ) -> dict[str, str]:
        """Set the owner-configured margin mode and leverage, then read both back.

        The only leverage or margin-mode write in this adapter. It refuses a
        client that is not on the allowlisted BloFin demo host, refuses a
        non-flat account (a margin-mode change cannot apply to an open
        position), sends no retry, and raises unless the venue reads back the
        exact requested values. Every refusal is a ValueError with fixed copy.
        Returns the previous and new values for the audit journal.

        BloFin docs:
        - set-margin-mode: https://docs.blofin.com/index.html#set-margin-mode
        - set-leverage: https://docs.blofin.com/index.html#set-leverage
        - batch-leverage-info (list; exactly one row for the instId):
          https://docs.blofin.com/index.html#get-multiple-leverage
          (replaces the deprecated single endpoint,
          https://docs.blofin.com/index.html#get-leverage-deprecated)
        - margin-mode: https://docs.blofin.com/index.html#get-margin-mode
        """

        with preflight_stage("leverage_write_host", "POST /api/v1/account/set-leverage"):
            base_url = getattr(self._client, "base_url", None)
            if (
                not isinstance(base_url, str)
                or not is_allowlisted_demo_host(base_url)
                or (urlsplit(base_url).hostname or "").lower() in BLOFIN_PRODUCTION_HOSTS
            ):
                raise ValueError("Demo leverage write refused: client is not the BloFin demo host.")
        with preflight_stage("leverage_request", "POST /api/v1/account/set-leverage"):
            if (
                margin_mode not in {"isolated", "cross"}
                or not isinstance(leverage, Decimal)
                or not leverage.is_finite()
                or leverage != leverage.to_integral_value()
                or not Decimal("1") <= leverage <= _DEMO_LEVERAGE_CEILING
            ):
                raise ValueError("Demo leverage request invalid; no leverage mutation.")
        self.verify_permissions()
        with preflight_stage("position_mode", "GET /api/v1/account/position-mode"):
            if self._account.get_position_mode().position_mode != "net_mode":
                raise ValueError("Governed demo requires the existing NET account mode.")
        self.verify_flat_account()
        instrument = to_blofin_inst_id(symbol)
        with preflight_stage("margin_mode", "GET /api/v1/account/margin-mode"):
            current = self._account.get_account_margin_mode()
            if not current.supplied:
                raise ValueError("Demo margin-mode omitted; no margin-mode mutation.")
        try:
            previous_leverage = str(
                self._account.get_leverage_info(
                    inst_id=instrument, margin_mode=current.margin_mode
                ).leverage
            )
        except Exception:
            previous_leverage = "unknown"
        if current.margin_mode != margin_mode:
            with preflight_stage("margin_mode_write", "POST /api/v1/account/set-margin-mode"):
                try:
                    data = self._client.request(
                        "POST",
                        "/api/v1/account/set-margin-mode",
                        body={"marginMode": margin_mode},
                        signed=True,
                    )
                except Exception as exc:
                    raise ValueError("Demo set-margin-mode failed.") from exc
                rows = _rows(data)
                if len(rows) != 1 or str(rows[0].get("marginMode", "")).lower() != margin_mode:
                    raise ValueError("Demo set-margin-mode was not confirmed.")
        with preflight_stage("leverage_write", "POST /api/v1/account/set-leverage"):
            try:
                data = self._client.request(
                    "POST",
                    "/api/v1/account/set-leverage",
                    body={
                        "instId": instrument,
                        "leverage": format(leverage.to_integral_value(), "f"),
                        "marginMode": margin_mode,
                        "positionSide": "net",
                    },
                    signed=True,
                )
            except Exception as exc:
                raise ValueError("Demo set-leverage failed.") from exc
            rows = _rows(data)
            try:
                confirmed = len(rows) == 1 and (
                    rows[0].get("instId") == instrument
                    and str(rows[0].get("marginMode", "")).lower() == margin_mode
                    and Decimal(str(rows[0].get("leverage"))) == leverage
                )
            except Exception:
                confirmed = False
            if not confirmed:
                raise ValueError("Demo set-leverage was not confirmed.")
        with preflight_stage("leverage_readback", "GET /api/v1/account/batch-leverage-info"):
            account_mode = self._account.get_account_margin_mode()
            if not account_mode.supplied or account_mode.margin_mode != margin_mode:
                raise ValueError("Demo margin mode read-back does not match the configured mode.")
            info = self._account.get_leverage_info(inst_id=instrument, margin_mode=margin_mode)
            if not info.margin_mode_supplied or info.margin_mode != margin_mode:
                raise ValueError("Demo margin mode read-back does not match the configured mode.")
            if info.leverage != leverage:
                raise ValueError("Demo leverage read-back does not match the configured leverage.")
        return {
            "inst_id": instrument,
            "previous_margin_mode": current.margin_mode,
            "previous_leverage": previous_leverage,
            "margin_mode": margin_mode,
            "leverage": format(leverage.to_integral_value(), "f"),
            "margin_mode_changed": str(current.margin_mode != margin_mode).lower(),
        }

    def flatten_position(self, *, symbol: str, margin_mode: str) -> str:
        """Close the whole NET position for one instrument on the BloFin DEMO host.

        Used only by the post-fill liquidation hold. Same demo-host guard as the
        leverage write; one POST, never retried; a venue error raises.
        Positions are read first: already flat for this instrument returns
        ``noop_already_flat`` with no POST (idempotent across a crash between a
        close and its commit). An unreadable positions read still sends the
        close, which is harmless on a flat account.
        Docs: https://docs.blofin.com/index.html#close-positions
        """

        with preflight_stage("flatten_host", "POST /api/v1/trade/close-position"):
            base_url = getattr(self._client, "base_url", None)
            if (
                not isinstance(base_url, str)
                or not is_allowlisted_demo_host(base_url)
                or (urlsplit(base_url).hostname or "").lower() in BLOFIN_PRODUCTION_HOSTS
            ):
                raise ValueError("Demo flatten refused: client is not the BloFin demo host.")
            if margin_mode not in {"isolated", "cross"}:
                raise ValueError("Demo flatten margin mode invalid.")
        instrument = to_blofin_inst_id(symbol)
        try:
            open_here = any(p.inst_id == instrument for p in self._account.get_positions())
        except Exception:
            open_here = True
        if not open_here:
            return "noop_already_flat"
        with preflight_stage("flatten", "POST /api/v1/trade/close-position"):
            self._client.request(
                "POST",
                "/api/v1/trade/close-position",
                body={
                    "instId": instrument,
                    "marginMode": margin_mode,
                    "positionSide": "net",
                },
                signed=True,
            )
        return "flatten_sent"

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
            if (
                self._expected_native_uid is not None
                and self._account.native_account_uid != self._expected_native_uid
            ):
                raise ValueError("Demo execution connection differs from the owned native account.")

    def read_funding_rate(self, *, symbol: str) -> Decimal | None:
        """Finite public funding print, or None when the GET fails.

        A failure is not replaced with the mock 0.0001 fallback. Callers use
        the documented floor instead. This does not place an order.
        """

        try:
            data = self._client.request(
                "GET",
                "/api/v1/market/funding-rate",
                params={"instId": to_blofin_inst_id(symbol)},
            )
        except Exception:
            return None
        row = data[0] if isinstance(data, list) and data else data
        if not isinstance(row, dict):
            return None
        raw = row.get("fundingRate")
        if raw is None or raw == "":
            return None
        try:
            rate = Decimal(str(raw))
        except Exception:
            return None
        if not rate.is_finite():
            return None
        return rate

    def read_open_liquidation(self, *, inst_id: str) -> Decimal | None:
        """Liquidation price of one open position. None when that market is flat.

        A missing price on an open position is not treated as safe. Callers
        distinguish None (flat) from a read that raises.
        """

        for position in self._account.get_positions():
            if position.inst_id == inst_id:
                if position.liquidation_price is None or position.liquidation_price <= 0:
                    raise ValueError("Demo liquidation price unavailable.")
                return position.liquidation_price
        return None

    def reconcile_exit(
        self, *, plan: TradePlanRevision, entry: DemoOrderEvidence
    ) -> VerifiedDemoExit | None:
        from app.providers.exchange.governed_blofin_exit import reconcile_demo_exit

        self.verify_permissions()
        uid = self._account.native_account_uid
        if uid != entry.native_account_uid:
            raise ValueError("Demo account identity changed before exit reconciliation.")
        result = reconcile_demo_exit(self._client, plan=plan, entry=entry, clock=self._clock)
        self.verify_permissions()
        if self._account.native_account_uid != uid:
            raise ValueError("Demo account identity changed during exit reconciliation.")
        return result

    def submit(
        self, *, plan: TradePlanRevision, client_order_id: str, before_post: Callable[[], None]
    ) -> str:
        """One POST, with stop and target attached to the entry order."""
        # Read only: the configured leverage was set before planning. Any drift refuses.
        required_leverage, required_mode = demo_plan_leverage(plan)
        snapshot = self.snapshot(
            symbol=plan.execution_instrument.replace("-", ""),
            now=self._clock(),
            side=plan.side,
            quantity=plan.quantity.value,
            required_leverage=required_leverage,
            margin_mode=required_mode,
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
            "marginMode": demo_order_margin_mode(
                plan.risk_and_exits.margin_assumption_id, str(plan.margin_mode.value)
            ),
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
