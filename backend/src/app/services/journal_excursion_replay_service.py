"""Deterministic journal trade excursion replay from HistoricalCandle (AT-032).

Record-only. Reads stored candles — never calls market-data providers, never
places orders. Persists MFE/MAE / available profit with ``excursion_source=replay``
and provenance. Manual/system values are protected unless FORCE is requested.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import JOURNAL_TRADE_NOT_FOUND_MSG, NotFoundError, ValidationAppError
from app.db.models import HistoricalCandle, JournalTrade
from app.providers.market_data import TIMEFRAME_SECONDS, normalize_symbol
from app.repositories.historical_candles import HistoricalCandleRepository
from app.repositories.journal_trades import JournalTradeRepository
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import (
    ActorType,
    AuditEventType,
    JournalTradeStatus,
    Timeframe,
    TradeDirection,
)
from app.schemas.human_vs_system import RunnerAnalysis
from app.schemas.journal_excursion_replay import (
    ExcursionOverwritePolicy,
    JournalExcursionBatchReplayRequest,
    JournalExcursionBatchReplayResult,
    JournalExcursionMetrics,
    JournalExcursionProvenance,
    JournalExcursionReplayRequest,
    JournalExcursionReplayResult,
    JournalTradeExcursionRead,
)
from app.services.audit_service import AuditService
from app.services.journal_excursion_calculator import (
    CandleBar,
    ExcursionCalculation,
    compute_trade_excursions,
    filter_bars_in_window,
)
from app.services.runner_missed_profit_analyzer import (
    RunnerAnalysisInput,
    RunnerAndMissedProfitAnalyzer,
)

_REQUEST_TAG = "journal-excursion-replay"
_PROTECTED_SOURCES = frozenset({"manual", "system"})
_EXCURSION_SOURCE_REPLAY = "replay"
_COVERAGE_RATIO = 0.85


class JournalExcursionReplayService:
    """Compute and optionally persist replay-derived excursion metrics."""

    def __init__(
        self,
        session: Session,
        audit_service: AuditService,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._audit = audit_service
        self._settings = settings or get_settings()
        self._trades = JournalTradeRepository(session)
        self._candles = HistoricalCandleRepository(session)
        self._runner = RunnerAndMissedProfitAnalyzer()

    def replay_trade(
        self,
        trade_id: uuid.UUID,
        request: JournalExcursionReplayRequest,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> JournalExcursionReplayResult:
        row = self._trades.get_scoped(trade_id, organization_id=organization_id)
        if row is None or row.user_id != user_id:
            raise NotFoundError(JOURNAL_TRADE_NOT_FOUND_MSG)
        return self._replay_row(row, request)

    def replay_batch(
        self,
        request: JournalExcursionBatchReplayRequest,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> JournalExcursionBatchReplayResult:
        batch_cap = min(request.limit, self._settings.journal_replay_batch_max)
        # Fetch one extra to detect truncation against the caller's limit.
        candidates = self._trades.list_replay_candidates(
            organization_id=organization_id,
            user_id=user_id,
            symbol=request.symbol,
            overwrite_policy=request.overwrite_policy.value,
            limit=batch_cap + 1,
        )
        truncated = len(candidates) > batch_cap
        candidates = candidates[:batch_cap]

        single = JournalExcursionReplayRequest(
            persist=request.persist,
            overwrite_policy=request.overwrite_policy,
            include_post_exit_runner=request.include_post_exit_runner,
            exchange=request.exchange,
        )
        results: list[JournalExcursionReplayResult] = []
        applied = skipped = failed = 0
        for row in candidates:
            try:
                outcome = self._replay_row(row, single)
            except ValidationAppError as exc:
                failed += 1
                results.append(
                    JournalExcursionReplayResult(
                        journal_trade_id=row.id,
                        applied=False,
                        skipped_reason=str(exc.message),
                        provenance=JournalExcursionProvenance(
                            limitations=[str(exc.message)],
                        ),
                    )
                )
                continue
            results.append(outcome)
            if outcome.applied:
                applied += 1
            else:
                skipped += 1

        return JournalExcursionBatchReplayResult(
            processed=len(results),
            applied=applied,
            skipped=skipped,
            failed=failed,
            truncated=truncated,
            results=results,
        )

    # ------------------------------------------------------------------ #
    # Core
    # ------------------------------------------------------------------ #

    def _replay_row(
        self,
        row: JournalTrade,
        request: JournalExcursionReplayRequest,
    ) -> JournalExcursionReplayResult:
        skip = self._overwrite_skip_reason(row, request.overwrite_policy)
        if skip is not None:
            return JournalExcursionReplayResult(
                journal_trade_id=row.id,
                applied=False,
                skipped_reason=skip,
                provenance=JournalExcursionProvenance(
                    limitations=[skip],
                    computed_at=datetime.now(UTC),
                ),
                trade=JournalTradeExcursionRead.model_validate(row),
            )

        window_error = self._validate_trade_window(row)
        if window_error is not None:
            return JournalExcursionReplayResult(
                journal_trade_id=row.id,
                applied=False,
                skipped_reason=window_error,
                provenance=JournalExcursionProvenance(
                    limitations=[window_error],
                    computed_at=datetime.now(UTC),
                ),
                trade=JournalTradeExcursionRead.model_validate(row),
            )

        assert row.entry_time is not None and row.exit_time is not None
        assert row.entry_price is not None
        exchange = (request.exchange or row.exchange or "").strip().lower()
        if not exchange:
            msg = "Exchange required for candle lookup (set on trade or request)."
            return JournalExcursionReplayResult(
                journal_trade_id=row.id,
                applied=False,
                skipped_reason=msg,
                provenance=JournalExcursionProvenance(
                    limitations=[msg],
                    computed_at=datetime.now(UTC),
                ),
                trade=JournalTradeExcursionRead.model_validate(row),
            )

        try:
            timeframe = Timeframe(row.timeframe)
        except ValueError:
            msg = f"Unsupported timeframe for replay: {row.timeframe!r}."
            return JournalExcursionReplayResult(
                journal_trade_id=row.id,
                applied=False,
                skipped_reason=msg,
                provenance=JournalExcursionProvenance(
                    limitations=[msg],
                    computed_at=datetime.now(UTC),
                ),
                trade=JournalTradeExcursionRead.model_validate(row),
            )

        symbol = normalize_symbol(row.symbol)
        max_candles = self._settings.journal_replay_max_candles
        # Pad the fetch slightly so overlapping edge bars are included.
        step = TIMEFRAME_SECONDS.get(timeframe, 3600)
        fetch_start = row.entry_time - timedelta(seconds=step)
        fetch_end = row.exit_time + timedelta(seconds=step)
        raw = self._candles.list_range(
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe.value,
            start_time=fetch_start,
            end_time=fetch_end,
            limit=max_candles,
        )
        bars = filter_bars_in_window(
            [_to_bar(c) for c in raw],
            entry_time=row.entry_time,
            exit_time=row.exit_time,
        )

        calc = compute_trade_excursions(
            direction=row.direction,
            entry_price=row.entry_price,
            size=row.size,
            net_pnl=row.net_pnl,
            bars=bars,
        )
        gaps = _count_gaps(bars, step)
        expected = _expected_bar_count(row.entry_time, row.exit_time, step)
        window_complete = (
            calc.candle_count > 0
            and expected > 0
            and calc.candle_count >= max(1, int(expected * _COVERAGE_RATIO))
            and gaps <= max(1, expected // 20)
            and len(raw) < max_candles
        )
        limitations = list(calc.limitations)
        if calc.candle_count == 0:
            limitations.append("No HistoricalCandle rows overlap the trade window.")
        if gaps > 0:
            limitations.append(f"{gaps} gap(s) detected in trade-window candles.")
        if not window_complete and calc.candle_count > 0:
            limitations.append(
                "Incomplete candle coverage for trade window — metrics are best-effort."
            )
        if len(raw) >= max_candles:
            limitations.append(f"Candle fetch hit journal_replay_max_candles={max_candles}.")

        freshness_note: str | None = None
        is_stale = calc.any_stale or not window_complete
        if is_stale:
            freshness_note = "Stale or incomplete HistoricalCandle coverage for trade window."

        computed_at = datetime.now(UTC)
        provenance = JournalExcursionProvenance(
            excursion_source=_EXCURSION_SOURCE_REPLAY,
            data_source=calc.data_source,
            is_stale=is_stale,
            freshness_note=freshness_note,
            candle_count=calc.candle_count,
            gaps_detected=gaps,
            window_complete=window_complete,
            computed_at=computed_at,
            limitations=limitations,
        )

        metrics: JournalExcursionMetrics | None = None
        applied = False
        skipped_reason: str | None = None

        if calc.candle_count == 0:
            skipped_reason = "missing_candles"
        else:
            metrics = JournalExcursionMetrics(
                mfe_price=calc.mfe_price,
                mae_price=calc.mae_price,
                mfe_amount=calc.mfe_amount,
                mae_amount=calc.mae_amount,
                available_profit=calc.available_profit,
                realized_vs_available_pct=calc.realized_vs_available_pct,
            )
            if request.persist:
                self._apply_metrics(row, calc, provenance)
                applied = True
                self._record_audit(row, provenance)
            else:
                skipped_reason = "persist_disabled"

        runner = None
        if request.include_post_exit_runner and row.exit_time is not None:
            runner = self._post_exit_runner(row, exchange=exchange, timeframe=timeframe)

        return JournalExcursionReplayResult(
            journal_trade_id=row.id,
            applied=applied,
            skipped_reason=skipped_reason,
            metrics=metrics,
            provenance=provenance,
            post_exit_runner=runner,
            trade=JournalTradeExcursionRead.model_validate(row),
        )

    def _apply_metrics(
        self,
        row: JournalTrade,
        calc: ExcursionCalculation,
        provenance: JournalExcursionProvenance,
    ) -> None:
        row.mfe_price = calc.mfe_price
        row.mae_price = calc.mae_price
        row.mfe_amount = calc.mfe_amount
        row.mae_amount = calc.mae_amount
        row.available_profit = calc.available_profit
        row.realized_vs_available_pct = calc.realized_vs_available_pct
        row.excursion_source = _EXCURSION_SOURCE_REPLAY
        row.excursion_data_source = provenance.data_source
        row.excursion_is_stale = provenance.is_stale
        row.excursion_freshness_note = provenance.freshness_note
        row.excursion_candle_count = provenance.candle_count
        row.excursion_gaps_detected = provenance.gaps_detected
        row.excursion_window_complete = provenance.window_complete
        row.excursion_computed_at = provenance.computed_at
        self._trades.add(row)

    def _post_exit_runner(
        self,
        row: JournalTrade,
        *,
        exchange: str,
        timeframe: Timeframe,
    ) -> RunnerAnalysis:
        assert row.exit_time is not None
        step = TIMEFRAME_SECONDS.get(timeframe, 3600)
        lookahead = RunnerAndMissedProfitAnalyzer.LOOKAHEAD_BARS
        end = row.exit_time + timedelta(seconds=step * lookahead)
        raw = self._candles.list_range(
            symbol=normalize_symbol(row.symbol),
            exchange=exchange,
            timeframe=timeframe.value,
            start_time=row.exit_time,
            end_time=end,
            limit=lookahead,
        )
        candles_after: list[tuple[datetime, Decimal, Decimal, Decimal, Decimal]] = [
            (c.open_time, c.open, c.high, c.low, c.close) for c in raw
        ]
        tp_prices = _planned_target_prices(row.planned_targets)
        invalidation = row.planned_stop_price
        return self._runner.analyze(
            RunnerAnalysisInput(
                entry_price=row.entry_price,
                exit_price=row.exit_price,
                exit_time=row.exit_time,
                direction=row.direction,
                tp_plan_prices=tp_prices,
                runner_enabled=bool(row.runner_enabled),
                invalidation_price=invalidation,
                candles_after_exit=candles_after or None,
            )
        )

    def _overwrite_skip_reason(
        self,
        row: JournalTrade,
        policy: ExcursionOverwritePolicy,
    ) -> str | None:
        source = (row.excursion_source or "").strip().lower()
        if not source:
            return None
        if source == _EXCURSION_SOURCE_REPLAY:
            return None
        if policy == ExcursionOverwritePolicy.FORCE:
            return None
        if source in _PROTECTED_SOURCES:
            return f"Protected excursion_source={source!r}; use overwrite_policy=force to replace."
        # Unknown non-empty source is also protected under skip_protected.
        return (
            f"Existing excursion_source={source!r} is protected; "
            "use overwrite_policy=force to replace."
        )

    @staticmethod
    def _validate_trade_window(row: JournalTrade) -> str | None:
        if row.status != JournalTradeStatus.CLOSED:
            return "Replay requires a closed journal trade."
        if row.entry_price is None or row.entry_time is None:
            return "Entry price/time required for excursion replay."
        if row.exit_time is None:
            return "Exit time required for excursion replay."
        if row.exit_time <= row.entry_time:
            return "Invalid trade window: exit_time must be after entry_time."
        if row.direction not in (TradeDirection.LONG, TradeDirection.SHORT):
            return "Trade direction must be long or short."
        return None

    def _record_audit(
        self,
        row: JournalTrade,
        provenance: JournalExcursionProvenance,
    ) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id=_REQUEST_TAG,
                trace_id=_REQUEST_TAG,
                event_type=AuditEventType.JOURNAL_TRADE_EXCURSION_REPLAYED,
                resource_type="journal_trade",
                resource_id=str(row.id),
                organization_id=row.organization_id,
                user_id=row.user_id,
                actor_type=ActorType.USER,
                metadata={
                    "action": "replay_excursions",
                    "symbol": row.symbol,
                    "excursion_source": _EXCURSION_SOURCE_REPLAY,
                    "data_source": provenance.data_source or "",
                    "candle_count": str(provenance.candle_count),
                    "gaps_detected": str(provenance.gaps_detected),
                    "window_complete": str(provenance.window_complete),
                    "is_stale": str(provenance.is_stale),
                },
            )
        )


def _to_bar(row: HistoricalCandle) -> CandleBar:
    return CandleBar(
        open_time=row.open_time,
        close_time=row.close_time,
        high=row.high,
        low=row.low,
        source=row.source,
        is_stale=bool(row.is_stale),
        freshness_note=row.freshness_note,
    )


def _count_gaps(bars: list[CandleBar], step_seconds: int) -> int:
    if len(bars) < 2:
        return 0
    ordered = sorted(bars, key=lambda b: b.open_time)
    gaps = 0
    for prev, cur in itertools.pairwise(ordered):
        delta = (cur.open_time - prev.open_time).total_seconds()
        if delta > step_seconds * 1.5:
            gaps += 1
    return gaps


def _expected_bar_count(entry: datetime, exit: datetime, step_seconds: int) -> int:
    if step_seconds <= 0:
        return 0
    span = max(0.0, (exit - entry).total_seconds())
    return int(span // step_seconds) + 1


def _planned_target_prices(raw: list[dict[str, object]] | None) -> list[Decimal]:
    if not raw:
        return []
    prices: list[Decimal] = []
    for item in raw:
        price = item.get("price")
        if price is None:
            continue
        prices.append(Decimal(str(price)))
    return prices
