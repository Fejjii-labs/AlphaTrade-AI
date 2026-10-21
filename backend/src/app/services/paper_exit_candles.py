"""Closed candles for the paper exit monitor.

Live trades read the same public perpetual source the live watcher uses for
that evidence identity (Binance USD-M, or Bybit when the trade's evidence is
Bybit). A live Binance trade keeps a configured Bybit secondary, and a
primary failure rebinds the page to that secondary identity. Replay fixtures
stay on the replay adapter and are used for tests and backtests. The process
default ``perpetual_evidence_source=replay`` does not hide a live trade. A
window that does not reach the entry is incomplete: the monitor must not
close, because an earlier stop may sit outside the fetched bars.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import structlog

from app.core.config import Environment, Settings
from app.market_activation.profile import (
    LIVE_SOURCE,
    canonicalize_perpetual_evidence_source,
)
from app.market_contracts.adapters.factory import resolve_perpetual_evidence_source
from app.market_contracts.adapters.protocol import PerpetualMarketSource
from app.market_contracts.catalog import catalog_for_symbols, instrument_for_source
from app.market_contracts.enums import SourceFamily, VenueId
from app.market_contracts.errors import EvidenceSourceSwitchRequiredError, MarketContractError
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.identity import (
    EvidenceMarketIdentity,
    InstrumentIdentity,
    interval_timedelta,
)
from app.market_contracts.ohlcv import ClosedOhlcvSeries, OhlcvBar
from app.schemas.common import Timeframe
from app.services.paper_exit_monitor import ExitBar, FetchedBars, OpenInternalPaperTrade

_MAX_PAGE_BARS = 1400
_MAX_PAGES = 24
_FALLBACK_PAGES = (200, 50, 1)
# Binance USD-M rejects a page whose evaluation time is inside 5 seconds
# after a bar close, and age zero counts. The next cursor is the oldest
# bar's start, which is the previous bar's end. Stay past that guard or a
# short first page can never be extended. Matches
# ``BINANCE_REST_SETTLEMENT_GRACE``. Bybit classifies a bar with
# ``FIRST_SLICE_OHLCV_GRACE_SECONDS`` (0), so its post-close guard is not
# longer than 5 seconds and this clearance stays one value for every source.
_PAGE_CURSOR_CLEARANCE = timedelta(seconds=5)

logger = structlog.get_logger(__name__)

CandleMode = Literal["replay", "binance_usdm", "bybit_usdt_perpetual"]
CandleOrigin = Literal["trade", "process", "staging_live"]


@dataclass(frozen=True, slots=True)
class _CandleCache:
    earliest_requested: datetime
    bars: tuple[ExitBar, ...]
    source_name: str
    candle_venue: str


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
                candle_venue=cached.candle_venue,
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
        if fetched.switched_from and fetched.candle_venue:
            logger.info(
                "paper_exit_monitor_candle_venue_switch",
                journal_trade_id=str(trade.journal_trade_id),
                from_venue=fetched.switched_from,
                to_venue=fetched.candle_venue,
                timeframe=trade.timeframe.value,
            )
        self._cache[key] = _CandleCache(
            earliest_requested=trade.entry_time,
            bars=fetched.bars,
            source_name=fetched.source_name,
            candle_venue=fetched.candle_venue,
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
    failover. A live Binance trade keeps a configured Bybit secondary so a
    regional primary failure can rebind. Any other trade identity, and the
    staging live default, builds that venue alone and does not follow the
    replay default.
    """

    if mode == "replay":
        from app.market_contracts.adapters.replay import ReplayPerpetualSource

        return ReplayPerpetualSource()
    if origin == "process":
        return resolve_perpetual_evidence_source(settings, shared=True)
    secondary = settings.perpetual_evidence_secondary_source.strip().lower()
    if mode == "binance_usdm" and secondary == "bybit_usdt_perpetual":
        tuned = settings.model_copy(
            update={
                "perpetual_evidence_source": "binance_usdm",
                "perpetual_evidence_secondary_source": "bybit_usdt_perpetual",
            }
        )
        return resolve_perpetual_evidence_source(tuned, shared=True)
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

    A venue switch discards every bar already collected and starts the walk
    again on the new venue. One series is one venue. A second switch, or a
    page whose bars are not that venue, returns no bars.
    """

    interval = interval_timedelta(timeframe)
    if interval.total_seconds() <= 0 or evaluated_at <= entry_time:
        return FetchedBars(bars=(), covers_entry=False, source_name=_source_name(source))
    first = _walk_pages(
        source,
        identity=identity,
        instrument=instrument,
        timeframe=timeframe,
        entry_time=entry_time,
        evaluated_at=evaluated_at,
        allow_switch=True,
    )
    if first.switch_to is None:
        return FetchedBars(
            bars=first.bars,
            covers_entry=first.covers_entry,
            source_name=_source_name(source),
            candle_venue=first.candle_venue,
        )
    rebound_identity, rebound_instrument = first.switch_to
    if rebound_identity.venue is identity.venue:
        return FetchedBars(bars=(), covers_entry=False, source_name=_source_name(source))
    second = _walk_pages(
        source,
        identity=rebound_identity,
        instrument=rebound_instrument,
        timeframe=timeframe,
        entry_time=entry_time,
        evaluated_at=evaluated_at,
        allow_switch=False,
    )
    if second.switch_to is not None or second.refused:
        return FetchedBars(
            bars=(),
            covers_entry=False,
            source_name=_source_name(source),
            candle_venue=rebound_identity.venue.value,
            switched_from=identity.venue.value,
        )
    return FetchedBars(
        bars=second.bars,
        covers_entry=second.covers_entry,
        source_name=_source_name(source),
        candle_venue=second.candle_venue or rebound_identity.venue.value,
        switched_from=identity.venue.value,
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


@dataclass(frozen=True, slots=True)
class _WalkResult:
    bars: tuple[ExitBar, ...]
    covers_entry: bool
    candle_venue: str
    switch_to: tuple[EvidenceMarketIdentity, InstrumentIdentity] | None = None
    refused: bool = False


@dataclass(frozen=True, slots=True)
class _PageRead:
    bars: tuple[OhlcvBar, ...] | None
    identity: EvidenceMarketIdentity
    instrument: InstrumentIdentity
    switch_to: tuple[EvidenceMarketIdentity, InstrumentIdentity] | None = None
    refuse: bool = False


def _walk_pages(
    source: PerpetualMarketSource,
    *,
    identity: EvidenceMarketIdentity,
    instrument: InstrumentIdentity,
    timeframe: Timeframe,
    entry_time: datetime,
    evaluated_at: datetime,
    allow_switch: bool,
) -> _WalkResult:
    """Page backward on one identity. A switch returns the new identity and no bars."""

    interval = interval_timedelta(timeframe)
    span = evaluated_at - entry_time
    needed = int(span / interval) + 3
    collected: dict[str, OhlcvBar] = {}
    cursor = evaluated_at
    active_identity = identity
    active_instrument = instrument
    for _page in range(_MAX_PAGES):
        page = _fetch_page(
            source,
            identity=active_identity,
            instrument=active_instrument,
            timeframe=timeframe,
            evaluated_at=cursor,
            needed=needed,
            allow_switch=allow_switch,
        )
        if page.switch_to is not None:
            return _WalkResult(
                bars=(),
                covers_entry=False,
                candle_venue="",
                switch_to=page.switch_to,
            )
        if page.refuse:
            return _WalkResult(bars=(), covers_entry=False, candle_venue="", refused=True)
        active_identity = page.identity
        active_instrument = page.instrument
        if not page.bars:
            break
        if any(
            not isinstance(bar, ExitBar) and bar.instrument.venue is not active_identity.venue
            for bar in page.bars
        ):
            return _WalkResult(bars=(), covers_entry=False, candle_venue="", refused=True)
        added = 0
        for bar in page.bars:
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
    if not collected:
        return _WalkResult(bars=(), covers_entry=False, candle_venue="")
    venues = {bar.instrument.venue for bar in collected.values() if not isinstance(bar, ExitBar)}
    if len(venues) > 1:
        return _WalkResult(bars=(), covers_entry=False, candle_venue="", refused=True)
    ordered = tuple(
        _exit_bar(bar) for bar in sorted(collected.values(), key=lambda bar: bar.interval_start)
    )
    venue = next(iter(venues)).value if venues else active_identity.venue.value
    if any(bar.venue not in {"", venue} for bar in ordered):
        return _WalkResult(bars=(), covers_entry=False, candle_venue="", refused=True)
    return _WalkResult(
        bars=ordered,
        covers_entry=_covers(ordered, entry_time),
        candle_venue=venue,
    )


def _fetch_page(
    source: PerpetualMarketSource,
    *,
    identity: EvidenceMarketIdentity,
    instrument: InstrumentIdentity,
    timeframe: Timeframe,
    evaluated_at: datetime,
    needed: int,
    allow_switch: bool,
) -> _PageRead:
    """Read one page. A required switch is returned so the caller can restart.

    ``EvidenceSourceSwitchRequiredError`` means the next read must use the
    secondary instrument. The bars from the venue that failed are not
    returned. A second switch in the same page, or a switch when this walk
    already committed to one venue, refuses the page.
    """

    current_identity = identity
    current_instrument = instrument
    switched = False
    for size in _page_sizes(needed):
        try:
            series = _read_closed_page(
                source,
                identity=current_identity,
                instrument=current_instrument,
                timeframe=timeframe,
                evaluated_at=evaluated_at,
                size=size,
            )
        except EvidenceSourceSwitchRequiredError as exc:
            if switched or not allow_switch or not isinstance(exc.instrument, InstrumentIdentity):
                return _PageRead(
                    None,
                    current_identity,
                    current_instrument,
                    refuse=True,
                )
            switched = True
            current_instrument = exc.instrument
            current_identity = first_slice_identity(
                timeframe=timeframe,
                replay=False,
                is_live=True,
                instrument=current_instrument,
            )
            switch_to = (current_identity, current_instrument)
            try:
                _read_closed_page(
                    source,
                    identity=current_identity,
                    instrument=current_instrument,
                    timeframe=timeframe,
                    evaluated_at=evaluated_at,
                    size=size,
                )
            except MarketContractError:
                return _PageRead(
                    None,
                    current_identity,
                    current_instrument,
                    switch_to=switch_to,
                )
            return _PageRead(
                None,
                current_identity,
                current_instrument,
                switch_to=switch_to,
            )
        except MarketContractError:
            continue
        return _PageRead(tuple(series.bars), current_identity, current_instrument)
    return _PageRead(None, current_identity, current_instrument)


def _read_closed_page(
    source: PerpetualMarketSource,
    *,
    identity: EvidenceMarketIdentity,
    instrument: InstrumentIdentity,
    timeframe: Timeframe,
    evaluated_at: datetime,
    size: int,
) -> ClosedOhlcvSeries:
    return source.fetch_closed_ohlcv(
        identity=identity,
        instrument=instrument,
        timeframe=timeframe,
        min_final_bars=size,
        evaluated_at=evaluated_at,
    )


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
    if isinstance(bar, ExitBar):
        return bar
    return ExitBar(
        interval_start=bar.interval_start,
        interval_end=bar.interval_end,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        source_event_id=bar.source_event_id,
        content_hash=bar.content_hash,
        venue=bar.instrument.venue.value,
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
