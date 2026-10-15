"""Bybit public USDT linear perpetual adapter (read-only, no spot, no fabricated fills)."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx

from app.market_contracts.adapters.aggtrade_cache import TtlValueCache
from app.market_contracts.adapters.book_sequence import BookSequenceGuard
from app.market_contracts.adapters.http import ReadOnlyHttpGetClient
from app.market_contracts.adapters.observation_cache import CausalObservationCache
from app.market_contracts.adapters.request_budget import record_cache_hit
from app.market_contracts.coverage import build_complete_trade_window_coverage
from app.market_contracts.cursor import TradeStreamSnapshot
from app.market_contracts.derivatives import (
    DerivativeMetric,
    DerivativeObservation,
    derivative_observation,
    refresh_derivative_observation,
)
from app.market_contracts.enums import MarketType, ProductFamily, SourceFamily, VenueId
from app.market_contracts.errors import (
    DuplicateDataError,
    FormingCandleError,
    GapDetectedError,
    IncompleteTradeWindowError,
    RateLimitedError,
    RegionalProviderFailureError,
    SpotFallbackRejectedError,
    UnsupportedTradeContractError,
    WrongInstrumentError,
    WrongMarketError,
    WrongSourceError,
)
from app.market_contracts.freshness import first_slice_freshness_policy
from app.market_contracts.hashing import semantic_content_hash, with_content_hash
from app.market_contracts.identity import (
    BYBIT_ADAPTER_VERSION,
    BYBIT_AGGRESSOR_CONVENTION,
    EvidenceMarketIdentity,
    InstrumentIdentity,
    bybit_usdt_perpetual,
    bybit_usdt_perpetual_btcusdt,
    require_instrument,
    require_perpetual,
)
from app.market_contracts.ohlcv import (
    ClosedOhlcvSeries,
    OhlcvBar,
    build_ohlcv_bar,
    require_closed_series,
)
from app.market_contracts.order_book import (
    BOOK_DEPTH,
    OrderBookObservation,
    order_book_observation,
    refresh_order_book,
)
from app.market_contracts.order_flow import require_order_flow_request
from app.market_contracts.provider_contracts import contract_from_bybit_instruments
from app.market_contracts.trade_reduction import build_released_trade_snapshot
from app.market_contracts.trades import (
    OrderedTradeBatch,
    TradeEvent,
    build_trade_event,
    order_trades,
)
from app.providers.base import ProviderHealth, ProviderKind, ProviderStatus
from app.schemas.common import Timeframe
from app.schemas.nested_continuation import EvidenceAvailability

BYBIT_SYMBOL = "BTCUSDT"
BYBIT_CATEGORY = "linear"
BYBIT_ALLOWED_PATHS = frozenset(
    {
        "/v5/market/time",
        "/v5/market/kline",
        "/v5/market/recent-trade",
        "/v5/market/instruments-info",
        "/v5/market/open-interest",
        "/v5/market/funding/history",
        "/v5/market/orderbook",
    }
)
BYBIT_ALLOWED_HOSTS = frozenset({"api.bybit.com"})
# Native linear kline intervals only. Bybit has no 3d interval; never substitute
# a different timeframe or aggregate uncontracted evidence for that request.
_BYBIT_INTERVAL = {
    Timeframe.M1: "1",
    Timeframe.M3: "3",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.M30: "30",
    Timeframe.H1: "60",
    Timeframe.H2: "120",
    Timeframe.H4: "240",
    Timeframe.H6: "360",
    Timeframe.H12: "720",
    Timeframe.D1: "D",
    Timeframe.W1: "W",
}
_RECENT_TRADE_LIMIT = 1000
_PROVEN_TAIL_RETENTION = timedelta(minutes=15)
# Three bounded consumers: monitor, canonical setup, and five-minute intelligence.
_MAX_LINEAGE_PROOFS = 3
# A finite ten-page proof budget, also limited by the existing 15-minute horizon.
# Evicted prefixes become unavailable; they are never reported as complete.
_MAX_PROVEN_TRADES = 10 * _RECENT_TRADE_LIMIT


@dataclass
class _LineageProof:
    proven: list[dict[str, Any]] = field(default_factory=list)
    proven_from_ms: int | None = None
    anchor_exec_id: str | None = None
    initial_oldest_ms: int | None = None


class BybitUsdtPerpetualSource:
    """Public Bybit linear USDT perpetual. Spot and inverse books are rejected.

    The default instrument is BTCUSDT. One instance serves one symbol so the
    proven trade tail is never reused for a different market.
    """

    name = "bybit-usdt-perpetual"
    kind = ProviderKind.MARKET_DATA

    def __init__(
        self,
        *,
        base_url: str = "https://api.bybit.com",
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        client: ReadOnlyHttpGetClient | None = None,
        max_retries: int = 3,
        max_backoff_seconds: float = 30.0,
        instrument: InstrumentIdentity | None = None,
        observation_cache: CausalObservationCache | None = None,
        observation_clock: Callable[[], datetime] | None = None,
        reuse_recent_pages: bool = False,
    ) -> None:
        if instrument is None:
            self._instrument = bybit_usdt_perpetual_btcusdt()
        else:
            expected = bybit_usdt_perpetual(instrument.provider_symbol)
            if instrument.instrument_id != expected.instrument_id:
                raise WrongInstrumentError(
                    "Bybit adapter requires the contracted Bybit linear USDT perpetual."
                )
            self._instrument = expected
        self._symbol = self._instrument.provider_symbol
        self._http = client or ReadOnlyHttpGetClient(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
            allowed_hosts=BYBIT_ALLOWED_HOSTS,
            allowed_paths=BYBIT_ALLOWED_PATHS,
            failure_label="Bybit USDT perpetual",
            max_retries=max_retries,
            max_backoff_seconds=max_backoff_seconds,
            weight_per_minute=240,
        )
        self._last_success_at: datetime | None = None
        self._last_error: str | None = None
        # Execution rank belongs to this exact source/symbol, not to a caller's
        # retrieval lineage. A historical read must not recycle a live cursor's
        # rank. Proofs remain lineage-scoped and bounded independently.
        self._rank_connection: UUID | None = None
        self._rank_by_exec: dict[str, int] = {}
        self._signature_by_exec: dict[str, tuple[object, ...]] = {}
        self._next_rank = 1
        self._recent_exec_ids: set[str] = set()
        self._lineages: OrderedDict[UUID, _LineageProof] = OrderedDict()
        self._observation_cache = observation_cache or CausalObservationCache()
        self._observation_clock = observation_clock
        self._book_sequences: dict[str, BookSequenceGuard] = {}
        self._book_snapshot_hashes: dict[str, str] = {}
        self._recent_print_cache = TtlValueCache(max_entries=1, ttl_seconds=1)
        self._recent_print_guard = threading.Lock()
        self._reuse_recent_pages = reuse_recent_pages

    def active_instrument(self) -> InstrumentIdentity:
        return self._instrument

    def fetch_derivative_observation(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        metric: DerivativeMetric,
        observed_at: datetime,
    ) -> DerivativeObservation:
        self._assert_request(identity, instrument)
        item = self._observation_cache.collect(
            (
                semantic_content_hash(identity.model_copy(update={"timeframe": None})),
                self._symbol,
                metric.value,
                "derivatives/v2",
            ),
            observed_at,
            lambda: self._collect_derivative_observation(
                identity=identity, instrument=instrument, metric=metric, observed_at=observed_at
            ),
        )
        return refresh_derivative_observation(item, max(observed_at, item.observed_at))

    def fetch_order_book_observation(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        observed_at: datetime,
    ) -> OrderBookObservation:
        self._assert_request(identity, instrument)
        item = self._observation_cache.collect(
            (
                semantic_content_hash(identity.model_copy(update={"timeframe": None})),
                self._symbol,
                "order_book",
                "depth20/v1",
            ),
            observed_at,
            lambda: self._collect_order_book(identity, instrument, observed_at),
        )
        if (
            item.update_id is not None
            and self._book_snapshot_hashes.get(instrument.provider_symbol) != item.content_hash
        ):
            self._book_sequences.setdefault(
                instrument.provider_symbol, BookSequenceGuard(identity.venue)
            ).snapshot(item.update_id)
            self._book_snapshot_hashes[instrument.provider_symbol] = item.content_hash
        return refresh_order_book(item, max(observed_at, item.observed_at))

    def _collect_order_book(
        self,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        observed_at: datetime,
    ) -> OrderBookObservation:
        params: dict[str, str | int] = {"category": BYBIT_CATEGORY, "symbol": self._symbol}
        contract = self._public_result("/v5/market/instruments-info", params)
        self._assert_linear(contract)
        contract_from_bybit_instruments({"result": contract}, requested_symbol=self._symbol)
        params["limit"] = BOOK_DEPTH
        result = self._public_result("/v5/market/orderbook", params)
        # The documented orderbook result has s/b/a/cts, without a category field.
        observed_at = self._observation_clock() if self._observation_clock else observed_at
        return order_book_observation(identity=identity, observed_at=observed_at, row=result)

    def invalidate_order_book(self, symbol: str) -> None:
        self._observation_cache.drop_symbol(symbol)
        self._book_snapshot_hashes.pop(symbol, None)

    def observe_order_book_delta(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        row: Mapping[str, Any],
        observed_at: datetime,
    ) -> OrderBookObservation:
        """Invalidate truncated snapshots on changes/gaps; next GET resynchronizes.

        This callback does not open a socket or claim a continuous local book.
        Absolute delta quantities (including deletions) require a fresh snapshot.
        """
        self._assert_request(identity, instrument)
        guard = self._book_sequences.setdefault(
            instrument.provider_symbol, BookSequenceGuard(identity.venue)
        )
        reason = "depth_changed_snapshot_required"
        try:
            if row.get("s") != instrument.provider_symbol:
                raise GapDetectedError("wrong_symbol")
            if not guard.delta(row):
                return self.fetch_order_book_observation(
                    identity=identity, instrument=instrument, observed_at=observed_at
                )
        except GapDetectedError as exc:
            guard.disconnect()
            reason = str(exc)
        self.invalidate_order_book(instrument.provider_symbol)
        return order_book_observation(
            identity=identity,
            observed_at=observed_at,
            availability=EvidenceAvailability.INCOMPLETE,
            sequence_status="resync_required",
            reason=reason,
        )

    def _collect_derivative_observation(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        metric: DerivativeMetric,
        observed_at: datetime,
    ) -> DerivativeObservation:
        self._assert_request(identity, instrument)
        params: dict[str, str | int] = {"category": BYBIT_CATEGORY, "symbol": self._symbol}
        contract = self._public_result("/v5/market/instruments-info", params)
        self._assert_linear(contract)
        rows = contract.get("list")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise WrongSourceError("Bybit instruments-info list is malformed.")
        required_fields = {"symbol", "contractType", "quoteCoin", "baseCoin", "status"}
        for row in rows:
            if row.get("symbol") == self._symbol and not required_fields <= row.keys():
                raise WrongSourceError("Bybit instruments-info contract fields are incomplete.")
        try:
            contract_from_bybit_instruments({"result": contract}, requested_symbol=self._symbol)
        except (WrongInstrumentError, WrongMarketError):
            return derivative_observation(
                identity=identity,
                metric=metric,
                observed_at=observed_at,
                availability=EvidenceAvailability.UNSUPPORTED,
                reason="provider_contract_not_verified",
            )
        params.update({"limit": 1, "endTime": int(observed_at.timestamp() * 1000)})
        if metric is DerivativeMetric.OPEN_INTEREST:
            params["intervalTime"] = "5min"
            path, value_key, time_key = "/v5/market/open-interest", "openInterest", "timestamp"
        else:
            path, value_key, time_key = (
                "/v5/market/funding/history",
                "fundingRate",
                "fundingRateTimestamp",
            )
        result = self._public_result(path, params)
        self._assert_linear(result)
        rows = result.get("list")
        row = rows[0] if isinstance(rows, list) and len(rows) == 1 else None
        malformed = (
            not isinstance(rows, list)
            or len(rows) > 1
            or (bool(rows) and not isinstance(row, dict))
        )
        if metric is DerivativeMetric.OPEN_INTEREST and result.get("symbol") != self._symbol:
            malformed = True
        if metric is DerivativeMetric.FUNDING and isinstance(row, dict):
            malformed = malformed or row.get("symbol") != self._symbol
        observed_at = self._observation_clock() if self._observation_clock else observed_at
        return derivative_observation(
            identity=identity,
            metric=metric,
            observed_at=observed_at,
            row=row if isinstance(row, dict) else None,
            value_key=value_key,
            time_key=time_key,
            availability=EvidenceAvailability.INCOMPLETE if malformed else None,
            reason="malformed_provider_payload" if malformed else None,
        )

    def fetch_closed_ohlcv(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        timeframe: Timeframe,
        min_final_bars: int,
        evaluated_at: datetime,
    ) -> ClosedOhlcvSeries:
        self._assert_request(identity, instrument)
        interval = _BYBIT_INTERVAL.get(timeframe)
        if interval is None:
            raise WrongMarketError(
                f"Timeframe {timeframe.value} is not contracted for Bybit USDT perpetual evidence."
            )
        limit = min(max(min_final_bars + 2, min_final_bars), 1000)
        result = self._public_result(
            "/v5/market/kline",
            {
                "category": BYBIT_CATEGORY,
                "symbol": self._symbol,
                "interval": interval,
                "limit": limit,
            },
        )
        self._assert_linear(result)
        rows = result.get("list")
        if not isinstance(rows, list):
            raise WrongMarketError("Bybit kline payload is not a list.")
        grace = timedelta(seconds=first_slice_freshness_policy().ohlcv_post_close_grace_seconds)
        bars = [
            self._parse_kline(
                row,
                instrument=instrument,
                timeframe=timeframe,
                evaluated_at=evaluated_at,
                grace=grace,
            )
            for row in rows
        ]
        bars.sort(key=lambda item: item.interval_start)
        final_bars = [item for item in bars if item.finality.value == "final"]
        if len(final_bars) < min_final_bars:
            raise FormingCandleError(
                f"Need {min_final_bars} final {timeframe.value} candles; "
                f"received {len(final_bars)} final and {len(bars) - len(final_bars)} non-final."
            )
        return require_closed_series(
            final_bars,
            identity=identity,
            timeframe=timeframe,
            evaluated_at=evaluated_at,
            min_bars=min_final_bars,
        )

    def fetch_ordered_trades(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        start: datetime,
        end: datetime,
        source_connection_id: UUID,
        receive_at: datetime,
    ) -> OrderedTradeBatch:
        self._assert_request(identity, instrument)
        if end <= start:
            raise WrongMarketError("Trade window end must be after start.")
        rows = self._recent_trades_covering(start, end, source_connection_id, receive_at)
        raw_trades = [
            self._parse_trade(
                row,
                instrument=instrument,
                source_connection_id=source_connection_id,
                receive_at=receive_at,
            )
            for row in rows
        ]
        ordered = order_trades(raw_trades)
        _require_contiguous_sequences(ordered)
        coverage = build_complete_trade_window_coverage(
            identity=identity,
            lineage_id=source_connection_id,
            requested_start=start,
            requested_end=end,
            trades=ordered,
        )
        batch = OrderedTradeBatch(
            identity=identity,
            trades=ordered,
            source_connection_id=source_connection_id,
            coverage=coverage,
            content_hash="0" * 64,
        )
        return with_content_hash(batch)

    def fetch_order_flow_snapshot(
        self,
        *,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
        start: datetime,
        end: datetime,
        source_connection_id: UUID,
        receive_at: datetime,
    ) -> TradeStreamSnapshot:
        require_order_flow_request(
            identity=identity,
            start=start,
            end=end,
            observed_at=receive_at,
        )
        self._assert_request(identity, instrument)
        contract = self._public_result(
            "/v5/market/instruments-info", {"category": BYBIT_CATEGORY, "symbol": self._symbol}
        )
        self._assert_linear(contract)
        rows = contract.get("list")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise WrongSourceError("Bybit instruments-info list is malformed.")
        required_fields = {"symbol", "contractType", "quoteCoin", "baseCoin", "status"}
        if any(
            row.get("symbol") == self._symbol and not required_fields <= row.keys() for row in rows
        ):
            raise WrongSourceError("Bybit instruments-info contract fields are incomplete.")
        try:
            contract_from_bybit_instruments({"result": contract}, requested_symbol=self._symbol)
        except (WrongMarketError, WrongInstrumentError) as exc:
            raise UnsupportedTradeContractError("Trade contract not verified.") from exc
        batch = self.fetch_ordered_trades(
            identity=identity,
            instrument=instrument,
            start=start,
            end=end,
            source_connection_id=source_connection_id,
            receive_at=receive_at,
        )
        proof = self._lineages[source_connection_id]
        if (
            proof.initial_oldest_ms is None
            or int(start.timestamp() * 1000) <= proof.initial_oldest_ms
        ):
            raise IncompleteTradeWindowError(
                "Bybit initial page does not prove the entire start bucket."
            )
        if not proof.proven or int(proof.proven[-1]["time_ms"]) < int(end.timestamp() * 1000):
            raise IncompleteTradeWindowError("Bybit tail does not prove the closed window end.")
        # The existing ledger proves overlap; its synthetic rank is transport history.
        # Window-local ordinals keep identical execution sets stable across poll histories.
        normalized = (
            with_content_hash(
                trade.model_copy(update={"sequence": index}),
                extra_exclude=frozenset({"source_connection_id"}),
            )
            for index, trade in enumerate(batch.trades)
        )
        return build_released_trade_snapshot(
            normalized,
            identity=identity,
            lineage_id=source_connection_id,
            window_start=start,
            window_end=end,
            observed_at=receive_at,
        )

    def status(self) -> ProviderStatus:
        try:
            self._public_result("/v5/market/time", None)
            self._last_error = None
            self._last_success_at = datetime.now(UTC)
            return ProviderStatus(
                name=self.name,
                kind=self.kind,
                health=ProviderHealth.HEALTHY,
                using_fallback=False,
                is_mock=False,
                detail="Bybit USDT linear perpetual public REST (read-only, no spot fallback).",
                last_success_at=self._last_success_at,
                error_message=None,
            )
        except (RegionalProviderFailureError, RateLimitedError) as exc:
            self._last_error = str(exc)[:200]
            return ProviderStatus(
                name=self.name,
                kind=self.kind,
                health=ProviderHealth.UNAVAILABLE,
                using_fallback=False,
                is_mock=False,
                detail="Bybit USDT perpetual source unreachable; no spot or fabricated fallback.",
                last_success_at=self._last_success_at,
                error_message=self._last_error,
            )

    def _recent_trades_covering(
        self,
        start: datetime,
        end: datetime,
        source_connection_id: UUID,
        receive_at: datetime,
    ) -> list[dict[str, Any]]:
        """Return the proven half-open window, extending it only from an overlap.

        The first read on a connection must see a recent-trade page that reaches
        ``start``. Later reads keep that proven tail. A new page has to contain
        the last proven execution id, then contributes only prints after it.
        Bybit ``seq`` is not a per-trade id. A page that drops the watermark is
        a gap. Trades are not invented to fill it.
        """

        start_ms = int(start.astimezone(UTC).timestamp() * 1000)
        end_ms = int(end.astimezone(UTC).timestamp() * 1000)
        prints = self._cached_recent_prints(receive_at)
        proof = self._prepare_connection(source_connection_id)
        try:
            self._assign_ranks(prints)
            self._recent_exec_ids = {str(item["exec_id"]) for item in prints}
            if not proof.proven:
                self._prove_initial_tail(prints, start_ms, proof)
            else:
                self._extend_proven_tail(prints, proof)
            if proof.proven_from_ms is None or start_ms < proof.proven_from_ms:
                raise IncompleteTradeWindowError(
                    "Bybit recent trades do not prove the requested window. "
                    "Refusing to fabricate the missing prefix."
                )
            window = [item for item in proof.proven if start_ms <= int(item["time_ms"]) < end_ms]
            for item in window:
                item["sequence"] = self._rank_by_exec[str(item["exec_id"])]
            return window
        finally:
            self._prune_ranks()

    def release_symbol_history(self, symbol: str) -> None:
        """Release bulk windows; retain one provider page per live proof.

        The retained execution ledger and anchor never renumber overlapping
        prints. A requested prefix removed by this bound fails closed.
        """

        if symbol.strip().upper() != self._symbol:
            return
        for proof in self._lineages.values():
            self._trim_proven_prefix(proof, limit=_RECENT_TRADE_LIMIT)
        self._prune_ranks()

    def _assert_linear(self, result: Mapping[str, Any]) -> None:
        category = str(result.get("category", "")).lower()
        if category == "spot":
            raise SpotFallbackRejectedError(
                "Bybit spot trades cannot satisfy USDT perpetual evidence."
            )
        if category != BYBIT_CATEGORY:
            raise WrongMarketError(
                f"Bybit category {category or 'missing'} is not linear perpetual evidence."
            )
        symbol = str(result.get("symbol", "")).upper()
        if symbol and symbol != self._symbol:
            raise WrongInstrumentError(
                f"Bybit symbol {symbol} is not the configured {self._symbol} perpetual."
            )

    def _assert_request(
        self,
        identity: EvidenceMarketIdentity,
        instrument: InstrumentIdentity,
    ) -> None:
        require_perpetual(identity)
        require_instrument(identity, instrument)
        if instrument.instrument_id != self._instrument.instrument_id:
            if self._symbol == BYBIT_SYMBOL:
                raise WrongInstrumentError("Bybit adapter only serves linear BTCUSDT perpetual.")
            raise WrongInstrumentError(
                f"Bybit adapter instance only serves linear {self._symbol} perpetual."
            )
        if identity.venue is not VenueId.BYBIT:
            raise WrongMarketError("Bybit adapter cannot serve a non-Bybit venue.")
        if identity.instrument.product_family is not ProductFamily.USDM_FUTURES:
            raise SpotFallbackRejectedError(
                "Bybit spot or inverse books are not USDT perpetual evidence."
            )
        if identity.instrument.market_type is not MarketType.PERPETUAL:
            raise WrongMarketError("Bybit adapter cannot serve non-perpetual markets.")
        source = identity.source
        provenance = identity.provenance
        if (
            source.family is not SourceFamily.BYBIT_USDT_PERPETUAL_PUBLIC
            or source.provider_name != self.name
            or provenance.source_family is not SourceFamily.BYBIT_USDT_PERPETUAL_PUBLIC
            or provenance.provider_name != self.name
            or provenance.fallback_used
            or not provenance.is_live
            or provenance.is_mock
        ):
            raise WrongSourceError(
                "Live Bybit evidence identity must exactly identify the Bybit USDT perpetual."
            )
        if source.aggressor_convention != BYBIT_AGGRESSOR_CONVENTION:
            raise WrongSourceError("Bybit aggressor convention does not match the adapter.")

    def _parse_kline(
        self,
        row: Any,
        *,
        instrument: InstrumentIdentity,
        timeframe: Timeframe,
        evaluated_at: datetime,
        grace: timedelta,
    ) -> OhlcvBar:
        if not isinstance(row, list) or len(row) < 7:
            raise WrongMarketError("Bybit kline row is malformed.")
        interval_start = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC)
        return build_ohlcv_bar(
            instrument=instrument,
            timeframe=timeframe,
            interval_start=interval_start,
            open_=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
            base_volume=Decimal(str(row[5])),
            quote_volume=Decimal(str(row[6])),
            evaluated_at=evaluated_at,
            grace=grace,
            adapter_version=BYBIT_ADAPTER_VERSION,
        )

    def _parse_trade(
        self,
        row: Mapping[str, Any],
        *,
        instrument: InstrumentIdentity,
        source_connection_id: UUID,
        receive_at: datetime,
    ) -> TradeEvent:
        symbol = str(row.get("symbol", "")).upper()
        if symbol != self._symbol:
            raise WrongInstrumentError(
                f"Bybit trade symbol {symbol} is not the configured {self._symbol} perpetual."
            )
        side = str(row.get("side", "")).lower()
        if side == "buy":
            buyer_is_maker = False
        elif side == "sell":
            buyer_is_maker = True
        else:
            raise WrongMarketError("Bybit trade is missing taker side.")
        exec_id = str(row["exec_id"])
        if len(exec_id) > 40:
            raise WrongMarketError("Bybit execution id is longer than the trade identity.")
        return build_trade_event(
            instrument=instrument,
            venue_trade_id=exec_id,
            sequence=int(row["sequence"]),
            price=Decimal(str(row["price"])),
            quantity=Decimal(str(row["size"])),
            buyer_is_maker=buyer_is_maker,
            event_timestamp=datetime.fromtimestamp(int(str(row["time"])) / 1000, tz=UTC),
            receive_timestamp=receive_at.astimezone(UTC),
            source_connection_id=source_connection_id,
            adapter_version=BYBIT_ADAPTER_VERSION,
            aggressor_convention=BYBIT_AGGRESSOR_CONVENTION,
        )

    def _public_result(self, path: str, params: Mapping[str, str | int] | None) -> dict[str, Any]:
        payload = self._http.get_json(path, params)
        if not isinstance(payload, dict):
            raise WrongMarketError("Bybit payload is not an object.")
        code = str(payload.get("retCode", ""))
        if code == "10006":
            raise RateLimitedError(
                "Bybit public perpetual source rate-limited the read-only client."
            )
        if code != "0":
            raise RegionalProviderFailureError(
                f"Bybit public perpetual source returned retCode {code}."
            )
        self._last_success_at = datetime.now(UTC)
        self._last_error = None
        result = payload.get("result", {})
        if not isinstance(result, dict):
            raise WrongMarketError("Bybit result is not an object.")
        return result

    def _load_recent_prints(self) -> list[dict[str, Any]]:
        result = self._public_result(
            "/v5/market/recent-trade",
            {"category": BYBIT_CATEGORY, "symbol": self._symbol, "limit": _RECENT_TRADE_LIMIT},
        )
        self._assert_linear(result)
        payload = result.get("list")
        if not isinstance(payload, list) or not payload or len(payload) > _RECENT_TRADE_LIMIT:
            raise IncompleteTradeWindowError(
                "Bybit recent trades do not prove the requested window. "
                "Refusing to fabricate the missing prefix."
            )
        prints = [_normalize_print(row) for row in payload]
        prints.sort(key=lambda item: (item["time_ms"], item["exec_id"]))
        _reject_conflicting_exec_ids(prints)
        return _unique_exec_ids(prints)

    def _cached_recent_prints(self, receive_at: datetime) -> list[dict[str, Any]]:
        """One public page for all consumers at the same declared evaluation.

        Each consumer proves its own bounded window/lineage over independent
        copies. Later polls reacquire; no absent overlap is filled by the cache.
        Future prints cannot prove the requested end boundary.
        """
        key = (self.name, self._symbol, "recent-trade/v1", receive_at.astimezone(UTC).isoformat())
        if not self._reuse_recent_pages:
            millis = int(receive_at.timestamp() * 1000)
            prints = [x for x in self._load_recent_prints() if int(x["time_ms"]) <= millis]
            if not prints:
                raise IncompleteTradeWindowError("No public prints at/before observation time.")
            return prints
        with self._recent_print_guard:
            cached = self._recent_print_cache.get(key)
            if cached is None:
                prints = self._load_recent_prints()
                millis = int(receive_at.timestamp() * 1000)
                prints = [x for x in prints if int(x["time_ms"]) <= millis]
                if not prints:
                    raise IncompleteTradeWindowError("No public prints at/before observation time.")
                cached = tuple(dict(x) for x in prints)
                self._recent_print_cache.put(key, cached)
            else:
                record_cache_hit()
            return [dict(x) for x in cached]

    def _prove_initial_tail(
        self, prints: list[dict[str, Any]], start_ms: int, proof: _LineageProof
    ) -> None:
        if int(prints[0]["time_ms"]) > start_ms:
            raise IncompleteTradeWindowError(
                "Bybit recent trades do not prove the requested window. "
                "Refusing to fabricate the missing prefix."
            )
        proven = [item for item in prints if int(item["time_ms"]) >= start_ms]
        if not proven:
            raise IncompleteTradeWindowError(
                "Bybit recent trades have no prints inside the requested window."
            )
        self._require_rank_tail(proven)
        proof.proven = proven
        proof.initial_oldest_ms = int(prints[0]["time_ms"])
        proof.proven_from_ms = start_ms
        proof.anchor_exec_id = str(proven[-1]["exec_id"])
        self._trim_proven_prefix(proof)

    def _extend_proven_tail(self, prints: list[dict[str, Any]], proof: _LineageProof) -> None:
        anchor = proof.anchor_exec_id
        anchor_at = next(
            (index for index, item in enumerate(prints) if item["exec_id"] == anchor),
            None,
        )
        if anchor is None or anchor_at is None:
            raise GapDetectedError(
                "Bybit recent-trade buffer dropped the last proven print. "
                "Refusing to fabricate the missing interval."
            )
        extension = prints[anchor_at + 1 :]
        if extension:
            self._require_rank_tail(proof.proven + extension)
            proof.proven.extend(extension)
            proof.anchor_exec_id = str(extension[-1]["exec_id"])
        self._trim_proven_prefix(proof)

    def _assign_ranks(self, prints: list[dict[str, Any]]) -> None:
        for item in prints:
            exec_id = str(item["exec_id"])
            signature = (item["time_ms"], item["price"], item["size"], item["side"])
            prior = self._signature_by_exec.get(exec_id)
            if prior is not None and signature != prior:
                raise DuplicateDataError(f"Conflicting content for Bybit execution {exec_id}.")
        for item in prints:
            exec_id = str(item["exec_id"])
            if exec_id in self._rank_by_exec:
                continue
            self._rank_by_exec[exec_id] = self._next_rank
            self._signature_by_exec[exec_id] = (
                item["time_ms"],
                item["price"],
                item["size"],
                item["side"],
            )
            self._next_rank += 1

    def _require_rank_tail(self, prints: list[dict[str, Any]]) -> None:
        ranks = [self._rank_by_exec[str(item["exec_id"])] for item in prints]
        if ranks != list(range(ranks[0], ranks[0] + len(ranks))):
            raise GapDetectedError(
                "Bybit recent-trade order is not a contiguous proven tail. Refusing a partial CVD."
            )

    def _trim_proven_prefix(self, proof: _LineageProof, *, limit: int = _MAX_PROVEN_TRADES) -> None:
        if not proof.proven:
            return
        newest_ms = int(proof.proven[-1]["time_ms"])
        cutoff_ms = newest_ms - int(_PROVEN_TAIL_RETENTION.total_seconds() * 1000)
        keep_at = max(0, len(proof.proven) - limit)
        while keep_at < len(proof.proven) - 1 and int(proof.proven[keep_at]["time_ms"]) < cutoff_ms:
            keep_at += 1
        if keep_at > 0:
            first_ms = int(proof.proven[keep_at]["time_ms"])
            # A count bound can split a millisecond bucket. Never claim that
            # partial bucket as complete coverage, even though its anchor stays.
            if int(proof.proven[keep_at - 1]["time_ms"]) == first_ms:
                first_ms += 1
            proof.proven = proof.proven[keep_at:]
            proof.proven_from_ms = max(proof.proven_from_ms or first_ms, first_ms)

    def _prepare_connection(self, source_connection_id: UUID) -> _LineageProof:
        self._rank_connection = source_connection_id
        proof = self._lineages.get(source_connection_id)
        if proof is None:
            proof = _LineageProof()
            self._lineages[source_connection_id] = proof
        self._lineages.move_to_end(source_connection_id)
        while len(self._lineages) > _MAX_LINEAGE_PROOFS:
            self._lineages.popitem(last=False)
        return proof

    def _prune_ranks(self) -> None:
        retained = set(self._recent_exec_ids)
        for proof in self._lineages.values():
            retained.update(str(item["exec_id"]) for item in proof.proven)
        self._rank_by_exec = {
            key: rank for key, rank in self._rank_by_exec.items() if key in retained
        }
        self._signature_by_exec = {
            key: signature for key, signature in self._signature_by_exec.items() if key in retained
        }

    def close(self) -> None:
        self._http.close()


def _normalize_print(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise WrongMarketError("Bybit trade row is malformed.")
    exec_id = str(row.get("execId", "")).strip()
    if not exec_id:
        raise WrongMarketError("Bybit trade is missing execId.")
    try:
        time_ms = int(str(row["time"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise WrongMarketError("Bybit trade is missing a timestamp.") from exc
    return {
        "exec_id": exec_id,
        "time_ms": time_ms,
        "symbol": row.get("symbol", ""),
        "price": row.get("price"),
        "size": row.get("size"),
        "side": row.get("side"),
        "time": row.get("time"),
    }


def _unique_exec_ids(prints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in prints:
        exec_id = str(item["exec_id"])
        if exec_id in seen:
            continue
        seen.add(exec_id)
        unique.append(item)
    return unique


def _reject_conflicting_exec_ids(prints: list[dict[str, Any]]) -> None:
    seen: dict[str, tuple[object, object, object, object]] = {}
    for item in prints:
        signature = (item["time_ms"], item["price"], item["size"], item["side"])
        previous = seen.get(str(item["exec_id"]))
        if previous is None:
            seen[str(item["exec_id"])] = signature
            continue
        if previous != signature:
            raise DuplicateDataError(f"Conflicting content for Bybit execution {item['exec_id']}.")


def _require_contiguous_sequences(trades: list[TradeEvent]) -> None:
    if len(trades) < 2:
        return
    previous = trades[0].sequence
    for trade in trades[1:]:
        if trade.sequence != previous + 1:
            raise GapDetectedError(
                f"Bybit trade sequence gap {previous + 1}-{trade.sequence - 1}; "
                "refusing a partial CVD."
            )
        previous = trade.sequence
