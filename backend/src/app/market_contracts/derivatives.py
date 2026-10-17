"""Read-only, provider-reported OI and settled funding evidence. No flow inference."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, Field, model_validator

from app.market_contracts.enums import FreshnessState, VenueId
from app.market_contracts.errors import MarketContractError, StaleEvidenceError, WrongSourceError
from app.market_contracts.freshness import FreshnessEvaluation, FreshnessPolicy, evaluate_freshness
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import EvidenceMarketIdentity
from app.market_contracts.models import CanonicalDecimal, CanonicalModel, parse_canonical_decimal
from app.schemas.common import Timeframe
from app.schemas.nested_continuation import EvidenceAvailability


class DerivativeMetric(StrEnum):
    OPEN_INTEREST = "open_interest"
    FUNDING = "funding"


class DerivativeObservation(CanonicalModel):
    """Identity includes instrument, venue, market, provider and native timeframe.

    Missing event times stay null; observation time never substitutes for them.
    Funding is a dimensionless settled rate, without annualization or an assumed
    8h interval. OI is never combined across venues or quantity conventions.
    """

    metric: DerivativeMetric
    identity: EvidenceMarketIdentity
    event_time: AwareDatetime | None
    observed_at: AwareDatetime
    collected_at: AwareDatetime | None = None
    coverage_kind: Literal["single_provider_record"] = "single_provider_record"
    historical_coverage: Literal[False] = False
    methodology_version: Literal["provider-reported-derivatives/v2"] = (
        "provider-reported-derivatives/v2"
    )
    freshness: FreshnessEvaluation | None
    freshness_policy_version: str = Field(min_length=3, max_length=80)
    units: str = Field(min_length=1, max_length=80)
    calculation_method: str = Field(min_length=1, max_length=120)
    availability: EvidenceAvailability
    value: CanonicalDecimal | None
    reason: str | None = Field(default=None, max_length=300)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _available_requires_evidence(self) -> DerivativeObservation:
        if self.collected_at is not None and self.collected_at > self.observed_at:
            raise ValueError("Collection cannot occur after observation.")
        if self.availability in {EvidenceAvailability.AVAILABLE, EvidenceAvailability.STALE}:
            if self.value is None or self.event_time is None or self.freshness is None:
                raise ValueError("Available/stale observations require value, time and freshness.")
            if self.collected_at is None or self.event_time > self.collected_at:
                raise ValueError("Usable records require causal collection time.")
        elif self.value is not None:
            raise ValueError("Unavailable observations cannot carry an executable value.")
        if (
            self.metric is DerivativeMetric.OPEN_INTEREST
            and self.value is not None
            and self.value < 0
        ):
            raise ValueError("Open interest cannot be negative.")
        return self


def hash_derivative_observation(item: DerivativeObservation) -> DerivativeObservation:
    """Freshness age is consumer metadata; bind its policy, not a polling clock."""
    return with_content_hash(item, extra_exclude=frozenset({"freshness", "collected_at"}))


def derivative_freshness_policy(metric: DerivativeMetric, venue: VenueId) -> FreshnessPolicy:
    # Consumer age limits, not claims about the venue's funding interval.
    max_age = (
        24 * 3600 if metric is DerivativeMetric.FUNDING else (600 if venue is VenueId.BYBIT else 60)
    )
    return FreshnessPolicy(
        policy_version=f"oi-funding/{venue.value}/{metric.value}/no-future/v2",
        trade_max_age_seconds=max_age,
        aging_age_seconds=max_age,
        ohlcv_post_close_grace_seconds=0,
        max_clock_skew_seconds=0,
    )


def derivative_identity(
    identity: EvidenceMarketIdentity, metric: DerivativeMetric
) -> EvidenceMarketIdentity:
    timeframe = (
        Timeframe.M5
        if metric is DerivativeMetric.OPEN_INTEREST and identity.venue is VenueId.BYBIT
        else None
    )
    return identity.model_copy(update={"timeframe": timeframe})


def derivative_observation(
    *,
    identity: EvidenceMarketIdentity,
    metric: DerivativeMetric,
    observed_at: datetime,
    row: Mapping[str, Any] | None = None,
    value_key: str = "",
    time_key: str = "",
    availability: EvidenceAvailability | None = None,
    reason: str | None = None,
) -> DerivativeObservation:
    identity = derivative_identity(identity, metric)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("Observation time must be timezone-aware.")
    observed_at = observed_at.astimezone(UTC)
    units = (
        "ratio_per_settlement"
        if metric is DerivativeMetric.FUNDING
        else (identity.instrument.base_quantity_unit)
    )
    method = (
        "provider_reported_settled_rate"
        if metric is DerivativeMetric.FUNDING
        else (
            "provider_reported_sum_both_sides_base_quantity"
            if identity.venue is VenueId.BYBIT
            else "provider_reported_base_quantity"
        )
    )
    value, event_time, freshness = None, None, None
    if availability is None:
        if row is None:
            availability, reason = EvidenceAvailability.MISSING, "provider_returned_no_records"
        else:
            try:
                if row.get("symbol", identity.instrument.provider_symbol) != (
                    identity.instrument.provider_symbol
                ):
                    raise ValueError("wrong_instrument")
                value = parse_canonical_decimal(row[value_key])
                if metric is DerivativeMetric.OPEN_INTEREST and value < 0:
                    raise ValueError("negative_open_interest")
                raw_time = row[time_key]
                if isinstance(raw_time, bool | float) or not str(raw_time).isdigit():
                    raise ValueError("invalid_event_time")
                milliseconds = int(raw_time)
                if milliseconds <= 0:
                    raise ValueError("invalid_event_time")
                event_time = datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
                freshness = evaluate_freshness(
                    source_time=event_time,
                    evaluated_at=observed_at,
                    policy=derivative_freshness_policy(metric, identity.venue),
                    require_fresh=False,
                )
                if freshness.state is FreshnessState.UNKNOWN:
                    value = None
                    availability, reason = EvidenceAvailability.INCOMPLETE, "future_event_time"
                elif freshness.state is FreshnessState.STALE:
                    availability, reason = EvidenceAvailability.STALE, "event_time_exceeds_max_age"
                else:
                    availability = EvidenceAvailability.AVAILABLE
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                value, event_time, freshness = None, None, None
                availability, reason = EvidenceAvailability.INCOMPLETE, "malformed_provider_record"
    return hash_derivative_observation(
        DerivativeObservation(
            metric=metric,
            identity=identity,
            event_time=event_time,
            observed_at=observed_at,
            collected_at=observed_at,
            freshness=freshness,
            freshness_policy_version=derivative_freshness_policy(
                metric, identity.venue
            ).policy_version,
            units=units,
            calculation_method=method,
            availability=availability,
            value=value,
            reason=reason,
            content_hash="0" * 64,
        )
    )


def refresh_derivative_observation(
    item: DerivativeObservation, observed_at: datetime
) -> DerivativeObservation:
    """Reuse a causally collected provider fact, re-evaluating age for this consumer."""
    if item.event_time is None or item.availability not in {
        EvidenceAvailability.AVAILABLE,
        EvidenceAvailability.STALE,
    }:
        return item
    freshness = evaluate_freshness(
        source_time=item.event_time,
        evaluated_at=observed_at,
        policy=derivative_freshness_policy(item.metric, item.identity.venue),
        require_fresh=False,
    )
    stale = freshness.state is FreshnessState.STALE
    return hash_derivative_observation(
        item.model_copy(
            update={
                "observed_at": observed_at,
                "freshness": freshness,
                "availability": EvidenceAvailability.STALE
                if stale
                else EvidenceAvailability.AVAILABLE,
                "reason": "event_time_exceeds_max_age" if stale else None,
            }
        )
    )


def require_derivative_observations(
    observations: Sequence[DerivativeObservation],
    *,
    required_metrics: Sequence[DerivativeMetric],
    identity: EvidenceMarketIdentity,
    evaluated_at: datetime,
    event_cutoff_at: datetime | None = None,
) -> None:
    """Re-evaluate age at every strategy consumer boundary; no stale replay of a pass."""
    for metric in required_metrics:
        matches = [item for item in observations if item.metric is metric]
        if len(matches) != 1:
            raise MarketContractError(f"required_{metric.value}:MISSING")
        item = matches[0]
        item = DerivativeObservation.model_validate(item.model_dump())
        if item.identity != derivative_identity(identity, metric):
            raise WrongSourceError(f"required_{metric.value}:wrong_source")
        if hash_derivative_observation(item).content_hash != item.content_hash:
            raise WrongSourceError(f"required_{metric.value}:invalid_content_hash")
        if (
            item.freshness_policy_version
            != derivative_freshness_policy(metric, identity.venue).policy_version
        ):
            raise WrongSourceError(f"required_{metric.value}:wrong_freshness_policy")
        if item.availability is not EvidenceAvailability.AVAILABLE:
            raise MarketContractError(f"required_{metric.value}:{item.availability.value}")
        if item.event_time is None or item.value is None:
            raise MarketContractError(f"required_{metric.value}:INCOMPLETE")
        expected = derivative_observation(
            identity=identity, metric=metric, observed_at=evaluated_at
        )
        if item.units != expected.units or item.calculation_method != expected.calculation_method:
            raise WrongSourceError(f"required_{metric.value}:wrong_units_or_method")
        if item.observed_at > evaluated_at:
            raise StaleEvidenceError(f"required_{metric.value}:future_observation")
        if event_cutoff_at is not None and item.event_time > event_cutoff_at:
            raise WrongSourceError(f"required_{metric.value}:event_after_cutoff")
        evaluate_freshness(
            source_time=item.event_time,
            evaluated_at=evaluated_at,
            policy=derivative_freshness_policy(metric, identity.venue),
        )
