"""Worker-persisted perpetual monitor snapshots.

The paper worker ticks the live monitor and writes the latest snapshot per
symbol. The API reads that row and never ticks a monitor or opens an upstream
connection. A missing row is unavailable; an old row is stale.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock

import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Environment, Settings
from app.core.errors import ValidationAppError
from app.db.market_monitor_snapshots import MarketMonitorSnapshotRow
from app.evidence_pipeline.types import CurrentPricePresentation
from app.market_contracts.adapters.factory import perpetual_source_is_replay
from app.market_contracts.catalog import PerpetualInstrumentCatalog, default_perpetual_catalog
from app.market_contracts.enums import (
    DataCompleteness,
    GapState,
    ReconnectState,
    SourceFamily,
    WarmUpStatus,
)
from app.market_contracts.errors import WrongInstrumentError
from app.market_monitor.identity import monitor_semantic_hash
from app.market_monitor.types import (
    BackoffReport,
    CoverageReport,
    MarketAvailability,
    MarketMode,
    MonitorReason,
    OhlcvHealth,
    ProviderHealthReport,
    StreamCvdReport,
    StreamHealth,
    SymbolMonitorSnapshot,
)
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus

logger = structlog.get_logger("market_monitor.persisted")

# Deterministic identity for synthesized "no worker state" snapshots.
_MISSING_CONNECTION = uuid.UUID(int=0)


def api_monitor_runs_in_process(settings: Settings) -> bool:
    """True when the API may own a monitor without upstream I/O at request time.

    Replay reads fixtures only. A live in-process monitor is a LOCAL opt-in.
    """

    if perpetual_source_is_replay(settings):
        return True
    return settings.environment is Environment.LOCAL and settings.market_monitor_api_in_process_live


def save_monitor_snapshot(
    session: Session,
    snapshot: SymbolMonitorSnapshot,
    *,
    worker_id: str,
    written_at: datetime,
) -> None:
    payload = snapshot.model_dump(mode="json")
    row = session.get(MarketMonitorSnapshotRow, snapshot.symbol)
    if row is None:
        session.add(
            MarketMonitorSnapshotRow(
                symbol=snapshot.symbol,
                payload=payload,
                evaluated_at=snapshot.evaluated_at,
                written_at=written_at,
                worker_id=worker_id,
            )
        )
        return
    row.payload = payload
    row.evaluated_at = snapshot.evaluated_at
    row.written_at = written_at
    row.worker_id = worker_id


def load_monitor_snapshot(
    session: Session, symbol: str
) -> tuple[SymbolMonitorSnapshot, datetime] | None:
    """Latest persisted snapshot and its write time. Unreadable rows are missing."""

    row = session.get(MarketMonitorSnapshotRow, symbol)
    if row is None:
        return None
    try:
        snapshot = SymbolMonitorSnapshot.model_validate(row.payload)
    except ValueError:
        logger.warning("market_monitor_snapshot_unreadable", symbol=symbol)
        return None
    written = row.written_at
    if written.tzinfo is None:
        written = written.replace(tzinfo=UTC)
    return snapshot, written


class MarketMonitorSnapshotPublisher:
    """Best-effort worker sink. Throttled per symbol; never raises."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        worker_id: str,
        min_interval_seconds: float = 5.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._worker_id = worker_id
        self._min_interval = timedelta(seconds=min_interval_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._last_written: dict[str, datetime] = {}
        self._lock = Lock()

    def __call__(self, snapshot: SymbolMonitorSnapshot) -> None:
        now = self._clock()
        with self._lock:
            last = self._last_written.get(snapshot.symbol)
            if last is not None and now - last < self._min_interval:
                return
            self._last_written[snapshot.symbol] = now
        try:
            with self._session_factory() as session, session.begin():
                save_monitor_snapshot(session, snapshot, worker_id=self._worker_id, written_at=now)
        except Exception:
            logger.warning(
                "market_monitor_snapshot_persist_failed",
                symbol=snapshot.symbol,
                worker_id=self._worker_id,
                exc_info=True,
            )


class PersistedMarketMonitorReader:
    """Request-time monitor view from the worker row. Never ticks, never fetches."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        catalog: PerpetualInstrumentCatalog | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._catalog = catalog if catalog is not None else default_perpetual_catalog()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._stale_after = timedelta(seconds=settings.market_monitor_snapshot_stale_after_seconds)

    def snapshot(self, symbol: str = "BTCUSDT") -> SymbolMonitorSnapshot:
        try:
            instrument = self._catalog.require(symbol)
        except WrongInstrumentError as exc:
            raise ValidationAppError(str(exc), code="unknown_perpetual_instrument") from exc
        now = self._clock().astimezone(UTC)
        loaded = load_monitor_snapshot(self._session, instrument.provider_symbol)
        if loaded is None:
            return self._missing(instrument.provider_symbol, instrument.instrument_id, now)
        snapshot, written_at = loaded
        if now - written_at > self._stale_after:
            return _rehash(
                snapshot.model_copy(
                    update={
                        "availability": MarketAvailability.STALE,
                        "reason": MonitorReason.MONITOR_STATE_STALE,
                        "current_price": None,
                    }
                )
            )
        quote = snapshot.current_price
        if (
            quote is not None
            and quote.usable_as_current_market_price
            and quote.freshness.valid_until <= now
        ):
            # The worker row is recent, but its last trade has aged past the
            # quote clock. Show it; never present it as a current mark.
            aged = quote.model_copy(
                update={
                    "usable_as_current_market_price": False,
                    "presentation": CurrentPricePresentation.STALE,
                }
            )
            return _rehash(snapshot.model_copy(update={"current_price": aged}))
        return snapshot

    def latest(self, symbol: str = "BTCUSDT") -> SymbolMonitorSnapshot:
        return self.snapshot(symbol)

    def _missing(
        self, provider_symbol: str, instrument_id: str, now: datetime
    ) -> SymbolMonitorSnapshot:
        source = self._settings.perpetual_evidence_source.strip().lower()
        family = (
            SourceFamily.BYBIT_USDT_PERPETUAL_PUBLIC
            if source.startswith("bybit")
            else SourceFamily.BINANCE_USDM_FUTURES_PUBLIC
        )
        detail = "No paper-worker monitor snapshot persisted for this symbol."
        snapshot = SymbolMonitorSnapshot(
            symbol=provider_symbol,
            mode=MarketMode.LIVE_PERPETUAL,
            availability=MarketAvailability.UNAVAILABLE,
            reason=MonitorReason.MONITOR_STATE_MISSING,
            current_price=None,
            last_update=None,
            evaluated_at=now,
            instrument_id=instrument_id,
            provider_symbol=provider_symbol,
            source_family=family,
            provider_name=source or "unknown",
            is_live=True,
            is_mock=False,
            stream=StreamHealth(
                reconnect_state=ReconnectState.INITIAL,
                gap_state=GapState.NONE,
                warm_up_status=WarmUpStatus.EMPTY,
                reconnect_count=0,
                connection_identity=_MISSING_CONNECTION,
            ),
            coverage=CoverageReport(completeness=DataCompleteness.UNKNOWN, gap_state=GapState.NONE),
            cvd=StreamCvdReport(available=False, reason="monitor_state_missing"),
            ohlcv=OhlcvHealth(
                available=False,
                completeness_15m=DataCompleteness.UNKNOWN,
                completeness_4h=DataCompleteness.UNKNOWN,
                reason="monitor_state_missing",
            ),
            provider=ProviderHealthReport(
                name=source or "unknown",
                health=ProviderHealth.UNAVAILABLE,
                is_mock=False,
                detail=detail,
            ),
            backoff=BackoffReport(active=False, attempt=0),
            content_hash="0" * 64,
        )
        return _rehash(snapshot)


class PersistedPerpetualEvidenceStatus:
    """API provider-status view of the perpetual source. Never probes the venue.

    Reports the provider health the paper worker last observed. A missing or
    stale row is degraded, not healthy.
    """

    def __init__(
        self,
        *,
        name: str,
        kind: ProviderKind,
        settings: Settings,
        session_factory: Callable[[], Session] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.name = name
        self.kind = kind
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._stale_after = timedelta(seconds=settings.market_monitor_snapshot_stale_after_seconds)
        self._symbol = default_perpetual_catalog().enabled_symbols()[0]

    def status(self) -> ProviderStatus:
        try:
            with self._factory()() as session:
                loaded = load_monitor_snapshot(session, self._symbol)
        except Exception:
            logger.warning("market_monitor_status_read_failed", exc_info=True)
            return self._degraded("Paper-worker monitor state could not be read.")
        if loaded is None:
            return self._degraded("No paper-worker monitor state; the API does not probe.")
        snapshot, written_at = loaded
        if self._clock() - written_at > self._stale_after:
            return self._degraded("Paper-worker monitor state is stale.")
        provider = snapshot.provider
        return ProviderStatus(
            name=self.name,
            kind=self.kind,
            health=provider.health,
            is_mock=provider.is_mock,
            using_fallback=False,
            detail=provider.detail,
            last_success_at=snapshot.last_update,
        )

    def _degraded(self, detail: str) -> ProviderStatus:
        return ProviderStatus(
            name=self.name, kind=self.kind, health=ProviderHealth.DEGRADED, detail=detail
        )

    def _factory(self) -> Callable[[], Session]:
        if self._session_factory is not None:
            return self._session_factory
        from app.db.session import get_session_factory

        return get_session_factory()


def _rehash(snapshot: SymbolMonitorSnapshot) -> SymbolMonitorSnapshot:
    return snapshot.model_copy(update={"content_hash": monitor_semantic_hash(snapshot)})
