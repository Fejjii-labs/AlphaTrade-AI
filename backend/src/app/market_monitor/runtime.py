"""Per-symbol trade-stream runtime with reconnect, backoff, and fail-closed gaps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from uuid import uuid4

import structlog

from app.evidence_pipeline.types import CurrentPriceQuote
from app.market_contracts.adapters.protocol import PerpetualMarketSource
from app.market_contracts.coverage import (
    TradeWindowCoverageProof,
    build_complete_trade_window_coverage,
    build_complete_trade_window_coverage_from_hash,
)
from app.market_contracts.cursor import TradeStreamAssembler
from app.market_contracts.cvd import accumulate_signed_quote
from app.market_contracts.enums import (
    DataCompleteness,
    Finality,
    GapState,
    ReconnectState,
    WarmUpStatus,
)
from app.market_contracts.errors import (
    CursorRecoveryError,
    DuplicateDataError,
    EvidenceSourceSwitchRequiredError,
    FormingCandleError,
    GapDetectedError,
    IncompleteWarmUpError,
    MarketContractError,
    OutOfOrderTradesError,
    RateLimitedError,
    RegionalProviderFailureError,
    SpotFallbackRejectedError,
    UnrecoverableGapError,
    UpstreamBanError,
    WrongInstrumentError,
    WrongMarketError,
    WrongSourceError,
)
from app.market_contracts.first_slice import (
    FIRST_SLICE_MIN_FINAL_4H,
    FIRST_SLICE_MIN_FINAL_15M,
    first_slice_identity,
)
from app.market_contracts.freshness import first_slice_freshness_policy
from app.market_contracts.identity import EvidenceMarketIdentity, InstrumentIdentity
from app.market_contracts.ohlcv import ClosedOhlcvSeries
from app.market_contracts.streaming_hash import TradeSetHasher, VenueIdSetHasher
from app.market_contracts.trades import OrderedTradeBatch, TradeEvent
from app.market_monitor.backoff import BackoffPolicy, delay_seconds
from app.market_monitor.identity import monitor_semantic_hash
from app.market_monitor.price import quote_from_terminal_trade
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
from app.observability.process_memory import release_allocator_memory
from app.providers.base import ProviderHealth
from app.schemas.common import Timeframe

logger = structlog.get_logger("market_monitor.runtime")

_MAX_LOOKBACK = timedelta(minutes=10)
# A catch-up after a slow Watcher cycle, a backoff or a reconnect can cover up
# to _MAX_LOOKBACK. Read as one list, that is ~3.3 KB of objects per aggTrade:
# a busy 10-minute BTC window alone could fill the 512 MiB worker (OOM,
# 2026-11-21). Read it in spans and keep only running totals.
_MAX_FETCH_SPAN = timedelta(seconds=60)


def _fetch_spans(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Split [start, end) into equal contiguous spans no longer than _MAX_FETCH_SPAN."""

    count = max(1, -(-(end - start) // _MAX_FETCH_SPAN))
    step = (end - start) / count
    bounds = [start + step * index for index in range(count)] + [end]
    return list(pairwise(bounds))


class _WindowTotals:
    """CVD totals and coverage for one polled window, fed one span at a time.

    A complete multi-span window yields the same proof, totals and hashes as
    one read of the whole window, without holding its trades.
    """

    def __init__(self, start: datetime) -> None:
        self.start = start
        self.end = start
        self.spans = 0
        self.complete = True
        self.cvd_reason: str | None = None
        self.signed = Decimal("0")
        self.total = Decimal("0")
        self.count = 0
        self._first: TradeEvent | None = None
        self._last: TradeEvent | None = None
        self._trade_set = TradeSetHasher()
        self._venue_ids = VenueIdSetHasher()
        self._last_coverage: TradeWindowCoverageProof | None = None

    def add(self, batch: OrderedTradeBatch, span_end: datetime) -> None:
        self.spans += 1
        self.end = span_end
        coverage = batch.coverage
        self._last_coverage = coverage
        if batch.trades and not (
            coverage.completeness is DataCompleteness.COMPLETE
            and coverage.gap_state is GapState.NONE
        ):
            self.complete = False
        if not self.complete or self.cvd_reason is not None or not batch.trades:
            return
        try:
            signed, total = accumulate_signed_quote(batch.trades)
        except MarketContractError as exc:
            self.cvd_reason = type(exc).__name__
            return
        self.signed += signed
        self.total += total
        self.count += len(batch.trades)
        for trade in batch.trades:
            self._trade_set.add(
                venue_trade_id=trade.venue_trade_id,
                sequence=trade.sequence,
                content_hash=trade.content_hash,
            )
            self._venue_ids.add(trade.venue_trade_id)
        self._first = self._first or batch.trades[0]
        self._last = batch.trades[-1]

    @property
    def usable(self) -> bool:
        return self.complete and self.cvd_reason is None and self.count > 0

    def event_hash(self) -> str:
        return self._venue_ids.hexdigest()

    def coverage(self) -> TradeWindowCoverageProof | None:
        last = self._last_coverage
        if last is None or self.spans == 1 or not self.complete or self.cvd_reason:
            return last
        if self._first is None or self._last is None:
            return build_complete_trade_window_coverage(
                identity=last.identity,
                lineage_id=last.lineage_id,
                requested_start=self.start,
                requested_end=self.end,
                trades=[],
            )
        return build_complete_trade_window_coverage_from_hash(
            identity=last.identity,
            lineage_id=last.lineage_id,
            requested_start=self.start,
            requested_end=self.end,
            trade_set_hash=self._trade_set.hexdigest(),
            first_trade_id=self._first.venue_trade_id,
            last_trade_id=self._last.venue_trade_id,
            first_sequence=self._first.sequence,
            last_sequence=self._last.sequence,
        )


class SymbolMonitorRuntime:
    """One catalog symbol. New process = new connection epoch."""

    def __init__(
        self,
        *,
        instrument: InstrumentIdentity,
        source: PerpetualMarketSource,
        replay: bool,
        backoff: BackoffPolicy,
        connected_at: datetime,
    ) -> None:
        self.instrument = instrument
        self._source = source
        self._replay = replay
        self._backoff_policy = backoff
        self.identity = first_slice_identity(
            timeframe=Timeframe.M15,
            replay=replay,
            is_live=not replay,
            instrument=instrument,
        )
        self._identity_4h = first_slice_identity(
            timeframe=Timeframe.H4,
            replay=replay,
            is_live=not replay,
            instrument=instrument,
        )
        self._assembler = TradeStreamAssembler(
            self.identity,
            connected_at=connected_at,
            connection_identity=uuid4(),
        )
        self._watermark_time: datetime | None = None
        self._watermark_sequence: int | None = None
        self._window_coverage: TradeWindowCoverageProof | None = None
        self._window_signed: Decimal | None = None
        self._window_total: Decimal | None = None
        self._window_count = 0
        self._window_event_hash: str | None = None
        self._window_cvd_reason: str | None = None
        self._series_15m: ClosedOhlcvSeries | None = None
        self._series_4h: ClosedOhlcvSeries | None = None
        self._reason = MonitorReason.WARM_UP
        self._backoff_until: datetime | None = None
        self._backoff_attempt = 0
        self._last_error_class: str | None = None
        self._ohlcv_reason: str | None = None
        self._provider_outage_open = False

    @property
    def replay(self) -> bool:
        return self._replay

    def tick(self, now: datetime) -> SymbolMonitorSnapshot:
        evaluated = now.astimezone(UTC)
        recovered = self._maybe_recover_primary(evaluated)
        if recovered is not None:
            self._rebind(recovered, evaluated)
        if self._in_backoff(evaluated):
            self._reason = MonitorReason.BACKOFF
            return self._project(evaluated)
        try:
            self._fetch_and_ingest(evaluated)
            self._refresh_ohlcv(evaluated)
            self._clear_backoff()
        except EvidenceSourceSwitchRequiredError as exc:
            switched = exc.instrument
            if not isinstance(switched, InstrumentIdentity):
                self._last_error_class = type(exc).__name__
                self._reason = MonitorReason.WRONG_SOURCE
                return self._project(evaluated)
            self._rebind(switched, evaluated)
            try:
                self._fetch_and_ingest(evaluated)
                self._refresh_ohlcv(evaluated)
                self._clear_backoff()
            except MarketContractError as retry_exc:
                self._last_error_class = type(retry_exc).__name__
                self._reason = MonitorReason.PROVIDER_ERROR
        except RateLimitedError as exc:
            self._last_error_class = type(exc).__name__
            self._apply_backoff(
                evaluated,
                retry_after_seconds=exc.retry_after_seconds,
                honor_full_retry_after=isinstance(exc, UpstreamBanError),
            )
            self._reason = MonitorReason.RATE_LIMITED
        except RegionalProviderFailureError as exc:
            self._last_error_class = type(exc).__name__
            self._provider_outage_open = True
            self._begin_reconnect(evaluated)
            self._apply_backoff(evaluated)
            self._reason = MonitorReason.PROVIDER_UNAVAILABLE
        except SpotFallbackRejectedError as exc:
            self._last_error_class = type(exc).__name__
            self._reason = MonitorReason.SPOT_REJECTED
        except WrongInstrumentError as exc:
            self._last_error_class = type(exc).__name__
            self._reason = MonitorReason.SYMBOL_MISMATCH
        except WrongSourceError as exc:
            self._last_error_class = type(exc).__name__
            self._reason = MonitorReason.WRONG_SOURCE
        except OutOfOrderTradesError as exc:
            self._last_error_class = type(exc).__name__
            self._reason = MonitorReason.OUT_OF_ORDER
        except DuplicateDataError as exc:
            self._last_error_class = type(exc).__name__
            self._reason = MonitorReason.DUPLICATE_CONFLICT
        except UnrecoverableGapError as exc:
            self._last_error_class = type(exc).__name__
            self._schedule_recovery(evaluated)
            self._reason = MonitorReason.UNRECOVERABLE_GAP
        except (GapDetectedError, CursorRecoveryError, IncompleteWarmUpError) as exc:
            self._last_error_class = type(exc).__name__
            if self._assembler.cursor.reconnect_state is not ReconnectState.RECONNECTING:
                self._begin_reconnect(evaluated)
            self._apply_backoff(evaluated)
            self._reason = (
                MonitorReason.UNRECOVERABLE_GAP
                if self._assembler.cursor.gap_state is GapState.UNRECOVERABLE
                else MonitorReason.GAP
            )
        except MarketContractError as exc:
            self._last_error_class = type(exc).__name__
            self._reason = MonitorReason.PROVIDER_ERROR
        return self._project(evaluated)

    def project(self, now: datetime) -> SymbolMonitorSnapshot:
        """Re-evaluate freshness without a provider fetch."""
        return self._project(now.astimezone(UTC))

    def _maybe_recover_primary(self, now: datetime) -> InstrumentIdentity | None:
        del now
        recover = getattr(self._source, "try_recover_primary", None)
        if not callable(recover):
            return None
        instrument = recover()
        if isinstance(instrument, InstrumentIdentity):
            return instrument
        return None

    def _rebind(self, instrument: InstrumentIdentity, now: datetime) -> None:
        """Start a new connection epoch on the switched venue. Drop the previous book."""

        self.instrument = instrument
        self.identity = first_slice_identity(
            timeframe=Timeframe.M15,
            replay=self._replay,
            is_live=not self._replay,
            instrument=instrument,
        )
        self._identity_4h = first_slice_identity(
            timeframe=Timeframe.H4,
            replay=self._replay,
            is_live=not self._replay,
            instrument=instrument,
        )
        self._assembler = TradeStreamAssembler(
            self.identity,
            connected_at=now,
            connection_identity=uuid4(),
        )
        self._watermark_time = None
        self._watermark_sequence = None
        self._clear_window_memory()
        self._series_15m = None
        self._series_4h = None
        self._reason = MonitorReason.WARM_UP

    def _fetch_and_ingest(self, now: datetime) -> None:
        if self._assembler.cursor.gap_state is GapState.UNRECOVERABLE:
            self._assembler.release_unrecoverable_for_retry()
        start, end = self._window(now)
        spans = _fetch_spans(start, end)
        totals = _WindowTotals(start)
        try:
            for index, (span_start, span_end) in enumerate(spans):
                batch = self._source.fetch_ordered_trades(
                    identity=self.identity,
                    instrument=self.instrument,
                    start=span_start,
                    end=span_end,
                    source_connection_id=self._assembler.connection_identity,
                    receive_at=now,
                )
                self._assert_batch_identity(batch)
                totals.add(batch, span_end)
                try:
                    self._ingest_span(batch.trades, now, last=index == len(spans) - 1)
                finally:
                    self._release_book()
                del batch
        finally:
            if totals.spans:
                self._remember_window(totals)
            if len(spans) > 1:
                memory = release_allocator_memory()
                logger.info(
                    "market_monitor_catch_up",
                    symbol=self.instrument.provider_symbol,
                    window_seconds=round((end - start).total_seconds(), 1),
                    spans=len(spans),
                    trades=totals.count,
                    rss_bytes=memory.rss_bytes,
                )

    def _ingest_span(self, trades: list[TradeEvent], now: datetime, *, last: bool) -> None:
        if self._assembler.cursor.reconnect_state is ReconnectState.RECONNECTING:
            watermark = self._watermark_sequence
            resumed = (
                any(trade.sequence > watermark for trade in trades)
                if (watermark is not None)
                else bool(trades)
            )
            if not resumed and not last:
                # Nothing past the watermark yet; a later span carries the backfill.
                return
            self._assembler.recover_from_backfill(trades, observed_at=now)
            self._reason = MonitorReason.OK if trades else MonitorReason.INCOMPLETE
            return
        if not trades:
            if self._assembler.accepted_trades():
                self._assembler.ingest([], observed_at=now, allow_empty=True)
                self._reason = MonitorReason.OK
            else:
                self._reason = MonitorReason.INCOMPLETE
            return
        self._assembler.ingest(trades, observed_at=now)
        self._reason = MonitorReason.OK

    def _refresh_ohlcv(self, now: datetime) -> None:
        self._ohlcv_reason = None
        self._series_15m = self._fetch_ohlcv(self.identity, Timeframe.M15, now)
        self._series_4h = self._fetch_ohlcv(self._identity_4h, Timeframe.H4, now)

    def _fetch_ohlcv(
        self,
        identity: EvidenceMarketIdentity,
        timeframe: Timeframe,
        now: datetime,
    ) -> ClosedOhlcvSeries | None:
        wanted = (
            FIRST_SLICE_MIN_FINAL_15M if timeframe is Timeframe.M15 else FIRST_SLICE_MIN_FINAL_4H
        )
        try:
            return self._source.fetch_closed_ohlcv(
                identity=identity,
                instrument=self.instrument,
                timeframe=timeframe,
                min_final_bars=wanted,
                evaluated_at=now,
            )
        except FormingCandleError:
            try:
                series = self._source.fetch_closed_ohlcv(
                    identity=identity,
                    instrument=self.instrument,
                    timeframe=timeframe,
                    min_final_bars=1,
                    evaluated_at=now,
                )
                self._ohlcv_reason = "partial"
                return series
            except MarketContractError as exc:
                self._ohlcv_reason = type(exc).__name__
                return None
        except MarketContractError as exc:
            self._ohlcv_reason = type(exc).__name__
            return None

    def _window(self, now: datetime) -> tuple[datetime, datetime]:
        freshness = timedelta(seconds=first_slice_freshness_policy().trade_max_age_seconds)
        cursor = self._assembler.cursor
        if cursor.reconnect_state is ReconnectState.RECONNECTING:
            start = self._watermark_time or (now - freshness)
        elif cursor.last_event_at is not None:
            start = cursor.last_event_at
        else:
            start = now - freshness
        if now - start > _MAX_LOOKBACK:
            start = now - _MAX_LOOKBACK
        if now <= start:
            start = now - freshness
        return start, now

    def _assert_batch_identity(self, batch: OrderedTradeBatch) -> None:
        if batch.identity != self.identity:
            raise WrongMarketError("Trade batch identity does not match the monitor identity.")
        if batch.identity.instrument.instrument_id != self.instrument.instrument_id:
            raise WrongInstrumentError("Trade batch instrument does not match the catalog symbol.")
        if batch.source_connection_id != self._assembler.connection_identity:
            raise WrongSourceError(
                "Trade batch lineage does not match the current connection epoch."
            )

    def _begin_reconnect(self, now: datetime) -> None:
        if self._assembler.cursor.gap_state is GapState.UNRECOVERABLE:
            return
        if self._assembler.cursor.reconnect_state is ReconnectState.RECONNECTING:
            return
        self._watermark_time = self._assembler.cursor.last_event_at
        self._watermark_sequence = self._assembler.cursor.last_sequence
        self._assembler.begin_reconnect(observed_at=now)
        self._assembler.drop_retained_trades()
        self._clear_window_memory()

    def _schedule_recovery(self, now: datetime) -> None:
        """Backoff a failed recovery without opening another connection epoch."""

        if self._assembler.cursor.reconnect_state is not ReconnectState.RECONNECTING:
            self._begin_reconnect(now)
        self._apply_backoff(now)

    def _apply_backoff(
        self,
        now: datetime,
        *,
        retry_after_seconds: float | None = None,
        honor_full_retry_after: bool = False,
    ) -> None:
        delay = delay_seconds(
            self._backoff_attempt,
            self._backoff_policy,
            retry_after_seconds=retry_after_seconds,
            honor_full_retry_after=honor_full_retry_after,
        )
        self._backoff_until = now + timedelta(seconds=delay)
        self._backoff_attempt += 1

    def _clear_backoff(self) -> None:
        self._backoff_until = None
        self._backoff_attempt = 0
        self._last_error_class = None
        self._provider_outage_open = False

    def _in_backoff(self, now: datetime) -> bool:
        return self._backoff_until is not None and now < self._backoff_until

    def _project(self, now: datetime) -> SymbolMonitorSnapshot:
        cursor = self._assembler.cursor
        terminal = self._assembler.terminal_trade()
        stream_healthy = (
            cursor.gap_state is GapState.NONE
            and cursor.warm_up_status is WarmUpStatus.COMPLETE
            and cursor.reconnect_state in {ReconnectState.CONTINUOUS, ReconnectState.RECOVERED}
        )
        suppress_quote = (
            self._reason
            in {
                MonitorReason.PROVIDER_UNAVAILABLE,
                MonitorReason.PROVIDER_ERROR,
                MonitorReason.DUPLICATE_CONFLICT,
                MonitorReason.OUT_OF_ORDER,
                MonitorReason.SYMBOL_MISMATCH,
                MonitorReason.WRONG_SOURCE,
                MonitorReason.SPOT_REJECTED,
                MonitorReason.UNRECOVERABLE_GAP,
                MonitorReason.GAP,
            }
            or cursor.reconnect_state is ReconnectState.RECONNECTING
        )
        quote = (
            None
            if terminal is None or suppress_quote
            else quote_from_terminal_trade(
                terminal,
                identity=self.identity,
                instrument=self.instrument,
                evaluated_at=now,
                replay=self._replay,
                reconnect_state=cursor.reconnect_state,
                stream_healthy=stream_healthy,
            )
        )
        availability, reason = self._availability(now, quote)
        coverage = self._coverage()
        snapshot = SymbolMonitorSnapshot(
            symbol=self.instrument.provider_symbol,
            mode=MarketMode.REPLAY if self._replay else MarketMode.LIVE_PERPETUAL,
            availability=availability,
            reason=reason,
            current_price=quote,
            last_update=cursor.last_event_at or cursor.updated_at,
            evaluated_at=now,
            instrument_id=self.instrument.instrument_id,
            provider_symbol=self.instrument.provider_symbol,
            source_family=self.identity.source.family,
            provider_name=self.identity.provenance.provider_name,
            is_live=self.identity.provenance.is_live,
            is_mock=self.identity.provenance.is_mock,
            fallback_used=False,
            perpetual=True,
            watcher_activated=False,
            live_executable=False,
            stream=StreamHealth(
                reconnect_state=cursor.reconnect_state,
                gap_state=cursor.gap_state,
                warm_up_status=cursor.warm_up_status,
                last_sequence=cursor.last_sequence,
                last_event_id=cursor.last_event_id,
                last_event_at=cursor.last_event_at,
                reconnect_count=cursor.reconnect_count,
                connection_identity=cursor.connection_identity,
            ),
            coverage=coverage,
            cvd=self._cvd(stream_healthy),
            ohlcv=self._ohlcv_health(),
            provider=self._provider_health(),
            backoff=BackoffReport(
                active=self._in_backoff(now),
                attempt=self._backoff_attempt,
                next_retry_at=self._backoff_until,
                last_error_class=self._last_error_class,
            ),
            content_hash="0" * 64,
        )
        return snapshot.model_copy(update={"content_hash": monitor_semantic_hash(snapshot)})

    def _availability(
        self, now: datetime, quote: CurrentPriceQuote | None
    ) -> tuple[MarketAvailability, MonitorReason]:
        del now
        cursor = self._assembler.cursor
        if self._replay:
            return MarketAvailability.REPLAY, MonitorReason.REPLAY_FIXTURE
        if self._reason is MonitorReason.SPOT_REJECTED:
            return MarketAvailability.UNAVAILABLE, self._reason
        if self._reason is MonitorReason.SYMBOL_MISMATCH:
            return MarketAvailability.UNAVAILABLE, self._reason
        if self._reason is MonitorReason.WRONG_SOURCE:
            return MarketAvailability.UNAVAILABLE, self._reason
        if cursor.gap_state is GapState.UNRECOVERABLE:
            return MarketAvailability.UNAVAILABLE, MonitorReason.UNRECOVERABLE_GAP
        if self._provider_outage_open and cursor.reconnect_state is ReconnectState.RECONNECTING:
            if self._reason is MonitorReason.BACKOFF:
                return MarketAvailability.DEGRADED, MonitorReason.PROVIDER_UNAVAILABLE
            return MarketAvailability.UNAVAILABLE, MonitorReason.PROVIDER_UNAVAILABLE
        if self._reason is MonitorReason.PROVIDER_UNAVAILABLE:
            return MarketAvailability.UNAVAILABLE, self._reason
        if self._reason is MonitorReason.DUPLICATE_CONFLICT:
            return MarketAvailability.UNAVAILABLE, self._reason
        if self._reason is MonitorReason.OUT_OF_ORDER:
            return MarketAvailability.UNAVAILABLE, self._reason
        if self._reason is MonitorReason.PROVIDER_ERROR:
            return MarketAvailability.UNAVAILABLE, self._reason
        if cursor.reconnect_state is ReconnectState.RECONNECTING or self._reason in {
            MonitorReason.RATE_LIMITED,
            MonitorReason.BACKOFF,
            MonitorReason.GAP,
            MonitorReason.RECONNECTING,
        }:
            reason = self._reason
            if self._reason is MonitorReason.BACKOFF:
                reason = (
                    MonitorReason.RATE_LIMITED
                    if self._last_error_class in {"RateLimitedError", "UpstreamBanError"}
                    else MonitorReason.RECONNECTING
                )
            elif self._reason is MonitorReason.OK:
                reason = MonitorReason.RECONNECTING
            return MarketAvailability.DEGRADED, reason
        if quote is None:
            if self._reason is MonitorReason.INCOMPLETE:
                return MarketAvailability.UNAVAILABLE, MonitorReason.INCOMPLETE
            return MarketAvailability.STALE, MonitorReason.STALE_STREAM
        if quote.usable_as_current_market_price:
            return MarketAvailability.FRESH, MonitorReason.OK
        if quote.presentation.value == "stale":
            return MarketAvailability.STALE, MonitorReason.STALE_STREAM
        return MarketAvailability.DEGRADED, self._reason

    def _remember_window(self, totals: _WindowTotals) -> None:
        """Keep the coverage proof and CVD totals. Do not keep the trade list."""

        self._window_coverage = totals.coverage()
        self._window_cvd_reason = totals.cvd_reason
        if not totals.usable:
            self._window_signed = None
            self._window_total = None
            self._window_count = 0
            self._window_event_hash = None
            return
        self._window_signed = totals.signed
        self._window_total = totals.total
        self._window_count = totals.count
        self._window_event_hash = totals.event_hash()

    def _clear_window_memory(self) -> None:
        self._window_coverage = None
        self._window_signed = None
        self._window_total = None
        self._window_count = 0
        self._window_event_hash = None
        self._window_cvd_reason = None

    def _release_book(self) -> None:
        last_event_at = self._assembler.cursor.last_event_at
        if last_event_at is not None:
            self._assembler.forget_trades_before(last_event_at)

    def _coverage(self) -> CoverageReport:
        proof = self._window_coverage
        if proof is None:
            return CoverageReport(
                completeness=DataCompleteness.UNKNOWN,
                gap_state=self._assembler.cursor.gap_state,
            )
        return CoverageReport(
            completeness=proof.completeness,
            gap_state=proof.gap_state,
            content_hash=proof.content_hash,
            first_trade_id=proof.first_trade_id,
            last_trade_id=proof.last_trade_id,
        )

    def _cvd(self, stream_healthy: bool) -> StreamCvdReport:
        signed = self._window_signed
        total = self._window_total
        event_hash = self._window_event_hash
        if not stream_healthy or signed is None or total is None or event_hash is None:
            if stream_healthy and self._window_cvd_reason is not None:
                return StreamCvdReport(available=False, reason=self._window_cvd_reason)
            return StreamCvdReport(available=False, reason="incomplete_or_unhealthy_stream")
        ratio = None if total == 0 else str(signed / total)
        return StreamCvdReport(
            available=True,
            signed_quote_delta=str(signed),
            total_quote_volume=str(total),
            signed_flow_ratio=ratio,
            event_count=self._window_count,
            event_set_hash=event_hash,
        )

    def _ohlcv_health(self) -> OhlcvHealth:
        series_15 = self._series_15m
        series_4h = self._series_4h
        complete_15 = (
            DataCompleteness.COMPLETE
            if series_15 is not None and len(series_15.bars) >= FIRST_SLICE_MIN_FINAL_15M
            else DataCompleteness.PARTIAL
            if series_15 is not None
            else DataCompleteness.UNKNOWN
        )
        complete_4h = (
            DataCompleteness.COMPLETE
            if series_4h is not None and len(series_4h.bars) >= FIRST_SLICE_MIN_FINAL_4H
            else DataCompleteness.PARTIAL
            if series_4h is not None
            else DataCompleteness.UNKNOWN
        )
        latest = series_15.bars[-1] if series_15 is not None and series_15.bars else None
        latest_final = (
            None
            if latest is None
            else latest.finality is Finality.FINAL and latest.provider_complete
        )
        return OhlcvHealth(
            available=series_15 is not None or series_4h is not None,
            completeness_15m=complete_15,
            completeness_4h=complete_4h,
            latest_15m_close=str(latest.close) if latest is not None else None,
            latest_15m_end=latest.interval_end if latest is not None else None,
            latest_15m_final=latest_final,
            series_15m_hash=series_15.content_hash if series_15 is not None else None,
            series_4h_hash=series_4h.content_hash if series_4h is not None else None,
            reason=self._ohlcv_reason,
        )

    def _provider_health(self) -> ProviderHealthReport:
        try:
            status = self._source.status()
        except Exception:  # status probes must never crash the monitor
            return ProviderHealthReport(
                name=self._source.name,
                health=ProviderHealth.UNAVAILABLE,
                is_mock=self._replay,
                using_fallback=False,
                detail="Provider status probe failed closed.",
            )
        if status.using_fallback:
            return ProviderHealthReport(
                name=status.name,
                health=ProviderHealth.UNAVAILABLE,
                is_mock=status.is_mock,
                using_fallback=False,
                detail="Provider reported a fallback; monitor refuses fabricated substitutes.",
            )
        return ProviderHealthReport(
            name=status.name,
            health=status.health,
            is_mock=status.is_mock,
            using_fallback=False,
            detail=status.detail,
        )
