"""Canonical perpetual evidence for agent market answers.

Unavailable and stale reads stay labeled. This module does not invent a price
and does not copy a spot or mock ticker over a failed perpetual read.
"""

from __future__ import annotations

from uuid import UUID

from app.core.errors import AppError
from app.evidence_pipeline.http_schemas import CanonicalEvidenceRead
from app.evidence_pipeline.service import CanonicalEvidenceService
from app.interactive_agent.contracts import MarketQuoteView
from app.market_contracts.context import MarketContextObservation, MarketEvidenceContext

_USABLE_PRESENTATIONS = frozenset({"live_mark", "replay_fixture"})


class CanonicalMarketStateError(Exception):
    """A perpetual read that must not be shown as a current price."""

    def __init__(self, availability: str, reason: str) -> None:
        self.availability = availability
        self.reason = reason
        super().__init__(f"{availability}: {reason}")


class CanonicalPerpetualQuoteReader:
    """Read one symbol through CanonicalEvidenceService for a single tenant."""

    def __init__(self, evidence: CanonicalEvidenceService, organization_id: UUID) -> None:
        self._evidence = evidence
        self._organization_id = organization_id

    def quote(self, symbol: str) -> MarketQuoteView:
        try:
            read = self._evidence.read(organization_id=self._organization_id, symbol=symbol)
        except AppError as exc:
            code = exc.code or "unavailable"
            raise CanonicalMarketStateError("unavailable", f"reason={code}") from exc
        return market_view_from_evidence(read)


def market_view_from_evidence(read: CanonicalEvidenceRead) -> MarketQuoteView:
    """Map a canonical read to a quote, or raise when no current price exists."""
    price = read.current_price
    presentation = price.presentation
    freshness = price.freshness.state
    stale = presentation == "stale" or freshness == "stale"
    if stale and price.price:
        return _view(read, is_stale=True, is_live=False)
    if stale:
        raise CanonicalMarketStateError("stale", _reason(read))
    usable = (
        price.usable_as_current_market_price
        and bool(price.price)
        and presentation in _USABLE_PRESENTATIONS
    )
    if usable:
        live = bool(price.is_live and presentation == "live_mark")
        return _view(read, is_stale=False, is_live=live)
    raise CanonicalMarketStateError("unavailable", _reason(read))


def _reason(read: CanonicalEvidenceRead) -> str:
    parts = [f"presentation={read.current_price.presentation}"]
    if read.unavailable_reason:
        parts.append(f"reason={read.unavailable_reason}")
    freshness = read.current_price.freshness.state
    if freshness:
        parts.append(f"freshness={freshness}")
    return " ".join(parts)[:300]


def _view(read: CanonicalEvidenceRead, *, is_stale: bool, is_live: bool) -> MarketQuoteView:
    price = read.current_price
    if not price.price:
        raise CanonicalMarketStateError("unavailable", _reason(read))
    source = f"canonical:{read.source.source_family}:{price.presentation}"
    provider = read.source.provider_name.strip() or "canonical"
    components: list[tuple[str, MarketContextObservation]] = [
        (x.metric.value, x) for x in read.market_intelligence
    ]
    components += [
        (name, x)
        for name, x in (("order_flow", read.order_flow), ("order_book", read.order_book))
        if x is not None
    ]
    evaluated_at = (
        read.timestamps.get("market_context_evaluated_at")
        or read.timestamps.get("evaluated_at")
        or price.freshness.evaluated_at
    )
    context = MarketEvidenceContext(
        evaluated_at=evaluated_at,
        evidence_cutoff_at=read.timestamps.get("evaluated_at"),
        anchor_venue=read.source.venue,
        anchor_symbol=read.symbol,
        derivatives=read.market_intelligence,
        order_flow=read.order_flow,
        order_book=read.order_book,
        cross_venue_components=tuple(
            name for name, x in components if x.identity.venue.value != read.source.venue
        ),
    )
    return MarketQuoteView(
        symbol=read.symbol,
        last_price=price.price,
        source=source[:80],
        is_live=is_live,
        is_stale=is_stale,
        fallback_used=False,
        provider_name=provider[:80],
        evidence_context=context,
    )
