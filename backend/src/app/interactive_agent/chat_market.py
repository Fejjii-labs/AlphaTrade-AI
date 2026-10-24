"""Bounded market retrieval for one agent chat turn.

Worker and Watcher scans do not use this module. They keep the shared
Binance client, including its single Retry-After sleep. A chat quote uses a
private client that raises on HTTP 429 and 418 without that sleep, then the
existing Bybit secondary source when settings already select it. The whole
read stops at ``agent_chat_market_retrieval_budget_seconds``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from uuid import UUID

import httpx

from app.core.config import Settings
from app.evidence_pipeline.service import CanonicalEvidenceService
from app.interactive_agent.canonical_market import (
    CanonicalMarketStateError,
    CanonicalPerpetualQuoteReader,
)
from app.interactive_agent.contracts import MarketQuoteView
from app.market_contracts.adapters.factory import resolve_chat_market_source
from app.market_contracts.errors import (
    RateLimitedError,
    RegionalProviderFailureError,
    RetrievalBudgetExceededError,
    UpstreamBanError,
)

MARKET_DATA_UNAVAILABLE = "market data unavailable"
_OUTAGE_REASONS = (
    "reason=unavailable",
    "reason=provider_unavailable",
    "reason=source_switch_failed",
)
_OUTAGE_ERRORS = (
    RateLimitedError,
    UpstreamBanError,
    RetrievalBudgetExceededError,
    RegionalProviderFailureError,
)


class ChatMarketQuoteReader:
    """Read one symbol for a chat turn, or report market data unavailable."""

    def __init__(
        self,
        settings: Settings,
        organization_id: UUID,
        *,
        transport: httpx.BaseTransport | None = None,
        evidence: CanonicalEvidenceService | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._organization_id = organization_id
        self._transport = transport
        self._evidence = evidence
        self._clock = clock or time.monotonic

    def quote(self, symbol: str) -> MarketQuoteView:
        budget = float(self._settings.agent_chat_market_retrieval_budget_seconds)
        deadline = self._clock() + budget
        if self._clock() > deadline:
            raise CanonicalMarketStateError("unavailable", MARKET_DATA_UNAVAILABLE)
        source = None
        service = self._evidence
        try:
            if service is None:
                source = resolve_chat_market_source(
                    self._settings,
                    deadline_monotonic=deadline,
                    transport=self._transport,
                    symbol=symbol,
                )
                service = CanonicalEvidenceService(self._settings, source=source)
            try:
                view = CanonicalPerpetualQuoteReader(service, self._organization_id).quote(symbol)
            except CanonicalMarketStateError as exc:
                if exc.availability != "stale" and _is_market_outage(exc):
                    raise CanonicalMarketStateError("unavailable", MARKET_DATA_UNAVAILABLE) from exc
                raise
            except _OUTAGE_ERRORS as exc:
                raise CanonicalMarketStateError("unavailable", MARKET_DATA_UNAVAILABLE) from exc
            except Exception as exc:
                raise CanonicalMarketStateError("unavailable", MARKET_DATA_UNAVAILABLE) from exc
            if self._clock() > deadline:
                raise CanonicalMarketStateError("unavailable", MARKET_DATA_UNAVAILABLE)
            return view
        finally:
            if source is not None:
                _close_source(source)


def _is_market_outage(exc: CanonicalMarketStateError) -> bool:
    """True when the quote failed closed because the venue could not be read."""

    if isinstance(exc.__cause__, _OUTAGE_ERRORS):
        return True
    return any(marker in exc.reason for marker in _OUTAGE_REASONS)


def _close_source(source: object) -> None:
    closer = getattr(source, "close", None)
    if callable(closer):
        closer()
        return
    for name in ("_primary", "_secondary"):
        child = getattr(source, name, None)
        child_closer = getattr(child, "close", None)
        if callable(child_closer):
            child_closer()
