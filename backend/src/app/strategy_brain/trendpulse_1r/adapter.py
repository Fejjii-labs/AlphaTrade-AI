"""Causal, bounded, Decimal-only TrendPulse1R research evaluation. No I/O."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal, localcontext
from uuid import UUID, uuid5

from app.market_contracts.enums import ContractStyle, Finality, FreshnessState, SourceFamily
from app.market_contracts.identity import (
    EvidenceMarketIdentity,
    interval_timedelta,
    require_perpetual,
)
from app.market_contracts.observation import PublicMarketObservation
from app.market_contracts.ohlcv import OhlcvBar, assert_closed_series_bars
from app.schemas.common import Timeframe, TradeDirection
from app.schemas.trade_plan import ContractType, InstrumentRules
from app.strategy_brain.sfp.contracts import available_at, validate_binding
from app.strategy_brain.trendpulse_1r.contracts import (
    DECIMAL_PRECISION,
    TRENDPULSE_ADAPTER_VERSION,
    TRENDPULSE_NAMESPACE,
    TrendPulseEvidenceReference,
    TrendPulseResult,
    TrendPulseSignal,
    TrendPulseSpec,
    TrendPulseStatus,
    exact_hash,
    require_aware,
)

Pair = tuple[OhlcvBar, PublicMarketObservation]


class _RefusalError(ValueError):
    def __init__(self, reason: str, *, missing: bool = False):
        super().__init__(reason)
        self.missing = missing


def _bounded_decimal(value: Decimal) -> bool:
    exponent = value.as_tuple().exponent
    return (
        value.is_finite()
        and isinstance(exponent, int)
        and len(value.as_tuple().digits) <= 24
        and exponent >= -12
        and value.adjusted() < 24
    )


def _window(
    bars: tuple[OhlcvBar, ...],
    observations: tuple[PublicMarketObservation, ...],
    *,
    timeframe: Timeframe,
    end: datetime,
    knowledge_cutoff: datetime,
    count: int,
) -> tuple[Pair, ...]:
    if len(bars) != len(observations):
        raise _RefusalError("missing_observation_binding", missing=True)
    if len(bars) > 1024:
        raise _RefusalError("input_bound_exceeded")
    by_interval: dict[datetime, Pair] = {}
    by_event: dict[str, datetime] = {}
    start = end - interval_timedelta(timeframe) * count
    for bar, obs in zip(bars, observations, strict=True):
        if (
            not start <= bar.interval_start < end
            or bar.interval_end > end
            or available_at(obs) > knowledge_cutoff
        ):
            continue  # Future receipts/candles and unused history cannot influence a decision.
        if bar.timeframe is not timeframe:
            raise _RefusalError("timeframe_mismatch")
        if bar.finality is not Finality.FINAL or not bar.provider_complete:
            raise _RefusalError("closed_complete_candles_required", missing=True)
        utc_start = bar.interval_start.astimezone(UTC)
        minutes = 15 if timeframe is Timeframe.M15 else 5
        if utc_start.minute % minutes or utc_start.second or utc_start.microsecond:
            raise _RefusalError("unaligned_candle")
        if not all(
            _bounded_decimal(v)
            for v in (
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.base_volume,
                bar.quote_volume,
                bar.instrument.contract_multiplier,
            )
        ):
            raise _RefusalError("numeric_precision_unsupported")
        try:
            # Existing canonical OHLCV proof helper; SFP rules unchanged.
            validate_binding(bar, obs)
            require_perpetual(obs.identity)
        except ValueError as exc:
            raise _RefusalError("invalid_canonical_binding") from exc
        if (
            obs.freshness_state is not FreshnessState.FRESH
            or obs.identity.provenance.regional_failure
            or obs.identity.source.family
            not in {
                SourceFamily.REPLAY_FIXTURE,
                SourceFamily.BINANCE_USDM_FUTURES_PUBLIC,
                SourceFamily.BYBIT_USDT_PERPETUAL_PUBLIC,
            }
            or bar.adapter_version != obs.identity.source.adapter_version
        ):
            raise _RefusalError("unsupported_or_stale_source")
        existing = by_interval.get(bar.interval_start)
        if existing:
            old_bar, old_obs = existing
            if (
                old_bar.content_hash != bar.content_hash
                or old_obs.content_hash != obs.content_hash
                or old_obs.identity != obs.identity
            ):
                raise _RefusalError("conflicting_duplicate_or_revision")
            if available_at(obs) < available_at(old_obs):
                by_interval[bar.interval_start] = (bar, obs)
            continue
        if bar.source_event_id in by_event:
            raise _RefusalError("conflicting_duplicate_or_revision")
        by_interval[bar.interval_start] = (bar, obs)
        by_event[bar.source_event_id] = bar.interval_start
    pairs = tuple(by_interval[key] for key in sorted(by_interval))
    if len(pairs) != count or not pairs or pairs[-1][0].interval_end != end:
        raise _RefusalError("missing_causal_history", missing=True)
    identity = pairs[0][1].identity
    if any(obs.identity != identity or bar.instrument != identity.instrument for bar, obs in pairs):
        raise _RefusalError("mixed_market_identity")
    try:
        assert_closed_series_bars(identity, timeframe, [bar for bar, _ in pairs])
    except ValueError as exc:
        raise _RefusalError("non_contiguous_history", missing=True) from exc
    return pairs


def _ema(closes: tuple[Decimal, ...], period: int) -> tuple[Decimal, ...]:
    seed = sum(closes[:period], Decimal(0)) / Decimal(period)
    values = [seed] * period
    alpha = Decimal(2) / Decimal(period + 1)
    for close in closes[period:]:
        values.append(values[-1] + alpha * (close - values[-1]))
    return tuple(values)


def _structure(bars: tuple[OhlcvBar, ...], sign: int) -> tuple[str, str, str, str] | None:
    # Strict two-wing pivots. Ties never establish a swing; both right wings are closed.
    highs, lows = [], []
    for i in range(2, len(bars) - 2):
        wings = bars[i - 2 : i] + bars[i + 1 : i + 3]
        if all(bars[i].high > b.high for b in wings):
            highs.append(i)
        if all(bars[i].low < b.low for b in wings):
            lows.append(i)
    if len(highs) < 2 or len(lows) < 2:
        return None
    h1, h2 = highs[-2:]
    l1, l2 = lows[-2:]
    if (bars[h2].high - bars[h1].high) * sign <= 0 or (bars[l2].low - bars[l1].low) * sign <= 0:
        return None
    # Last two swing pairs must alternate, ending in the trend-side retracement.
    if not (h1 < l1 < h2 < l2 if sign == 1 else l1 < h1 < l2 < h2):
        return None
    return (
        bars[h1].source_event_id,
        bars[l1].source_event_id,
        bars[h2].source_event_id,
        bars[l2].source_event_id,
    )


def _geometry(
    trigger: OhlcvBar, pullback: tuple[OhlcvBar, ...], spec: TrendPulseSpec, rules: InstrumentRules
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    long = spec.direction is TradeDirection.LONG
    tick = rules.tick_size
    rounding = ROUND_CEILING if long else ROUND_FLOOR
    entry = (trigger.close / tick).to_integral_value(rounding=rounding) * tick
    extreme = min(b.low for b in pullback) if long else max(b.high for b in pullback)
    raw_stop = extreme - tick if long else extreme + tick
    stop = (raw_stop / tick).to_integral_value(
        rounding=ROUND_FLOOR if long else ROUND_CEILING
    ) * tick
    distance = entry - stop if long else stop - entry
    target = entry + distance if long else entry - distance
    p = spec.parameters
    if (
        entry <= 0
        or stop <= 0
        or target <= 0
        or distance < tick * 2
        or not p.minimum_risk_ratio <= distance / entry <= p.maximum_risk_ratio
        or abs(entry - trigger.close) / trigger.close > p.maximum_entry_rounding_ratio
        or not (stop < extreme < entry < target if long else target < entry < extreme < stop)
        or any(price % tick for price in (entry, stop, target))
    ):
        raise _RefusalError("invalid_rounded_risk_geometry")
    return entry, stop, target, extreme


def _validate_rules(identity: EvidenceMarketIdentity, rules: InstrumentRules) -> None:
    instrument = identity.instrument
    if (
        rules.contract_type is not ContractType.LINEAR
        or instrument.contract_style is not ContractStyle.LINEAR
        or (
            rules.base_currency,
            rules.quote_currency,
            rules.settlement_currency,
            rules.contract_multiplier,
        )
        != (
            instrument.base_asset,
            instrument.quote_asset,
            instrument.settlement_asset,
            instrument.contract_multiplier,
        )
        or instrument.quote_asset != "USDT"
        or instrument.price_unit != "USDT"
    ):
        raise _RefusalError("instrument_rules_mismatch")
    if not all(
        _bounded_decimal(v)
        for v in (
            rules.tick_size,
            rules.lot_size,
            rules.contract_multiplier,
            rules.minimum_quantity,
            rules.minimum_notional,
        )
    ):
        raise _RefusalError("numeric_precision_unsupported")


def evaluate_trendpulse(
    spec: TrendPulseSpec,
    *,
    trend_bars: tuple[OhlcvBar, ...],
    trend_observations: tuple[PublicMarketObservation, ...],
    entry_bars: tuple[OhlcvBar, ...],
    entry_observations: tuple[PublicMarketObservation, ...],
    instrument_rules: InstrumentRules,
    trigger_end: datetime,
    evaluated_at: datetime,
    seen_signal_ids: frozenset[UUID] = frozenset(),
) -> TrendPulseResult:
    """Evaluate one explicit closed 5m event; clocks and duplicate history are supplied."""
    spec = TrendPulseSpec.model_validate(spec.model_dump())
    instrument_rules = InstrumentRules.model_validate(instrument_rules.model_dump())
    require_aware(trigger_end)
    require_aware(evaluated_at)
    # A fresh context is the latest exact UTC 15m boundary at/before entry opening.
    trigger_start = trigger_end - timedelta(minutes=5)
    utc_start = trigger_start.astimezone(UTC)
    trend_end = utc_start.replace(minute=utc_start.minute // 15 * 15, second=0, microsecond=0)
    expires_at = trigger_end + timedelta(seconds=spec.parameters.expiry_seconds)
    if evaluated_at < trigger_end:
        return TrendPulseResult(status=TrendPulseStatus.UNAVAILABLE, reason="trigger_not_closed")
    if evaluated_at >= expires_at:
        return TrendPulseResult(status=TrendPulseStatus.REFUSED, reason="trigger_expired")
    with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
        try:
            return _evaluate(
                spec,
                trend_bars,
                trend_observations,
                entry_bars,
                entry_observations,
                instrument_rules,
                trigger_end,
                trend_end,
                evaluated_at,
                expires_at,
                seen_signal_ids,
            )
        except _RefusalError as exc:
            return TrendPulseResult(
                status=TrendPulseStatus.UNAVAILABLE if exc.missing else TrendPulseStatus.REFUSED,
                reason=str(exc),
            )


def _evaluate(
    spec: TrendPulseSpec,
    trend_bars: tuple[OhlcvBar, ...],
    trend_observations: tuple[PublicMarketObservation, ...],
    entry_bars: tuple[OhlcvBar, ...],
    entry_observations: tuple[PublicMarketObservation, ...],
    rules: InstrumentRules,
    trigger_end: datetime,
    trend_end: datetime,
    evaluated_at: datetime,
    expires_at: datetime,
    seen: frozenset[UUID],
) -> TrendPulseResult:
    p = spec.parameters
    trend = _window(
        trend_bars,
        trend_observations,
        timeframe=Timeframe.M15,
        end=trend_end,
        knowledge_cutoff=evaluated_at,
        count=p.trend_history_bars,
    )
    entry = _window(
        entry_bars,
        entry_observations,
        timeframe=Timeframe.M5,
        end=trigger_end,
        knowledge_cutoff=evaluated_at,
        count=p.entry_history_bars,
    )
    identity = trend[0][1].identity
    if identity.model_copy(update={"timeframe": Timeframe.M5}) != entry[0][1].identity:
        raise _RefusalError("mixed_market_identity")
    if identity.instrument.provider_symbol != spec.symbol:
        raise _RefusalError("symbol_mismatch")
    _validate_rules(identity, rules)
    trend_prices = tuple(bar.close for bar, _ in trend)
    fast, slow = _ema(trend_prices, 20), _ema(trend_prices, 50)
    sign = 1 if spec.direction is TradeDirection.LONG else -1
    slope = (slow[-1] - slow[-1 - p.slope_bars]) / slow[-1 - p.slope_bars]
    anchors = _structure(tuple(bar for bar, _ in trend[-p.structure_lookback :]), sign)
    if (
        (trend_prices[-1] - slow[-1]) * sign <= 0
        or (entry[-1][0].close - slow[-1]) * sign <= 0
        or (fast[-1] - slow[-1]) * sign <= 0
        or slope * sign < p.minimum_slope_ratio
        or anchors is None
    ):
        return TrendPulseResult(status=TrendPulseStatus.NO_SETUP, reason="trend_not_confirmed")
    entry_prices = tuple(bar.close for bar, _ in entry)
    entry_ema = _ema(entry_prices, 20)
    trigger = entry[-1][0]
    pullback = tuple(bar for bar, _ in entry[-4:-1])
    # Prior impulse separated from exactly three pullback candles, not the trigger itself.
    impulse = entry[-5][0]
    if (
        (impulse.close - entry_ema[-5]) * sign <= entry_ema[-5] * p.pullback_tolerance
        or (pullback[-1].close - pullback[0].close) * sign >= 0
        or not any((b.close - b.open) * sign < 0 for b in pullback)
    ):
        return TrendPulseResult(status=TrendPulseStatus.NO_SETUP, reason="pullback_not_confirmed")
    touched = False
    for bar, ema in zip(pullback, entry_ema[-4:-1], strict=True):
        extreme = bar.low if sign == 1 else bar.high
        relative_extreme = (extreme - ema) * sign / ema
        if (
            (bar.close - ema) * sign / ema < -p.pullback_tolerance
            or relative_extreme < -p.maximum_pullback_penetration
        ):
            return TrendPulseResult(status=TrendPulseStatus.NO_SETUP, reason="pullback_too_deep")
        touched |= relative_extreme <= p.pullback_tolerance
    if not touched:
        return TrendPulseResult(
            status=TrendPulseStatus.NO_SETUP, reason="pullback_did_not_touch_ema20"
        )
    threshold = max(b.high for b in pullback) if sign == 1 else min(b.low for b in pullback)
    if (
        (trigger.close - trigger.open) * sign <= 0
        or (trigger.close - threshold) * sign <= 0
        or (trigger.close - entry_ema[-1]) * sign <= 0
    ):
        return TrendPulseResult(status=TrendPulseStatus.NO_SETUP, reason="continuation_not_closed")
    if (
        abs(trigger.open - pullback[-1].close) / pullback[-1].close
        > p.maximum_trigger_open_gap_ratio
    ):
        raise _RefusalError("trigger_open_gap_exceeded")
    prices = _geometry(trigger, pullback, spec, rules)
    spec_hash = exact_hash(spec)
    signal_id = uuid5(
        TRENDPULSE_NAMESPACE,
        exact_hash(
            {
                "adapter_version": TRENDPULSE_ADAPTER_VERSION,
                "spec_hash": spec_hash,
                "instrument": identity.instrument.model_dump(),
                "trigger_event_id": trigger.source_event_id,
            }
        ),
    )
    if signal_id in seen:
        return TrendPulseResult(
            status=TrendPulseStatus.DUPLICATE,
            reason="signal_already_seen",
            duplicate_signal_id=signal_id,
        )
    refs = tuple(
        TrendPulseEvidenceReference(
            timeframe=bar.timeframe,
            observation_id=obs.observation_id,
            payload_hash=bar.content_hash,
            observation_hash=obs.content_hash,
            available_at=available_at(obs),
        )
        for bar, obs in trend + entry
    )
    signal = TrendPulseSignal(
        signal_id=signal_id,
        content_hash="0" * 64,
        spec_hash=spec_hash,
        instrument_rules_hash=exact_hash(rules),
        identity=entry[0][1].identity,
        direction=spec.direction,
        trigger_event_id=trigger.source_event_id,
        trigger_end=trigger_end,
        known_at=max(ref.available_at for ref in refs),
        decision_at=evaluated_at,
        expires_at=expires_at,
        trend_end=trend_end,
        trend_ema20=fast[-1],
        trend_ema50=slow[-1],
        ema50_slope_ratio=slope,
        structure_anchor_ids=anchors,
        entry_ema20=entry_ema[-1],
        pullback_event_ids=tuple(b.source_event_id for b in pullback),
        structural_extreme=prices[3],
        entry=prices[0],
        structural_stop=prices[1],
        target=prices[2],
        evidence=refs,
    )
    digest = exact_hash(signal.model_dump(exclude={"content_hash"}))
    return TrendPulseResult(
        status=TrendPulseStatus.QUALIFIED,
        reason="closed_trend_pullback_continuation",
        signal=signal.model_copy(update={"content_hash": digest}),
    )
