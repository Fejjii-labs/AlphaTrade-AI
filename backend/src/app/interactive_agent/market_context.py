"""Deterministic Agent presentation, rechecking every optional evidence boundary."""

from __future__ import annotations

import re

from app.market_contracts.context import MarketContextObservation, MarketEvidenceContext
from app.market_contracts.derivatives import DerivativeMetric, require_derivative_observations
from app.market_contracts.errors import MarketContractError, StaleEvidenceError
from app.market_contracts.order_book import require_order_book
from app.market_contracts.order_flow import require_order_flow
from app.schemas.nested_continuation import EvidenceAvailability


def _unusable_state(reported: EvidenceAvailability, exc: Exception) -> str:
    if isinstance(exc, StaleEvidenceError):
        return EvidenceAvailability.STALE.value
    return (
        EvidenceAvailability.INCOMPLETE.value
        if reported is EvidenceAvailability.AVAILABLE
        else reported.value
    )


def _reported_reason(reason: str | None) -> str:
    # Contract reasons are codes; never echo arbitrary provider bodies into Agent context.
    return reason if reason and re.fullmatch(r"[A-Za-z0-9_:-]{1,120}", reason) else "unrecorded"


def _provenance(item: MarketContextObservation) -> str:
    identity = item.identity
    parts = [
        f"provider={identity.source.provider_name}",
        f"adapter={identity.source.adapter_version}",
        f"instrument={identity.instrument.instrument_id}",
        f"observed_at={item.observed_at.isoformat()}",
    ]
    if item.event_time is not None:
        parts.append(f"event_time={item.event_time.isoformat()}")
    collected_at = getattr(item, "collected_at", None)
    if collected_at is not None:
        parts.append(f"collected_at={collected_at.isoformat()}")
    parts.append(f"method={item.calculation_method}")
    return "; ".join(parts)


def market_context_text(context: MarketEvidenceContext) -> str:
    parts: list[str] = [f"Market context evaluated_at={context.evaluated_at.isoformat()}."]
    if not any(item.metric is DerivativeMetric.OPEN_INTEREST for item in context.derivatives):
        parts.append("Open interest: MISSING.")
    for item in context.derivatives:
        label = f"{item.metric.value} venue={item.identity.venue.value}"
        if item.metric is not DerivativeMetric.OPEN_INTEREST:
            continue
        try:
            require_derivative_observations(
                (item,),
                required_metrics=(item.metric,),
                identity=item.identity,
                evaluated_at=context.evaluated_at,
                event_cutoff_at=context.evidence_cutoff_at,
            )
            parts.append(
                f"{label}: {item.value} {item.units}; single provider record, "
                f"OI change and OI notional unavailable. {_provenance(item)}."
            )
        except (MarketContractError, ValueError) as exc:
            parts.append(
                f"{label}: {_unusable_state(item.availability, exc)}; no usable OI value; "
                f"reason={_reported_reason(item.reason)}; consumer_check={type(exc).__name__}; "
                f"{_provenance(item)}."
            )
    flow = context.order_flow
    if flow is None:
        parts.append("Executed flow and CVD: MISSING.")
    else:
        label = f"Executed flow/CVD venue={flow.identity.venue.value}"
        try:
            require_order_flow(flow, identity=flow.identity, evaluated_at=context.evaluated_at)
            last = flow.windows[-1]
            parts.append(
                f"{label}: closed 5m buy={last.aggressive_buy_base_volume} "
                f"sell={last.aggressive_sell_base_volume} {flow.base_units}; "
                f"10m CVD={flow.rolling_cvd} {flow.cvd_units}, reset to zero at "
                f"{flow.window_start.isoformat()}; divergence={flow.cvd_divergence or 'none'}; "
                f"coverage={flow.coverage_kind}; window_end={flow.window_end.isoformat()}; "
                f"{_provenance(flow)}."
            )
        except (MarketContractError, ValueError) as exc:
            parts.append(
                f"{label}: {_unusable_state(flow.availability, exc)}; "
                f"coverage or freshness unavailable; reason={_reported_reason(flow.reason)}; "
                f"consumer_check={type(exc).__name__}; {_provenance(flow)}."
            )
    book = context.order_book
    if book is None:
        parts.append("Resting order book: MISSING.")
    else:
        label = f"Resting order book venue={book.identity.venue.value}"
        try:
            require_order_book(book, identity=book.identity, evaluated_at=context.evaluated_at)
            parts.append(
                f"{label}: spread={book.spread} {book.price_units}; "
                f"snapshot of up to {book.requested_depth} levels per side; "
                f"historical coverage unavailable; RPI excluded; {_provenance(book)}."
            )
        except (MarketContractError, ValueError) as exc:
            parts.append(
                f"{label}: {_unusable_state(book.availability, exc)}; no usable liquidity value; "
                f"reason={_reported_reason(book.reason)}; consumer_check={type(exc).__name__}; "
                f"{_provenance(book)}."
            )
    if context.cross_venue_components:
        parts.append("Cross-venue context: " + ", ".join(context.cross_venue_components) + ".")
    return " ".join(parts)
