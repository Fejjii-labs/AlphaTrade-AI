"""Closed candles for the paper exit monitor.

Uses the same perpetual evidence source the strategy assembler calls
(``fetch_closed_ohlcv`` via :func:`resolve_perpetual_evidence_source`). A
window that does not reach the entry is incomplete: the monitor must not
close, because an earlier stop may sit outside the fetched bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.core.config import Settings
from app.market_contracts.adapters.factory import (
    perpetual_source_is_replay,
    resolve_perpetual_evidence_source,
)
from app.market_contracts.adapters.protocol import PerpetualMarketSource
from app.market_contracts.catalog import catalog_for_symbols, instrument_for_source
from app.market_contracts.enums import VenueId
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


@dataclass(frozen=True, slots=True)
class _CandleCache:
    earliest_requested: datetime
    bars: tuple[ExitBar, ...]
    source_name: str


class PaperExitCandleLoader:
    """Fetch once per symbol and timeframe, from the oldest entry in the pass."""

    def __init__(self, settings: Settings, source: PerpetualMarketSource) -> None:
        self._settings = settings
        self._source = source
        self._cache: dict[tuple[str, str], _CandleCache] = {}

    def __call__(self, trade: OpenInternalPaperTrade, *, evaluated_at: datetime) -> FetchedBars:
        key = (trade.symbol, trade.timeframe.value)
        cached = self._cache.get(key)
        if cached is not None and cached.earliest_requested <= trade.entry_time:
            return FetchedBars(
                bars=cached.bars,
                covers_entry=_covers(cached.bars, trade.entry_time),
                source_name=cached.source_name,
            )
        identity, instrument = identity_for_trade(
            self._settings,
            self._source,
            symbol=trade.symbol,
            timeframe=trade.timeframe,
        )
        fetched = fetch_closed_bars_since(
            self._source,
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


def build_exit_candle_source(settings: Settings) -> PerpetualMarketSource:
    """The process evidence source. Replay stays replay; live stays that venue."""

    return resolve_perpetual_evidence_source(settings, shared=True)


def identity_for_trade(
    settings: Settings,
    source: PerpetualMarketSource,
    *,
    symbol: str,
    timeframe: Timeframe,
) -> tuple[EvidenceMarketIdentity, InstrumentIdentity]:
    """Same identity construction the OHLCV strategy assembler uses."""

    replay = perpetual_source_is_replay(settings)
    catalog = catalog_for_symbols([symbol], venue=_venue_for(settings, source))
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
        cursor = earliest
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
    return bool(bars) and bars[0].interval_start <= entry_time


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


def _venue_for(settings: Settings, source: object) -> VenueId:
    active = getattr(source, "active_instrument", None)
    if callable(active):
        instrument = active()
        if isinstance(instrument, InstrumentIdentity):
            return instrument.venue
    mode = settings.perpetual_evidence_source.strip().lower()
    if mode == "bybit_usdt_perpetual":
        return VenueId.BYBIT
    return VenueId.BINANCE
