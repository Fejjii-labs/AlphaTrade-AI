"""Typed optional market context for the existing Agent market read consumer."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from app.market_contracts.derivatives import DerivativeObservation
from app.market_contracts.models import CanonicalModel
from app.market_contracts.order_book import OrderBookObservation
from app.market_contracts.order_flow import OrderFlowObservation

MarketContextObservation = DerivativeObservation | OrderFlowObservation | OrderBookObservation


class UnavailableContextMetric(CanonicalModel):
    """Capability limitation, not an observation or a zero-valued market fact."""

    availability: Literal["UNSUPPORTED"] = "UNSUPPORTED"
    value: None = None
    reason: str = Field(min_length=3, max_length=120)


class MarketEvidenceContext(CanonicalModel):
    """Stable v1 optional context; required qualification uses canonical evidence.

    Event selection can retain an earlier cutoff than evaluation/receipt. Every
    available observation still needs a consumer freshness/hash/identity check.
    Unsupported capabilities below carry no inferred observations or values.
    """

    contract_version: Literal["public-market-context/v1"] = "public-market-context/v1"
    evaluated_at: AwareDatetime
    evidence_cutoff_at: AwareDatetime | None = None
    anchor_venue: str = Field(min_length=2, max_length=32)
    anchor_symbol: str = Field(min_length=2, max_length=32)
    derivatives: tuple[DerivativeObservation, ...] = ()
    order_flow: OrderFlowObservation | None = None
    order_book: OrderBookObservation | None = None
    cross_venue_components: tuple[str, ...] = ()
    qualification_authority: Literal[False] = False
    open_interest_change: UnavailableContextMetric = Field(
        default_factory=lambda: UnavailableContextMetric(
            reason="no_verified_paired_causal_oi_samples"
        )
    )
    open_interest_notional: UnavailableContextMetric = Field(
        default_factory=lambda: UnavailableContextMetric(reason="native_oi_notional_not_collected")
    )
    historical_order_book: UnavailableContextMetric = Field(
        default_factory=lambda: UnavailableContextMetric(
            reason="historical_snapshot_coverage_unavailable"
        )
    )

    @model_validator(mode="after")
    def _labels(self) -> MarketEvidenceContext:
        if self.evidence_cutoff_at is not None and self.evidence_cutoff_at > self.evaluated_at:
            raise ValueError("Evidence cutoff cannot occur after evaluation.")
        if len({x.metric for x in self.derivatives}) != len(self.derivatives):
            raise ValueError("Each derivative metric must have one explicit source.")
        components: list[tuple[str, MarketContextObservation]] = [
            (x.metric.value, x) for x in self.derivatives
        ]
        components += [
            (name, x)
            for name, x in (("order_flow", self.order_flow), ("order_book", self.order_book))
            if x is not None
        ]
        expected = tuple(
            name for name, x in components if x.identity.venue.value != self.anchor_venue
        )
        if self.cross_venue_components != expected:
            raise ValueError("Cross-venue context must be explicitly labeled per component.")
        if any(x.identity.instrument.provider_symbol != self.anchor_symbol for _, x in components):
            raise ValueError("Different-symbol evidence cannot be presented as this market.")
        return self
