"""OI/funding acquisition and required-input checks on the existing evidence source."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from app.market_contracts.adapters.protocol import PerpetualMarketSource
from app.market_contracts.cursor import TradeStreamSnapshot
from app.market_contracts.derivatives import (
    DerivativeMetric,
    DerivativeObservation,
    derivative_identity,
    derivative_observation,
    hash_derivative_observation,
    require_derivative_observations,
)
from app.market_contracts.errors import (
    EmptyTradeWindowError,
    EvidenceSourceSwitchRequiredError,
    MarketContractError,
    RateLimitedError,
    RegionalProviderFailureError,
    UnsupportedTradeContractError,
    WrongSourceError,
)
from app.market_contracts.identity import EvidenceMarketIdentity, InstrumentIdentity
from app.market_contracts.order_book import (
    OrderBookObservation,
    book_identity,
    hash_order_book,
    order_book_observation,
)
from app.market_contracts.order_flow import (
    OrderFlowObservation,
    closed_order_flow_bounds,
    order_flow_identity,
    order_flow_lineage,
    order_flow_observation,
)
from app.schemas.nested_continuation import EvidenceAvailability
from app.signal_fusion.enums import EvidenceRole
from app.signal_fusion.order_flow_inputs import ORDER_FLOW_ROLES as ORDER_FLOW_ROLES

DERIVATIVE_ROLES = {
    EvidenceRole.OPEN_INTEREST: DerivativeMetric.OPEN_INTEREST,
    EvidenceRole.FUNDING: DerivativeMetric.FUNDING,
}


def read_order_book(
    source: PerpetualMarketSource,
    *,
    identity: EvidenceMarketIdentity,
    instrument: InstrumentIdentity,
    observed_at: datetime,
    allow_later_observation: bool = False,
) -> OrderBookObservation:
    try:
        fetch = getattr(source, "fetch_order_book_observation", None)
        if not callable(fetch):
            return order_book_observation(
                identity=identity,
                observed_at=observed_at,
                availability=EvidenceAvailability.UNSUPPORTED,
                reason="provider_has_no_resting_book_contract",
            )
        item = fetch(identity=identity, instrument=instrument, observed_at=observed_at)
        if (
            not isinstance(item, OrderBookObservation)
            or item.identity != book_identity(identity)
            or (item.observed_at != observed_at and not allow_later_observation)
            or hash_order_book(item).content_hash != item.content_hash
        ):
            raise WrongSourceError("Resting book returned an incompatible observation.")
        return OrderBookObservation.model_validate(item.model_dump())
    except EvidenceSourceSwitchRequiredError:
        raise
    except (MarketContractError, ValueError, TypeError, KeyError, OverflowError) as exc:
        return order_book_observation(
            identity=identity,
            observed_at=observed_at,
            availability=EvidenceAvailability.MISSING
            if isinstance(exc, RegionalProviderFailureError | RateLimitedError)
            else EvidenceAvailability.INCOMPLETE,
            reason=f"resting_book_failure:{type(exc).__name__}",
        )


def read_market_intelligence(
    source: PerpetualMarketSource,
    *,
    identity: EvidenceMarketIdentity,
    instrument: InstrumentIdentity,
    observed_at: datetime,
    metrics: Sequence[DerivativeMetric] = tuple(DerivativeMetric),
    allow_later_observation: bool = False,
    acquisition_clock: Callable[[], datetime] | None = None,
) -> tuple[DerivativeObservation, ...]:
    """Read exact provider facts, with bounded receipt time for current scans.

    An acquisition clock permits later receipts, never events beyond the fixed
    requested cutoff. Without it, the strict as-of observation contract remains.
    The optional presentation reader separately validates its final context clock.
    """
    observations = []
    for metric in metrics:
        try:
            fetch = getattr(source, "fetch_derivative_observation", None)
            if not callable(fetch):
                observations.append(
                    derivative_observation(
                        identity=identity,
                        metric=metric,
                        observed_at=observed_at,
                        availability=EvidenceAvailability.UNSUPPORTED,
                        reason="provider_has_no_oi_funding_contract",
                    )
                )
                continue
            result = fetch(
                identity=identity,
                instrument=instrument,
                metric=metric,
                observed_at=observed_at,
            )
            if not isinstance(result, DerivativeObservation):
                raise WrongSourceError("OI/funding provider returned an invalid observation.")
            result = DerivativeObservation.model_validate(result.model_dump())
            completed_at = acquisition_clock() if acquisition_clock is not None else observed_at
            if acquisition_clock is not None and (
                completed_at.tzinfo is None
                or completed_at.utcoffset() is None
                or completed_at < observed_at
                or result.observed_at > completed_at
                or result.collected_at is None
                or result.collected_at > completed_at
                or (result.event_time is not None and result.event_time > observed_at)
            ):
                raise WrongSourceError("OI/funding violates the acquisition clock or event cutoff.")
            if (
                result.metric is not metric
                or result.identity != derivative_identity(identity, metric)
                or (
                    result.observed_at != observed_at
                    and not allow_later_observation
                    and acquisition_clock is None
                )
                or hash_derivative_observation(result).content_hash != result.content_hash
            ):
                raise WrongSourceError("OI/funding result does not match the requested source.")
        except EvidenceSourceSwitchRequiredError:
            # Discard all prior facts; reassemble under the new venue identity.
            raise
        except (MarketContractError, ValueError, TypeError) as exc:
            result = derivative_observation(
                identity=identity,
                metric=metric,
                observed_at=observed_at,
                availability=(
                    EvidenceAvailability.MISSING
                    if isinstance(exc, RegionalProviderFailureError | RateLimitedError)
                    else EvidenceAvailability.INCOMPLETE
                ),
                reason=f"provider_failure:{type(exc).__name__}",
            )
        observations.append(result)
    return tuple(observations)


def require_market_intelligence(
    observations: Sequence[DerivativeObservation],
    *,
    required_roles: Sequence[EvidenceRole],
    identity: EvidenceMarketIdentity,
    evaluated_at: datetime,
) -> None:
    require_derivative_observations(
        observations,
        required_metrics=tuple(
            DERIVATIVE_ROLES[r] for r in required_roles if r in DERIVATIVE_ROLES
        ),
        identity=identity,
        evaluated_at=evaluated_at,
    )


def read_order_flow(
    source: PerpetualMarketSource,
    *,
    identity: EvidenceMarketIdentity,
    instrument: InstrumentIdentity,
    observed_at: datetime,
    window_end: datetime | None = None,
) -> OrderFlowObservation:
    """Reuse the provider's proven bounded trade acquisition; never infer coverage."""
    normalized = order_flow_identity(identity)
    start, end = closed_order_flow_bounds(observed_at)
    if window_end is not None:
        # Validate bounds before making an upstream read.
        empty = order_flow_observation(
            identity=normalized,
            observed_at=observed_at,
            window_end=window_end,
        )
        start, end = empty.window_start, empty.window_end
    try:
        fetch = getattr(source, "fetch_order_flow_snapshot", None)
        if not callable(fetch):
            return order_flow_observation(
                identity=normalized,
                observed_at=observed_at,
                window_end=end,
                availability=EvidenceAvailability.UNSUPPORTED,
                reason="provider_has_no_verified_trade_print_contract",
            )
        snapshot = fetch(
            identity=normalized,
            instrument=instrument,
            start=start,
            end=end,
            source_connection_id=order_flow_lineage(normalized),
            receive_at=observed_at,
        )
        if not isinstance(snapshot, TradeStreamSnapshot):
            raise WrongSourceError("Order-flow provider returned an invalid snapshot.")
        return order_flow_observation(
            identity=normalized,
            observed_at=observed_at,
            snapshot=snapshot,
            window_end=end,
        )
    except EvidenceSourceSwitchRequiredError:
        # Atomic read restart under the new venue; bounded CVD baseline resets to zero.
        raise
    except (MarketContractError, ValueError, TypeError, KeyError, OverflowError) as exc:
        state = (
            EvidenceAvailability.MISSING
            if isinstance(
                exc, RegionalProviderFailureError | RateLimitedError | EmptyTradeWindowError
            )
            else EvidenceAvailability.UNSUPPORTED
            if isinstance(exc, UnsupportedTradeContractError)
            else EvidenceAvailability.INCOMPLETE
        )
        return order_flow_observation(
            identity=normalized,
            observed_at=observed_at,
            window_end=end,
            availability=state,
            reason=f"trade_print_failure:{type(exc).__name__}",
        )
