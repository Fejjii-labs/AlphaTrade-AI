"""Closed candles for the paper exit monitor.

Live trades read the same public perpetual source the live watcher uses for
that evidence identity (Binance USD-M, or Bybit when the trade's evidence is
Bybit). Replay fixtures stay on the replay adapter and are used for tests and
backtests. The process default ``perpetual_evidence_source=replay`` does not
hide a live trade. A window that does not reach the entry is incomplete: the
monitor must not close, because an earlier stop may sit outside the fetched
bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from app.core.config import Environment, Settings
from app.market_activation.profile import (
    LIVE_SOURCE,
    canonicalize_perpetual_evidence_source,
)
from app.market_contracts.adapters.factory import resolve_perpetual_evidence_source
from app.market_contracts.adapters.protocol import PerpetualMarketSource
from app.market_contracts.catalog import catalog_for_symbols, instrument_for_source
from app.market_contracts.enums import SourceFamily, VenueId
from app.market_contracts.errors import MarketContractError
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.identity import (
    EvidenceMarketIdentity,
    InstrumentIdentity,
    interval_timedelta,
)
from app.market_contracts.ohlcv import OhlcvBar
from app.schemas.common import Timeframe
from app.services.paper_exit_monitor import ExitBar, FetchedBars, OpenInternalPaperTrade

_MAX_PAGE_BARS = 1400
_MAX_PAGES = 24
_FALLBACK_PAGES = (200, 50, 1)
# Binance USD-M rejects a page whose evaluation time is inside 5 seconds
# after a bar close, and age zero counts. The next cursor is the oldest
# bar's start, which is the previous bar's end. Stay past that guard or a
# short first page can never be extended. Matches
# ``BINANCE_REST_SETTLEMENT_GRACE``.
_PAGE_CURSOR_CLEARANCE = timedelta(seconds=5)

CandleMode = Literal["replay", "binance_usdm", "bybit_usdt_perpetual"]
CandleOrigin = Literal["trade", "process", "staging_live"]


@dataclass(frozen=True, slots=True)
class _CandleCache:
    earliest_requested: datetime
    bars: tuple[ExitBar, ...]
    source_name: str


class PaperExitCandleLoader:
    """Fetch once per symbol, timeframe, and candle source."""

    def __init__(
        self,
        settings: Settings,
        sources: dict[str, PerpetualMarketSource] | None = None,
    ) -> None:
        self._settings = settings
        self._sources: dict[str, PerpetualMarketSource] = dict(sources or {})
        self._cache: dict[tuple[str, str, str], _CandleCache] = {}

    def __call__(self, trade: OpenInternalPaperTrade, *, evaluated_at: datetime) -> FetchedBars:
        mode, origin = candle_mode_for_trade(trade, self._settings)
        key = (mode, trade.symbol, trade.timeframe.value)
        cached = self._cache.get(key)
        if cached is not None and cached.earliest_requested <= trade.entry_time:
            return FetchedBars(
                bars=cached.bars,
                covers_entry=_covers(cached.bars, trade.entry_time),
                source_name=cached.source_name,
            )
        source = self._source_for(mode, origin)
        identity, instrument = identity_for_trade(
            source,
            symbol=trade.symbol,
            timeframe=trade.timeframe,
            mode=mode,
        )
        fetched = fetch_closed_bars_since(
            source,
            identity=identity,
            instrument=instrument,
            timeframe=trade.timeframe,
            entry_time=trade.entry_time,
            evaluated_at=evaluated_at,
        )
        self._cache[key] = _CandleCache(
            earliest_requested=trade.entry_time,
            bars=fetched.bars,
            source_name=fetched.source_name,
        )
        return fetched

    def _source_for(self, mode: CandleMode, origin: CandleOrigin) -> PerpetualMarketSource:
        cache_key = f"{origin}:{mode}"
        cached = self._sources.get(cache_key)
        if cached is not None:
            return cached
        # Tests inject the live adapter under the mode name.
        injected = self._sources.get(mode)
        if injected is not None:
            self._sources[cache_key] = injected
            return injected
        source = build_candle_source(self._settings, mode, origin)
        self._sources[cache_key] = source
        return source


def candle_mode_for_trade(
    trade: OpenInternalPaperTrade,
    settings: Settings,
) -> tuple[CandleMode, CandleOrigin]:
    """Pick the candle adapter from the trade, then from the environment.

    A stored live identity wins over the process default. Replay and mock
    evidence stay on replay even when the process is pointed at a live venue.
    With no stored identity, a process that is already live keeps that source,
    including its configured failover. Staging with the replay default uses
    Binance USD-M, the live source the watcher uses for BTCUSDT. Local replay
    stays replay so tests and backtests do not open a network read.
    """

    family = (trade.evidence_source_family or "").strip().lower()
    venue = (trade.evidence_venue or "").strip().lower()
    if trade.evidence_is_mock is True or family == SourceFamily.REPLAY_FIXTURE.value:
        return "replay", "trade"
    if trade.evidence_is_live is True and (
        family == SourceFamily.BYBIT_USDT_PERPETUAL_PUBLIC.value or venue == VenueId.BYBIT.value
    ):
        return "bybit_usdt_perpetual", "trade"
    if trade.evidence_is_live is True and (
        family == SourceFamily.BINANCE_USDM_FUTURES_PUBLIC.value or venue == VenueId.BINANCE.value
    ):
        return "binance_usdm", "trade"
    configured = canonicalize_perpetual_evidence_source(settings.perpetual_evidence_source)
    if configured != "replay":
        return configured, "process"
    if settings.environment is Environment.STAGING:
        return LIVE_SOURCE, "staging_live"
    return "replay", "process"


def build_candle_source(
    settings: Settings,
    mode: CandleMode,
    origin: CandleOrigin,
) -> PerpetualMarketSource:
    """Construct the read-only adapter for one resolved mode.

    ``process`` keeps the watcher's configured source, including secondary
    failover. A trade identity or the staging live default builds that venue
    alone and does not follow the replay default.
    """

    if mode == "replay":
        from app.market_contracts.adapters.replay import ReplayPerpetualSource

        return ReplayPerpetualSource()
    if origin == "process":
        return resolve_perpetual_evidence_source(settings, shared=True)
    tuned = settings.model_copy(
        update={
            "perpetual_evidence_source": mode,
            "perpetual_evidence_secondary_source": "none",
        }
    )
    return resolve_perpetual_evidence_source(tuned, shared=True)


def identity_for_trade(
    source: PerpetualMarketSource,
    *,
    symbol: str,
    timeframe: Timeframe,
    mode: CandleMode,
) -> tuple[EvidenceMarketIdentity, InstrumentIdentity]:
    """Same identity construction the OHLCV strategy assembler uses."""

    replay = mode == "replay"
    catalog = catalog_for_symbols([symbol], venue=_venue_for(source, mode))
    instrument = instrument_for_source(source, catalog, symbol)
    identity = first_slice_identity(
        timeframe=timeframe,
        replay=replay,
        is_live=not replay,
        instrument=instrument,
    )
    return identity, instrument


def fetch_closed_bars_since(
    source: PerpetualMarketSource,
    *,
    identity: EvidenceMarketIdentity,
    instrument: InstrumentIdentity,
    timeframe: Timeframe,
    entry_time: datetime,
    evaluated_at: datetime,
) -> FetchedBars:
    """Page ``fetch_closed_ohlcv`` backward until the entry bar is included.

    Each page uses the source contract the strategies use. The page size stays
    under the public REST cap. At most ``_MAX_PAGES`` pages are requested.
    The next page is evaluated just after the oldest bar's start, past the
    public REST settlement guard. The guard rejects a request whose time
    equals that close, which would stop the walk on a short first page.
    """

    interval = interval_timedelta(timeframe)
    if interval.total_seconds() <= 0 or evaluated_at <= entry_time:
        return FetchedBars(bars=(), covers_entry=False, source_name=_source_name(source))
    span = evaluated_at - entry_time
    needed = int(span / interval) + 3
    collected: dict[str, OhlcvBar] = {}
    cursor = evaluated_at
    for _page in range(_MAX_PAGES):
        page = _fetch_page(
            source,
            identity=identity,
            instrument=instrument,
            timeframe=timeframe,
            evaluated_at=cursor,
            needed=needed,
        )
        if not page:
            break
        added = 0
        for bar in page:
            if bar.source_event_id not in collected:
                collected[bar.source_event_id] = bar
                added += 1
        earliest = min(bar.interval_start for bar in collected.values())
        if earliest <= entry_time or added == 0:
            break
        if earliest >= cursor:
            break
        cursor = earliest + _PAGE_CURSOR_CLEARANCE
        if cursor >= evaluated_at:
            break
        needed = _MAX_PAGE_BARS
    ordered = tuple(
        _exit_bar(bar) for bar in sorted(collected.values(), key=lambda bar: bar.interval_start)
    )
    return FetchedBars(
        bars=ordered,
        covers_entry=_covers(ordered, entry_time),
        source_name=_source_name(source),
    )


def _covers(bars: tuple[ExitBar, ...], entry_time: datetime) -> bool:
    """True when the earliest bar starts at or before the trade entry.

    A series that begins later is a missing stretch at the start. The monitor
    refuses that series. Bar order does not matter: the earliest start is
    the check, not whichever bar the page returned first.
    """

    if entry_time.tzinfo is None or not bars:
        return False
    earliest = min(bar.interval_start for bar in bars)
    if earliest.tzinfo is None:
        return False
    return earliest <= entry_time


def _fetch_page(
    source: PerpetualMarketSource,
    *,
    identity: EvidenceMarketIdentity,
    instrument: InstrumentIdentity,
    timeframe: Timeframe,
    evaluated_at: datetime,
    needed: int,
) -> tuple[OhlcvBar, ...] | None:
    for size in _page_sizes(needed):
        try:
            series = source.fetch_closed_ohlcv(
                identity=identity,
                instrument=instrument,
                timeframe=timeframe,
                min_final_bars=size,
                evaluated_at=evaluated_at,
            )
        except MarketContractError:
            continue
        return tuple(series.bars)
    return None


def _page_sizes(needed: int) -> tuple[int, ...]:
    primary = min(max(needed, 1), _MAX_PAGE_BARS)
    sizes: list[int] = [primary]
    for size in _FALLBACK_PAGES:
        if size not in sizes and size <= primary:
            sizes.append(size)
    if 1 not in sizes:
        sizes.append(1)
    return tuple(sizes)


def _exit_bar(bar: OhlcvBar) -> ExitBar:
    return ExitBar(
        interval_start=bar.interval_start,
        interval_end=bar.interval_end,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        source_event_id=bar.source_event_id,
        content_hash=bar.content_hash,
    )


def _source_name(source: PerpetualMarketSource) -> str:
    name = getattr(source, "name", "")
    return name if isinstance(name, str) and name else "perpetual-evidence"


def _venue_for(source: object, mode: CandleMode) -> VenueId:
    active = getattr(source, "active_instrument", None)
    if callable(active):
        instrument = active()
        if isinstance(instrument, InstrumentIdentity):
            return instrument.venue
    if mode == "bybit_usdt_perpetual":
        return VenueId.BYBIT
    return VenueId.BINANCE
