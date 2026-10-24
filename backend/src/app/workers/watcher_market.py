"""Bounded symbol-specific, read-only production market composition.

Discovery proves listing eligibility only. A probe fetches fresh closed candles;
it is not strategy evidence, current-price authority or a Candidate producer.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.setup_lifetime import SetupLifetimeStore
from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
from app.market_contracts.adapters.binance_usdm import (
    BINANCE_REST_SETTLEMENT_GRACE,
    BinanceUsdmPerpetualSource,
)
from app.market_contracts.adapters.factory import (
    perpetual_source_is_replay,
    resolve_perpetual_evidence_source,
)
from app.market_contracts.adapters.failover import FailoverPerpetualSource
from app.market_contracts.catalog import catalog_for_symbols, instrument_for_source
from app.market_contracts.contract_discovery import fetch_binance_usdm_exchange_info
from app.market_contracts.enums import VenueId
from app.market_contracts.errors import (
    ContractUnavailableError,
    EvidenceSourceSwitchRequiredError,
    StaleEvidenceError,
    WrongInstrumentError,
    WrongMarketError,
)
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.identity import require_instrument
from app.market_contracts.provider_contracts import (
    ContractBook,
    ContractCheck,
    ContractProviderUnreachableError,
    ContractVerdict,
    apply_binance_exchange_info,
    contract_from_bybit_instruments,
    default_contract_book,
    venue_for_evidence_source,
)
from app.market_monitor.factory import build_perpetual_market_monitor
from app.market_monitor.watcher_port import MarketMonitorWatcherPort
from app.persistence.setup_lifetime import SqlAlchemySetupLifetimeStore
from app.schemas.common import Timeframe
from app.watcher.ports import WatcherStore


@dataclass(frozen=True)
class MarketProbeResult:
    symbol: str
    provider: str
    freshness: str
    observed_at: datetime


class WatchlistContractDiscovery:
    """Bounded cache with retry after TTL, separate from market acquisition."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.clock = clock
        self.book = ContractBook(())
        self.verdicts: dict[tuple[str, VenueId], ContractVerdict] = {}
        self._cache: OrderedDict[tuple[str, VenueId], tuple[float, ContractVerdict]] = OrderedDict()
        self._binance: tuple[float, dict | None] | None = None

    def __call__(self, book: ContractBook, symbols: Sequence[str]) -> ContractBook:
        if perpetual_source_is_replay(self.settings):
            self.book = default_contract_book()
            self.verdicts = {}
            return self.book
        venues = [venue_for_evidence_source(self.settings.perpetual_evidence_source)]
        if (
            self.settings.perpetual_evidence_secondary_source == "bybit_usdt_perpetual"
            and VenueId.BYBIT not in venues
        ):
            venues.append(VenueId.BYBIT)
        now = self.clock()
        result = ContractBook(())
        verdicts = {}
        with httpx.Client(
            transport=self.transport,
            timeout=self.settings.perpetual_evidence_timeout_seconds,
            follow_redirects=False,
        ) as http:
            for symbol in symbols[:5]:
                for venue in venues:
                    key = (symbol, venue)
                    cached = self._cache.get(key)
                    if cached is not None and 0 <= now - cached[0] < 60:
                        verdict = cached[1]
                    else:
                        verdict = self._fetch(http, symbol, venue, now)
                        self._cache[key] = (now, verdict)
                    self._cache.move_to_end(key)
                    while len(self._cache) > 10:
                        self._cache.popitem(last=False)
                    verdicts[key] = verdict
                    if verdict.contract is not None and verdict.state is ContractCheck.VERIFIED:
                        result = result.with_contract(verdict.contract)
        self.book, self.verdicts = result, verdicts
        return result

    def _fetch(
        self, http: httpx.Client, symbol: str, venue: VenueId, now: float
    ) -> ContractVerdict:
        try:
            if venue is VenueId.BINANCE:
                if self._binance is None or not 0 <= now - self._binance[0] < 60:
                    try:
                        payload = fetch_binance_usdm_exchange_info(client=http)
                    except ContractProviderUnreachableError:
                        payload = None
                    self._binance = (now, payload)
                payload = self._binance[1]
                if payload is None:
                    raise ContractProviderUnreachableError("provider_unreachable")
                _, verdicts = apply_binance_exchange_info(ContractBook(()), payload, [symbol])
                return verdicts[0]
            response = http.get(
                "https://api.bybit.com/v5/market/instruments-info",
                params={"category": "linear", "symbol": symbol},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("retCode") != 0:
                raise ContractProviderUnreachableError("provider_unreachable")
            contract = contract_from_bybit_instruments(payload, requested_symbol=symbol)
            return ContractVerdict(symbol, venue, ContractCheck.VERIFIED, "verified", contract)
        except (WrongInstrumentError, WrongMarketError):
            return ContractVerdict(symbol, venue, ContractCheck.UNSUPPORTED, "unsupported_contract")
        except (httpx.HTTPError, ValueError, ContractProviderUnreachableError):
            return ContractVerdict(symbol, venue, ContractCheck.UNREACHABLE, "provider_unreachable")

    def require(self, symbol: str, venue: VenueId) -> None:
        if perpetual_source_is_replay(self.settings):
            if symbol != "BTCUSDT":
                raise ContractUnavailableError("replay_symbol_unavailable")
            return
        verdict = self.verdicts.get((symbol, venue))
        if verdict is None:
            raise ContractUnavailableError("awaiting_contract_check")
        if verdict.state is not ContractCheck.VERIFIED or not self.book.supports(symbol, venue):
            raise ContractUnavailableError(verdict.reason)


class _EligibleSource:
    """Recheck the selected venue before every acquisition, including failover."""

    def __init__(
        self, source: object, check: Callable[[], None], on_read: Callable[[], None]
    ) -> None:
        self.source, self.check, self.on_read = source, check, on_read

    def __getattr__(self, name: str):
        value = getattr(self.source, name)
        if name == "reduce_ordered_trades":

            def guarded(**kwargs):
                self.check()
                return value(**kwargs)

            return guarded
        return value

    def fetch_closed_ohlcv(self, **kwargs):
        self.check()
        result = self.source.fetch_closed_ohlcv(**kwargs)
        self.on_read()
        return result

    def fetch_ordered_trades(self, **kwargs):
        self.check()
        return self.source.fetch_ordered_trades(**kwargs)


def _active_rest_source(source: object) -> object:
    """Return the adapter a probe will read, under the eligibility wrapper.

    ``_EligibleSource.__getattr__`` raises for names the inner adapter lacks, so
    ``getattr(source, "active_source", source)`` cannot see through it.
    """

    current = source
    if isinstance(current, FailoverPerpetualSource):
        current = current.active_source
    if isinstance(current, _EligibleSource):
        current = current.source
    return current


def _wait_past_configured_boundary(
    evaluated_at: datetime,
    delay: timedelta,
    *,
    now: Callable[[], datetime] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> bool:
    """Pause until ``evaluated_at`` is at least ``delay`` past the last M15 close."""

    pause = sleeper or sleep
    close = evaluated_at.replace(second=0, microsecond=0) - timedelta(
        minutes=evaluated_at.minute % 15
    )
    remaining = (close + delay) - evaluated_at
    if remaining <= timedelta(0) or remaining > delay + timedelta(minutes=15):
        return False
    pause(remaining.total_seconds())
    return True


def _wait_for_binance_rest_settlement(
    evaluated_at: datetime,
    *,
    now: Callable[[], datetime] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> bool:
    """Pause until Binance's post-close admission window has elapsed.

    The adapter rejects a candle whose close is still inside that window.
    A pause is useful only for that short interval. Any other forming-candle
    failure returns immediately so the caller can record it. ``now`` and
    ``sleeper`` default to the wall clock and ``time.sleep``.
    """

    clock = now or (lambda: datetime.now(UTC))
    pause = sleeper or sleep
    close = evaluated_at.replace(second=0, microsecond=0) - timedelta(
        minutes=evaluated_at.minute % 15
    )
    remaining = (close + BINANCE_REST_SETTLEMENT_GRACE) - clock()
    if remaining <= timedelta(0) or remaining > BINANCE_REST_SETTLEMENT_GRACE:
        return False
    pause(remaining.total_seconds())
    return True


class SymbolMarketFactory:
    """At most five independent sources/monitors; no tenant data is cached here."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self._clock = clock
        self._sleeper = sleeper
        self.discovery = WatchlistContractDiscovery(settings, transport=transport)
        self._entries: OrderedDict[str, tuple] = OrderedDict()
        self._replay = perpetual_source_is_replay(settings)
        self._reads: dict[str, int] = {}
        self._assembler_contract_book: ContractBook | None = None

    def retain(self, symbols: Sequence[str]) -> None:
        for symbol in list(self._entries):
            if symbol not in symbols:
                self._dispose(self._entries.pop(symbol))
                self._reads.pop(symbol, None)

    def provider_for(self, symbol: str) -> str | None:
        entry = self._entries.get(symbol)
        return None if entry is None else entry[0].name

    def read_count(self, symbol: str) -> int:
        return self._reads.get(symbol, 0)

    def _read(self, symbol: str) -> None:
        self._reads[symbol] = self.read_count(symbol) + 1

    @staticmethod
    def _dispose(entry: tuple) -> None:
        for source in entry[3]:
            close = getattr(source, "close", None)
            if callable(close):
                close()

    def composition(self, symbol: str) -> tuple:
        venue = venue_for_evidence_source(self.settings.perpetual_evidence_source)
        self.discovery.require(symbol, venue)
        entry = self._entries.get(symbol)
        if entry is not None:
            self._entries.move_to_end(symbol)
            return entry
        catalog = catalog_for_symbols([symbol], venue=venue)
        source = resolve_perpetual_evidence_source(
            self.settings, catalog=catalog, symbol=symbol, transport=self.transport
        )
        if isinstance(source, FailoverPerpetualSource):
            raw = [source._primary, source._secondary]
            source._primary = _EligibleSource(
                raw[0],
                lambda: self.discovery.require(symbol, VenueId.BINANCE),
                lambda: self._read(symbol),
            )
            source._secondary = _EligibleSource(
                raw[1],
                lambda: self.discovery.require(symbol, VenueId.BYBIT),
                lambda: self._read(symbol),
            )
        else:
            raw = [source]
            source = _EligibleSource(
                source, lambda: self.discovery.require(symbol, venue), lambda: self._read(symbol)
            )
        monitor = build_perpetual_market_monitor(self.settings, source=source, catalog=catalog)
        entry = (source, catalog, monitor, raw)
        self._entries[symbol] = entry
        while len(self._entries) > 5:
            old_symbol, old_entry = self._entries.popitem(last=False)
            self._dispose(old_entry)
            self._reads.pop(old_symbol, None)
        return entry

    def _contract_book_for_assembler(self) -> ContractBook:
        if self._assembler_contract_book is not None:
            return self._assembler_contract_book
        if self.discovery.book.contracts:
            self._assembler_contract_book = self.discovery.book
        else:
            self._assembler_contract_book = default_contract_book()
        return self._assembler_contract_book

    def __call__(self, session: Session | None, store: WatcherStore, symbol: str):
        source, catalog, monitor, _ = self.composition(symbol)
        lifetime = (
            SqlAlchemySetupLifetimeStore(session) if session is not None else SetupLifetimeStore()
        )
        return AssemblingWatcherScanEvidence(
            FirstSliceEvidenceAssembler(
                source,
                replay=self._replay,
                catalog=catalog,
                lifetime=lifetime,
                contract_book=self._contract_book_for_assembler(),
            ),
            session=session,
            watcher_store=store,
            symbol=symbol,
            monitor=MarketMonitorWatcherPort(monitor),
        )

    def poll_monitor(self, symbol: str) -> None:
        """Keep the live stream current even when the bar's evaluation is replayed."""

        if not self._replay:
            _, _, monitor, _ = self.composition(symbol)
            monitor.snapshot(symbol)

    def ensure_bar_close_ready(self, symbol: str) -> bool:
        """Block until the just-closed M15 bar may be evaluated. Never admits forming rows."""

        if self._replay:
            return False
        now = self._clock() if self._clock is not None else datetime.now(UTC)
        slept = False
        configured = timedelta(seconds=float(self.settings.watcher_paper_bar_close_delay_seconds))
        if configured > timedelta(0):
            slept = _wait_past_configured_boundary(
                now, configured, now=self._clock, sleeper=self._sleeper
            )
            now = self._clock() if self._clock is not None else datetime.now(UTC)
        source, _, _, _ = self.composition(symbol)
        if isinstance(_active_rest_source(source), BinanceUsdmPerpetualSource) and (
            _wait_for_binance_rest_settlement(now, now=self._clock, sleeper=self._sleeper)
        ):
            slept = True
        return slept

    def probe(self, symbol: str) -> MarketProbeResult:
        if self._replay:
            raise ContractUnavailableError("live_probe_unavailable_in_replay")
        source, catalog, _, _ = self.composition(symbol)
        # Two final M15 bars (provider request bounded to four rows). Never a
        # current-price quote, full trade/CVD window, strategy or candidate.
        now = self._clock() if self._clock is not None else datetime.now(UTC)
        # Binance withholds the just-closed candle for a few seconds. Bybit and
        # the other adapters admit that candle immediately, so they must not sleep.
        if isinstance(_active_rest_source(source), BinanceUsdmPerpetualSource) and (
            _wait_for_binance_rest_settlement(now, now=self._clock, sleeper=self._sleeper)
        ):
            now = self._clock() if self._clock is not None else datetime.now(UTC)
        for attempt in range(2):
            instrument = instrument_for_source(source, catalog, symbol)
            self.discovery.require(symbol, instrument.venue)
            identity = first_slice_identity(
                timeframe=Timeframe.M15, replay=False, is_live=True, instrument=instrument
            )
            try:
                series = source.fetch_closed_ohlcv(
                    identity=identity,
                    instrument=instrument,
                    timeframe=Timeframe.M15,
                    min_final_bars=2,
                    evaluated_at=now,
                    use_public_kline_cache=False,
                )
            except EvidenceSourceSwitchRequiredError:
                if attempt:
                    raise
                continue
            require_instrument(series.identity, instrument)
            latest = max(bar.interval_end for bar in series.bars)
            expected = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 15)
            if latest != expected:
                raise StaleEvidenceError("Probe did not return the latest fully closed candle.")
            return MarketProbeResult(
                symbol, series.identity.source.provider_name, "fresh_closed_candle", now
            )
        raise ContractUnavailableError("provider_unreachable")

    def release_symbol_history(self, symbol: str) -> None:
        entry = self._entries.get(symbol)
        if entry is None:
            return
        for source in entry[3]:
            release = getattr(source, "release_symbol_history", None)
            if callable(release):
                release(symbol)

    def memory_counts(self) -> dict[str, int]:
        """Cardinalities only, sampled by the owning Watcher thread after cleanup."""

        counts = dict.fromkeys(
            (
                "market_compositions",
                "monitor_trades",
                "monitor_ohlcv_bars",
                "raw_cache_entries",
                "raw_cache_rows",
                "reduced_cache_entries",
                "cache_locks",
                "bybit_lineages",
                "bybit_proven_prints",
                "bybit_ranks",
            ),
            0,
        )
        counts["market_compositions"] = len(self._entries)
        seen_caches: set[int] = set()
        for _, _, monitor, sources in self._entries.values():
            for runtime in monitor._runtimes.values():
                counts["monitor_trades"] += len(runtime._assembler._ordered)
                for series in (runtime._series_15m, runtime._series_4h):
                    counts["monitor_ohlcv_bars"] += len(series.bars) if series else 0
            for source in sources:
                for attr in ("_trade_cache", "_reduced_cache"):
                    cache = getattr(source, attr, None)
                    if cache is None or id(cache) in seen_caches:
                        continue
                    seen_caches.add(id(cache))
                    with cache._guard:
                        if attr == "_trade_cache":
                            counts["raw_cache_entries"] += len(cache._entries)
                            counts["raw_cache_rows"] += sum(
                                len(entry.rows) for entry in cache._entries.values()
                            )
                            counts["cache_locks"] += len(cache._locks)
                        else:
                            counts["reduced_cache_entries"] += len(cache._entries)
                lineages = getattr(source, "_lineages", {})
                counts["bybit_lineages"] += len(lineages)
                counts["bybit_proven_prints"] += sum(len(p.proven) for p in lineages.values())
                counts["bybit_ranks"] += len(getattr(source, "_rank_by_exec", {}))
        return counts
