"""Canonical evidence from the paper worker's persisted monitor row.

Deployed (non-replay) APIs serve ``GET /canonical/evidence`` from the
``market_monitor_snapshots`` row the paper worker writes. No request-time
upstream I/O: no Binance/Bybit HTTP, no socket, no live source construction.

Fail closed:
- missing row -> price unavailable, ``unavailable_reason=monitor_state_missing``;
- old row -> price dropped, ``monitor_state_stale``;
- recent row whose last trade aged past the quote clock -> price shown as stale,
  never as a current mark.

Setup evidence (assembled 15m trigger window) is not persisted by the worker,
so it is reported ``available=False`` with reason ``setup_not_persisted``.
Completeness and CVD facts come from the row and are informational only.
Market intelligence, order flow and order book are empty, never fabricated.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.core.config import Settings
from app.evidence_pipeline.http_schemas import (
    CanonicalCompletenessRead,
    CanonicalEvidenceRead,
    CanonicalSetupEvidenceRead,
)
from app.market_monitor.persisted import PersistedMarketMonitorReader
from app.market_monitor.service import status_from_snapshot

SETUP_NOT_PERSISTED = "setup_not_persisted"
MONITOR_STATE_NOT_CURRENT = frozenset({"monitor_state_missing", "monitor_state_stale"})


class CanonicalEvidenceReader(Protocol):
    def read(self, *, organization_id: UUID, symbol: str = "BTCUSDT") -> CanonicalEvidenceRead: ...


class PersistedCanonicalEvidenceService:
    """Request-time evidence view from the worker row. Never ticks, never fetches."""

    def __init__(self, reader: PersistedMarketMonitorReader, *, settings: Settings) -> None:
        self._reader = reader
        self._settings = settings

    def read(self, *, organization_id: UUID, symbol: str = "BTCUSDT") -> CanonicalEvidenceRead:
        snapshot, written_at, age_seconds = self._reader.view(symbol)
        # Reuse the market-status projection so both panels agree byte-for-byte
        # on source identity, price presentation and unavailable reason.
        status = status_from_snapshot(
            snapshot,
            settings=self._settings,
            written_at=written_at,
            age_seconds=age_seconds,
        )
        price = status.current_price
        setup = CanonicalSetupEvidenceRead(
            available=False,
            completeness=CanonicalCompletenessRead(
                ohlcv_15m=snapshot.ohlcv.completeness_15m.value,
                ohlcv_4h=snapshot.ohlcv.completeness_4h.value,
                cvd="complete" if snapshot.cvd.available else "unknown",
                signed_flow="complete" if snapshot.cvd.signed_flow_ratio is not None else "unknown",
            ),
            reason=SETUP_NOT_PERSISTED,
        )
        unavailable = None
        if not price.usable_as_current_market_price:
            unavailable = status.unavailable_reason or price.presentation
        source = status.source
        if unavailable in MONITOR_STATE_NOT_CURRENT:
            # No current worker state: the source is not a live feed right now.
            source = source.model_copy(update={"is_live": False})
        return CanonicalEvidenceRead(
            organization_id=str(organization_id),
            symbol=snapshot.provider_symbol,
            source=source,
            current_price=price,
            setup_evidence=setup,
            market_intelligence=(),
            order_flow=None,
            order_book=None,
            timestamps={
                "evaluated_at": snapshot.evaluated_at,
                "market_context_evaluated_at": None,
                "current_price_source_time": price.source_time,
                "trigger_interval_start": None,
                "trigger_interval_end": None,
                "monitor_written_at": written_at,
            },
            unavailable_reason=unavailable,
        )
