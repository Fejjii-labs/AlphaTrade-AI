"""Depth-limited resting liquidity snapshots, never executed flow or book history."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from app.market_contracts.enums import FreshnessState, VenueId
from app.market_contracts.errors import MarketContractError, WrongSourceError
from app.market_contracts.freshness import FreshnessEvaluation, FreshnessPolicy, evaluate_freshness
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import EvidenceMarketIdentity, require_perpetual
from app.market_contracts.models import CanonicalDecimal, CanonicalModel, parse_canonical_decimal
from app.schemas.nested_continuation import EvidenceAvailability

BOOK_METHOD = "visible-resting-depth/base-and-unrounded-quote/v1"
BOOK_DEPTH: Literal[20] = 20


class BookLevel(CanonicalModel):
    price: CanonicalDecimal = Field(gt=0)
    base_quantity: CanonicalDecimal = Field(gt=0)


class OrderBookObservation(CanonicalModel):
    identity: EvidenceMarketIdentity
    event_time: AwareDatetime | None = None
    provider_generated_at: AwareDatetime | None = None
    observed_at: AwareDatetime
    collected_at: AwareDatetime
    availability: EvidenceAvailability
    freshness: FreshnessEvaluation | None = None
    freshness_policy_version: str = "resting-book/event-age-10s/no-future/v1"
    calculation_method: str = BOOK_METHOD
    coverage_kind: Literal["depth_limited_snapshot"] = "depth_limited_snapshot"
    historical_coverage: Literal[False] = False
    sequence_status: Literal["independent_snapshot", "resync_required"] = "independent_snapshot"
    requested_depth: Literal[20] = BOOK_DEPTH
    # Both exchanges exclude RPI liquidity from these public book endpoints.
    excluded_liquidity: Literal["RPI"] = "RPI"
    update_id: int | None = Field(default=None, ge=1)
    cross_sequence: int | None = Field(default=None, ge=1)
    base_units: str
    quote_units: str
    price_units: str
    bids: tuple[BookLevel, ...] = ()
    asks: tuple[BookLevel, ...] = ()
    spread: CanonicalDecimal | None = None
    bid_base_quantity: CanonicalDecimal | None = None
    ask_base_quantity: CanonicalDecimal | None = None
    bid_quote_notional: CanonicalDecimal | None = None
    ask_quote_notional: CanonicalDecimal | None = None
    # Ratio refers only to the returned levels; it is not taker buy/sell imbalance.
    resting_base_imbalance_ratio: CanonicalDecimal | None = None
    reason: str | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _valid(self) -> OrderBookObservation:
        require_perpetual(self.identity)
        if self.identity.timeframe is not None:
            raise ValueError("Resting snapshots have no candle timeframe.")
        if self.collected_at > self.observed_at:
            raise ValueError("Snapshot collection cannot occur after its observation.")
        if self.availability in {EvidenceAvailability.AVAILABLE, EvidenceAvailability.STALE}:
            if (
                self.event_time is None
                or self.provider_generated_at is None
                or self.freshness is None
                or self.update_id is None
                or self.sequence_status != "independent_snapshot"
            ):
                raise ValueError("Usable book snapshots require times, freshness and update ID.")
            if not self.event_time <= self.provider_generated_at <= self.collected_at:
                raise ValueError("Book event/generation times must be causal.")
            for levels, descending in ((self.bids, True), (self.asks, False)):
                prices = [level.price for level in levels]
                if not prices or len(prices) > BOOK_DEPTH or len(set(prices)) != len(prices):
                    raise ValueError("Book sides require bounded, unique price levels.")
                if prices != sorted(prices, reverse=descending):
                    raise ValueError("Book levels must follow provider price ordering.")
            if self.bids[0].price >= self.asks[0].price:
                raise ValueError("Crossed/locked snapshots cannot provide resting context.")
            if self.spread != self.asks[0].price - self.bids[0].price:
                raise ValueError("Spread must reconcile to the best prices.")
            bid, ask = (
                sum((x.base_quantity for x in levels), Decimal(0))
                for levels in (self.bids, self.asks)
            )
            if (self.bid_base_quantity, self.ask_base_quantity) != (bid, ask):
                raise ValueError("Resting base totals must reconcile to returned levels.")
            notionals = tuple(
                sum((x.price * x.base_quantity for x in levels), Decimal(0))
                for levels in (self.bids, self.asks)
            )
            if (self.bid_quote_notional, self.ask_quote_notional) != notionals:
                raise ValueError("Resting notionals must reconcile without rounding.")
            if self.resting_base_imbalance_ratio != (bid - ask) / (bid + ask):
                raise ValueError("Resting imbalance must reconcile to returned base quantities.")
        elif (
            self.bids
            or self.asks
            or any(
                x is not None
                for x in (
                    self.spread,
                    self.bid_base_quantity,
                    self.ask_base_quantity,
                    self.bid_quote_notional,
                    self.ask_quote_notional,
                    self.resting_base_imbalance_ratio,
                )
            )
        ):
            raise ValueError("Unavailable books cannot carry usable liquidity values.")
        return self


def book_identity(identity: EvidenceMarketIdentity) -> EvidenceMarketIdentity:
    return identity.model_copy(update={"timeframe": None})


def book_freshness_policy() -> FreshnessPolicy:
    return FreshnessPolicy(
        policy_version="resting-book/event-age-10s/no-future/v1",
        trade_max_age_seconds=10,
        aging_age_seconds=10,
        ohlcv_post_close_grace_seconds=0,
        max_clock_skew_seconds=0,
    )


def hash_order_book(item: OrderBookObservation) -> OrderBookObservation:
    return with_content_hash(item, extra_exclude=frozenset({"freshness", "collected_at"}))


def provider_integer(value: object) -> int:
    if isinstance(value, bool | float) or not str(value).isdigit() or int(str(value)) <= 0:
        raise ValueError("Expected a positive provider integer.")
    return int(str(value))


def provider_milliseconds(value: object) -> datetime:
    return datetime.fromtimestamp(provider_integer(value) / 1000, tz=UTC)


def book_levels(value: object) -> tuple[BookLevel, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= BOOK_DEPTH:
        raise ValueError("Malformed bounded order-book side.")
    result = []
    for row in value:
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError("Malformed price/quantity pair.")
        result.append(
            BookLevel(
                price=parse_canonical_decimal(row[0]), base_quantity=parse_canonical_decimal(row[1])
            )
        )
    return tuple(result)


def order_book_observation(
    *,
    identity: EvidenceMarketIdentity,
    observed_at: datetime,
    row: Mapping[str, Any] | None = None,
    availability: EvidenceAvailability = EvidenceAvailability.MISSING,
    reason: str | None = None,
    sequence_status: Literal["independent_snapshot", "resync_required"] = "independent_snapshot",
) -> OrderBookObservation:
    identity = book_identity(identity)
    values: dict[str, Any] = {}
    if row is not None:
        try:
            bybit = identity.venue is VenueId.BYBIT
            if bybit and row.get("s") != identity.instrument.provider_symbol:
                raise ValueError("Missing/wrong Bybit provider symbol.")
            if (
                row.get("s", identity.instrument.provider_symbol)
                != identity.instrument.provider_symbol
            ):
                raise ValueError("Wrong provider symbol.")
            event = provider_milliseconds(row["cts" if bybit else "T"])
            generated = provider_milliseconds(row["ts" if bybit else "E"])
            if not event <= generated <= observed_at:
                raise ValueError("Future or reversed provider time.")
            bids, asks = (
                book_levels(row["b" if bybit else "bids"]),
                book_levels(row["a" if bybit else "asks"]),
            )
            bid, ask = (
                sum((x.base_quantity for x in levels), Decimal(0)) for levels in (bids, asks)
            )
            freshness = evaluate_freshness(
                source_time=event,
                evaluated_at=observed_at,
                policy=book_freshness_policy(),
                require_fresh=False,
            )
            availability = (
                EvidenceAvailability.STALE
                if freshness.state is FreshnessState.STALE
                else EvidenceAvailability.AVAILABLE
            )
            values = {
                "event_time": event,
                "provider_generated_at": generated,
                "update_id": provider_integer(row["u" if bybit else "lastUpdateId"]),
                "cross_sequence": provider_integer(row["seq"]) if bybit else None,
                "freshness": freshness,
                "bids": bids,
                "asks": asks,
                "spread": asks[0].price - bids[0].price,
                "bid_base_quantity": bid,
                "ask_base_quantity": ask,
                "bid_quote_notional": sum((x.price * x.base_quantity for x in bids), Decimal(0)),
                "ask_quote_notional": sum((x.price * x.base_quantity for x in asks), Decimal(0)),
                "resting_base_imbalance_ratio": (bid - ask) / (bid + ask),
            }
            reason = (
                "book_event_exceeds_max_age" if availability is EvidenceAvailability.STALE else None
            )
            # Structural validation is also part of provider admission.
            OrderBookObservation(
                identity=identity,
                observed_at=observed_at,
                collected_at=observed_at,
                availability=availability,
                base_units=identity.instrument.base_quantity_unit,
                quote_units=identity.instrument.quote_asset,
                price_units=f"{identity.instrument.quote_asset}/{identity.instrument.base_quantity_unit}",
                content_hash="0" * 64,
                **values,
            )
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            values = {}
            availability, reason = EvidenceAvailability.INCOMPLETE, "malformed_or_future_book"
    return hash_order_book(
        OrderBookObservation(
            identity=identity,
            observed_at=observed_at,
            collected_at=observed_at,
            availability=availability,
            sequence_status=sequence_status,
            base_units=identity.instrument.base_quantity_unit,
            quote_units=identity.instrument.quote_asset,
            price_units=f"{identity.instrument.quote_asset}/{identity.instrument.base_quantity_unit}",
            reason=reason,
            content_hash="0" * 64,
            **values,
        )
    )


def refresh_order_book(item: OrderBookObservation, observed_at: datetime) -> OrderBookObservation:
    if item.event_time is None:
        return item
    freshness = evaluate_freshness(
        source_time=item.event_time,
        evaluated_at=observed_at,
        policy=book_freshness_policy(),
        require_fresh=False,
    )
    stale = freshness.state is FreshnessState.STALE
    return hash_order_book(
        item.model_copy(
            update={
                "observed_at": observed_at,
                "freshness": freshness,
                "availability": EvidenceAvailability.STALE
                if stale
                else EvidenceAvailability.AVAILABLE,
                "reason": "book_event_exceeds_max_age" if stale else None,
            }
        )
    )


def require_order_book(
    item: OrderBookObservation | None,
    *,
    identity: EvidenceMarketIdentity,
    evaluated_at: datetime,
    historical: bool = False,
) -> None:
    if item is None:
        raise MarketContractError("required_order_book:MISSING")
    item = OrderBookObservation.model_validate(item.model_dump())
    if (
        item.identity != book_identity(identity)
        or hash_order_book(item).content_hash != item.content_hash
        or item.calculation_method != BOOK_METHOD
        or item.freshness_policy_version != book_freshness_policy().policy_version
        or item.base_units != identity.instrument.base_quantity_unit
        or item.quote_units != identity.instrument.quote_asset
        or item.price_units
        != f"{identity.instrument.quote_asset}/{identity.instrument.base_quantity_unit}"
    ):
        raise WrongSourceError("required_order_book:wrong_source_hash_or_method")
    if historical:
        raise MarketContractError("required_order_book:historical_snapshot_coverage_unavailable")
    if item.availability is not EvidenceAvailability.AVAILABLE:
        raise MarketContractError(f"required_order_book:{item.availability.value}")
    if item.observed_at > evaluated_at:
        raise MarketContractError("required_order_book:future_observation")
    assert item.event_time is not None
    evaluate_freshness(
        source_time=item.event_time, evaluated_at=evaluated_at, policy=book_freshness_policy()
    )
