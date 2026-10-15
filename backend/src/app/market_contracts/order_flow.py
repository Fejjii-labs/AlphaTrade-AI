"""Five-minute trade-print evidence over a venue-bound ten-minute observation window.

CVD is an internal signed base-volume sum, with zero at each declared window
start. It is not an absolute provider CVD. No candle volume is accepted here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal, TypedDict
from uuid import UUID, uuid5

from pydantic import AwareDatetime, Field, model_validator

from app.market_contracts.coverage import require_complete_window_coverage
from app.market_contracts.cursor import TradeStreamSnapshot
from app.market_contracts.cvd import require_cvd_stream_proof
from app.market_contracts.enums import DataCompleteness, FreshnessState
from app.market_contracts.errors import MarketContractError, StaleEvidenceError, WrongSourceError
from app.market_contracts.freshness import FreshnessEvaluation, FreshnessPolicy, evaluate_freshness
from app.market_contracts.hashing import semantic_content_hash, with_content_hash
from app.market_contracts.identity import EvidenceMarketIdentity, require_perpetual
from app.market_contracts.models import CanonicalDecimal, CanonicalModel
from app.market_contracts.trade_reduction import build_released_trade_snapshot
from app.schemas.common import Timeframe
from app.schemas.nested_continuation import EvidenceAvailability

ORDER_FLOW_METHOD = "real-aggressor-prints/base-and-unrounded-quote/5m/v1"
CVD_RESET = "zero-at-10m-window-start;rebuild-on-window-roll-or-venue-switch/v1"
ORDER_FLOW_BARS = 2
FIVE_MINUTES = timedelta(minutes=5)
_LINEAGE = UUID("cffa00d3-52aa-4b86-93be-7a0b65d6d000")


class FiveMinuteFlow(CanonicalModel):
    window_start: AwareDatetime
    window_end: AwareDatetime
    aggressive_buy_base_volume: CanonicalDecimal
    aggressive_sell_base_volume: CanonicalDecimal
    aggressive_buy_quote_volume: CanonicalDecimal
    aggressive_sell_quote_volume: CanonicalDecimal
    signed_volume_delta: CanonicalDecimal
    quote_volume_delta: CanonicalDecimal
    trade_count: int = Field(ge=0)
    # Signed quote imbalance (buy-sell)/(buy+sell); null for a zero denominator.
    buy_sell_imbalance_ratio: CanonicalDecimal | None
    rolling_cvd: CanonicalDecimal
    rolling_quote_cvd: CanonicalDecimal
    event_time: AwareDatetime | None
    terminal_price: CanonicalDecimal | None

    @model_validator(mode="after")
    def _valid(self) -> FiveMinuteFlow:
        if self.window_end - self.window_start != FIVE_MINUTES:
            raise ValueError("Order flow requires exactly five-minute intervals.")
        if int(self.window_start.timestamp()) % 300 or self.window_start.microsecond:
            raise ValueError("Order-flow intervals must align to UTC five-minute boundaries.")
        buy, sell = self.aggressive_buy_quote_volume, self.aggressive_sell_quote_volume
        if min(buy, sell, self.aggressive_buy_base_volume, self.aggressive_sell_base_volume) < 0:
            raise ValueError("Aggressive volumes cannot be negative.")
        if (
            self.signed_volume_delta
            != (self.aggressive_buy_base_volume - self.aggressive_sell_base_volume)
            or self.quote_volume_delta != buy - sell
        ):
            raise ValueError("Signed volumes must reconcile to aggressive volumes.")
        expected = (buy - sell) / (buy + sell) if buy + sell else None
        if self.buy_sell_imbalance_ratio != expected:
            raise ValueError("Imbalance must equal signed quote delta / total quote volume.")
        if self.trade_count:
            if self.event_time is None or self.terminal_price is None:
                raise ValueError("Nonempty windows require print event time and price.")
            if not self.window_start <= self.event_time < self.window_end:
                raise ValueError("Trade event time must lie in its half-open window.")
        elif self.event_time is not None or self.terminal_price is not None:
            raise ValueError("Empty windows cannot contain print times or prices.")
        elif buy or sell or self.aggressive_buy_base_volume or self.aggressive_sell_base_volume:
            raise ValueError("Empty windows cannot contain volume.")
        return self


class OrderFlowObservation(CanonicalModel):
    identity: EvidenceMarketIdentity
    window_start: AwareDatetime
    window_end: AwareDatetime
    observed_at: AwareDatetime
    event_time: AwareDatetime | None
    availability: EvidenceAvailability
    completeness: DataCompleteness
    freshness: FreshnessEvaluation | None
    freshness_policy_version: str
    base_units: str
    quote_units: str
    cvd_units: str
    calculation_method: str = ORDER_FLOW_METHOD
    reset_semantics: str = CVD_RESET
    baseline: CanonicalDecimal = Decimal("0")
    series_identity: str
    coverage_content_hash: str | None = None
    trade_set_hash: str | None = None
    coverage_kind: Literal["proven_executed_trade_window", "unproven"] = "unproven"
    windows: tuple[FiveMinuteFlow, ...] = ()
    rolling_cvd: CanonicalDecimal | None = None
    rolling_quote_cvd: CanonicalDecimal | None = None
    cvd_change: CanonicalDecimal | None = None
    cvd_slope_base_per_second: CanonicalDecimal | None = None
    cvd_supportive_side: str | None = None
    cvd_weakening: bool | None = None
    order_flow_strengthening_side: str | None = None
    # Two print-close points only; not a swing/pivot divergence or probability.
    cvd_divergence: str | None = None
    state_method: str = "last-vs-prior-5m;directional-delta-and-quote-imbalance;print-close/v1"
    reason: str | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _valid(self) -> OrderFlowObservation:
        require_perpetual(self.identity)
        if self.identity.timeframe is not Timeframe.M5:
            raise ValueError("Order-flow identity must declare five minutes.")
        if self.window_end - self.window_start != FIVE_MINUTES * ORDER_FLOW_BARS:
            raise ValueError("CVD has a fixed, bounded ten-minute observation window.")
        if self.baseline != 0:
            raise ValueError("Internal bounded CVD resets to zero.")
        if self.availability in {EvidenceAvailability.AVAILABLE, EvidenceAvailability.STALE}:
            if self.completeness is not DataCompleteness.COMPLETE or self.freshness is None:
                raise ValueError("Usable evidence requires complete coverage and freshness.")
            if len(self.windows) != ORDER_FLOW_BARS or not self.coverage_content_hash:
                raise ValueError("Usable evidence requires both closed windows and coverage.")
            if self.coverage_kind != "proven_executed_trade_window":
                raise ValueError("Usable flow requires explicitly proven historical coverage.")
            base, quote = Decimal("0"), Decimal("0")
            for index, window in enumerate(self.windows):
                if window.window_start != self.window_start + FIVE_MINUTES * index:
                    raise ValueError("Order-flow windows must be contiguous.")
                base += window.signed_volume_delta
                quote += window.quote_volume_delta
                if window.rolling_cvd != base or window.rolling_quote_cvd != quote:
                    raise ValueError("CVD must reconcile to signed trade-print sums.")
            if self.rolling_cvd != base or self.rolling_quote_cvd != quote:
                raise ValueError("Rolling CVD must match the declared observation window.")
            if self.cvd_change != self.windows[-1].signed_volume_delta or (
                self.cvd_slope_base_per_second
                != self.windows[-1].signed_volume_delta / Decimal(300)
            ):
                raise ValueError("CVD change and slope must reconcile to the final window.")
            times = [window.event_time for window in self.windows if window.event_time is not None]
            if not times or self.event_time != max(times):
                raise ValueError("Observation event time must equal the latest print time.")
            expected_states = _states(list(self.windows))
            for key in (
                "cvd_supportive_side",
                "cvd_weakening",
                "order_flow_strengthening_side",
                "cvd_divergence",
            ):
                if getattr(self, key) != expected_states.get(key):
                    raise ValueError(
                        "Order-flow states must follow the deterministic state method."
                    )
            if self.window_end > self.observed_at or self.event_time is None:
                raise ValueError("Evidence requires a closed window and a print event time.")
        elif self.windows or self.rolling_cvd is not None:
            raise ValueError("Unavailable evidence cannot carry usable flow values.")
        if self.availability not in {
            EvidenceAvailability.AVAILABLE,
            EvidenceAvailability.STALE,
        } and any(
            value is not None
            for value in (
                self.cvd_change,
                self.cvd_slope_base_per_second,
                self.rolling_quote_cvd,
                self.cvd_supportive_side,
                self.cvd_weakening,
                self.order_flow_strengthening_side,
                self.cvd_divergence,
            )
        ):
            raise ValueError("Unavailable evidence cannot offer directional confirmation.")
        return self


def order_flow_identity(identity: EvidenceMarketIdentity) -> EvidenceMarketIdentity:
    return identity.model_copy(update={"timeframe": Timeframe.M5})


def order_flow_freshness_policy() -> FreshnessPolicy:
    return FreshnessPolicy(
        policy_version="5m-real-print/event-age-600s/v1",
        trade_max_age_seconds=600,
        aging_age_seconds=600,
        ohlcv_post_close_grace_seconds=0,
        max_clock_skew_seconds=0,
    )


def closed_order_flow_bounds(observed_at: datetime) -> tuple[datetime, datetime]:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Order-flow observation time must be timezone-aware.")
    epoch = int(observed_at.timestamp())
    end = datetime.fromtimestamp(epoch - epoch % 300, tz=UTC)
    return end - FIVE_MINUTES * ORDER_FLOW_BARS, end


def hash_order_flow(item: OrderFlowObservation) -> OrderFlowObservation:
    return with_content_hash(item, extra_exclude=frozenset({"freshness"}))


def order_flow_observation(
    *,
    identity: EvidenceMarketIdentity,
    observed_at: datetime,
    snapshot: TradeStreamSnapshot | None = None,
    availability: EvidenceAvailability = EvidenceAvailability.MISSING,
    reason: str | None = None,
    window_end: datetime | None = None,
) -> OrderFlowObservation:
    identity = order_flow_identity(identity)
    start, end = closed_order_flow_bounds(observed_at)
    if window_end is not None:
        if window_end > end or window_end.microsecond or int(window_end.timestamp()) % 300:
            raise ValueError("Requested order-flow end must be a closed five-minute boundary.")
        end, start = window_end, window_end - FIVE_MINUTES * ORDER_FLOW_BARS
    windows = []
    freshness, event_time = None, None
    coverage_hash, trade_hash = None, None
    if snapshot is not None:
        if snapshot.cursor.identity != identity:
            raise WrongSourceError("Order-flow snapshot has a different venue/source identity.")
        require_cvd_stream_proof(snapshot)
        require_complete_window_coverage(
            snapshot.coverage,
            identity=identity,
            lineage_id=snapshot.cursor.connection_identity,
            trades=snapshot.trades,
            required_start=start,
            required_end=end,
            released=snapshot.released_tape,
        )
        if snapshot.released_tape is None:
            snapshot = build_released_trade_snapshot(
                snapshot.trades,
                identity=identity,
                lineage_id=snapshot.cursor.connection_identity,
                window_start=start,
                window_end=end,
                observed_at=observed_at,
            )
        tape = snapshot.released_tape
        assert tape is not None
        if tape.window_start != start or tape.window_end != end:
            raise WrongSourceError("Order-flow snapshot must cover exactly the declared window.")
        coverage_hash, trade_hash = snapshot.coverage.content_hash, tape.trade_set_hash
        base, quote = Decimal("0"), Decimal("0")
        for index in range(ORDER_FLOW_BARS):
            left = start + FIVE_MINUTES * index
            right = left + FIVE_MINUTES
            bar = next((bar for bar in tape.bars if bar.interval_start == left), None)
            if bar is not None and (
                bar.interval_end != right
                or bar.buy_base_volume is None
                or bar.sell_base_volume is None
            ):
                raise MarketContractError("Five-minute base-volume reduction is incomplete.")
            buy_base = bar.buy_base_volume if bar else Decimal("0")
            sell_base = bar.sell_base_volume if bar else Decimal("0")
            assert buy_base is not None and sell_base is not None
            buy_quote = bar.buy_quote_volume if bar else Decimal("0")
            sell_quote = bar.sell_quote_volume if bar else Decimal("0")
            delta, quote_delta = buy_base - sell_base, buy_quote - sell_quote
            base += delta
            quote += quote_delta
            windows.append(
                FiveMinuteFlow(
                    window_start=left,
                    window_end=right,
                    aggressive_buy_base_volume=buy_base,
                    aggressive_sell_base_volume=sell_base,
                    aggressive_buy_quote_volume=buy_quote,
                    aggressive_sell_quote_volume=sell_quote,
                    signed_volume_delta=delta,
                    quote_volume_delta=quote_delta,
                    trade_count=bar.event_count if bar else 0,
                    buy_sell_imbalance_ratio=quote_delta / (buy_quote + sell_quote)
                    if buy_quote + sell_quote
                    else None,
                    rolling_cvd=base,
                    rolling_quote_cvd=quote,
                    event_time=bar.event_time_max if bar else None,
                    terminal_price=bar.terminal_price if bar else None,
                )
            )
        if sum(window.trade_count for window in windows) != tape.event_count:
            raise MarketContractError("Five-minute reduction did not account for every print.")
        event_time = tape.event_time_max
        freshness = evaluate_freshness(
            source_time=event_time,
            evaluated_at=observed_at,
            policy=order_flow_freshness_policy(),
            require_fresh=False,
        )
        availability = (
            EvidenceAvailability.STALE
            if freshness.state is FreshnessState.STALE
            else EvidenceAvailability.AVAILABLE
        )
        if freshness.state is FreshnessState.UNKNOWN:
            raise MarketContractError("Order flow has a future event time.")
        reason = (
            "print_event_exceeds_max_age" if availability is EvidenceAvailability.STALE else None
        )
    states = _states(windows)
    return hash_order_flow(
        OrderFlowObservation(
            identity=identity,
            window_start=start,
            window_end=end,
            observed_at=observed_at,
            event_time=event_time,
            availability=availability,
            completeness=DataCompleteness.COMPLETE if windows else DataCompleteness.PARTIAL,
            freshness=freshness,
            freshness_policy_version=order_flow_freshness_policy().policy_version,
            base_units=identity.instrument.base_quantity_unit,
            quote_units=identity.instrument.quote_asset,
            cvd_units=identity.instrument.base_quantity_unit,
            series_identity=semantic_content_hash(
                {
                    "identity": identity.model_dump(mode="python"),
                    "window_start": start,
                    "reset_semantics": CVD_RESET,
                }
            ),
            coverage_content_hash=coverage_hash,
            coverage_kind="proven_executed_trade_window" if windows else "unproven",
            trade_set_hash=trade_hash,
            windows=tuple(windows),
            rolling_cvd=windows[-1].rolling_cvd if windows else None,
            rolling_quote_cvd=windows[-1].rolling_quote_cvd if windows else None,
            cvd_change=windows[-1].signed_volume_delta if windows else None,
            cvd_slope_base_per_second=windows[-1].signed_volume_delta / Decimal(300)
            if windows
            else None,
            reason=reason,
            content_hash="0" * 64,
            **states,
        )
    )


class _FlowStates(TypedDict, total=False):
    cvd_supportive_side: str | None
    cvd_weakening: bool | None
    order_flow_strengthening_side: str | None
    cvd_divergence: str | None


def _states(windows: list[FiveMinuteFlow]) -> _FlowStates:
    if len(windows) != ORDER_FLOW_BARS or any(window.trade_count == 0 for window in windows):
        return {}
    previous, current = windows
    prior, delta = previous.signed_volume_delta, current.signed_volume_delta
    side = "buy" if delta > 0 else "sell" if delta < 0 else None
    same_direction = prior * delta > 0
    weakening = abs(delta) < abs(prior) if same_direction else None
    strengthening = None
    if (
        same_direction
        and abs(delta) > abs(prior)
        and previous.buy_sell_imbalance_ratio is not None
        and current.buy_sell_imbalance_ratio is not None
        and prior * previous.buy_sell_imbalance_ratio > 0
        and delta * current.buy_sell_imbalance_ratio > 0
        and abs(current.buy_sell_imbalance_ratio) > abs(previous.buy_sell_imbalance_ratio)
    ):
        strengthening = side
    divergence = None
    if current.terminal_price is not None and previous.terminal_price is not None:
        if current.terminal_price < previous.terminal_price and delta > 0:
            divergence = "bullish_print_close_to_close"
        elif current.terminal_price > previous.terminal_price and delta < 0:
            divergence = "bearish_print_close_to_close"
    return {
        "cvd_supportive_side": side,
        "cvd_weakening": weakening,
        "order_flow_strengthening_side": strengthening,
        "cvd_divergence": divergence,
    }


def require_order_flow(
    item: OrderFlowObservation | None,
    *,
    identity: EvidenceMarketIdentity,
    evaluated_at: datetime,
) -> None:
    if item is None:
        raise MarketContractError("required_order_flow:MISSING")
    # Revalidate structure as well as semantic integrity at the consumer boundary.
    try:
        item = OrderFlowObservation.model_validate(item.model_dump())
    except ValueError as exc:
        raise WrongSourceError("required_order_flow:invalid_structure") from exc
    if item.identity != order_flow_identity(identity):
        raise WrongSourceError("required_order_flow:wrong_source")
    if (
        hash_order_flow(item).content_hash != item.content_hash
        or item.calculation_method != ORDER_FLOW_METHOD
        or item.reset_semantics != CVD_RESET
        or item.freshness_policy_version != order_flow_freshness_policy().policy_version
        or item.base_units != identity.instrument.base_quantity_unit
        or item.cvd_units != identity.instrument.base_quantity_unit
        or item.quote_units != identity.instrument.quote_asset
        or item.state_method
        != "last-vs-prior-5m;directional-delta-and-quote-imbalance;print-close/v1"
        or item.series_identity
        != semantic_content_hash(
            {
                "identity": order_flow_identity(identity).model_dump(mode="python"),
                "window_start": item.window_start,
                "reset_semantics": CVD_RESET,
            }
        )
    ):
        raise WrongSourceError("required_order_flow:wrong_hash_or_policy")
    if item.availability is not EvidenceAvailability.AVAILABLE:
        raise MarketContractError(f"required_order_flow:{item.availability.value}")
    if item.observed_at > evaluated_at or item.window_end > evaluated_at:
        raise StaleEvidenceError("required_order_flow:future_observation_or_window")
    assert item.event_time is not None
    evaluate_freshness(
        source_time=item.event_time, evaluated_at=evaluated_at, policy=order_flow_freshness_policy()
    )


def order_flow_lineage(identity: EvidenceMarketIdentity) -> UUID:
    # Persistent provider overlap proof, never shared across venues/instruments.
    return uuid5(_LINEAGE, f"{identity.instrument.instrument_id}:{ORDER_FLOW_METHOD}")


def require_order_flow_request(
    *,
    identity: EvidenceMarketIdentity,
    start: datetime,
    end: datetime,
    observed_at: datetime,
) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise MarketContractError("Order-flow request bounds must be timezone-aware.")
    _start, closed_end = closed_order_flow_bounds(observed_at)
    if (
        identity.timeframe is not Timeframe.M5
        or end > closed_end
        or end - start != FIVE_MINUTES * ORDER_FLOW_BARS
        or end.microsecond
        or int(end.timestamp()) % 300
    ):
        raise MarketContractError("Order flow requires two aligned, closed five-minute windows.")
