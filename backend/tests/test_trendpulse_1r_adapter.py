"""Independent synthetic research paths. No providers, accounts, workers or orders."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest
from pydantic import ValidationError

from app.market_contracts.enums import Finality, FreshnessState
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import interval_timedelta
from app.market_contracts.observation import observation_from_ohlcv
from app.market_contracts.ohlcv import build_ohlcv_bar
from app.schemas.common import Timeframe, TradeDirection
from app.schemas.trade_plan import InstrumentRules
from app.strategy_brain.trendpulse_1r.adapter import evaluate_trendpulse
from app.strategy_brain.trendpulse_1r.contracts import (
    TrendPulseParameters,
    TrendPulseSpec,
    TrendPulseStatus,
)

END = datetime(2026, 10, 10, 12, 5, tzinfo=UTC)


def spec(short=False):
    return TrendPulseSpec(
        symbol="BTCUSDT", direction=TradeDirection.SHORT if short else TradeDirection.LONG
    )


def rules(**changes):
    return InstrumentRules.model_validate(
        {
            "contract_multiplier": "1",
            "contract_type": "LINEAR",
            "base_currency": "BTC",
            "quote_currency": "USDT",
            "settlement_currency": "USDT",
            "tick_size": "0.01",
            "lot_size": "0.001",
            "minimum_quantity": "0.001",
            "minimum_notional": "5",
            "rules_version": "synthetic-btc/v1",
            **changes,
        }
    )


def evidence(prices, timeframe, end, *, short=False, identity=None):
    identity = identity or first_slice_identity(timeframe=timeframe, replay=True)
    delta = interval_timedelta(timeframe)
    start = end - delta * len(prices)
    pairs = []
    for i, row in enumerate(prices):
        o, h, low, c = [Decimal(str(x)) for x in row]
        if short:
            o, h, low, c = 300 - o, 300 - low, 300 - h, 300 - c
        bar = build_ohlcv_bar(
            instrument=identity.instrument,
            timeframe=timeframe,
            interval_start=start + delta * i,
            open_=o,
            high=h,
            low=low,
            close=c,
            base_volume=Decimal(100),
            quote_volume=c * 100,
            evaluated_at=start + delta * (i + 1) + timedelta(seconds=2),
            grace=timedelta(0),
            adapter_version=identity.source.adapter_version,
        )
        obs = observation_from_ohlcv(
            bar,
            identity=identity,
            observed_at=bar.interval_end + timedelta(seconds=2),
            receive_time=bar.interval_end + timedelta(seconds=3),
            freshness_state=FreshnessState.FRESH,
        ).model_copy(update={"recorded_at": bar.interval_end + timedelta(seconds=4)})
        pairs.append((bar, obs))
    return tuple(b for b, _ in pairs), tuple(o for _, o in pairs)


def path(short=False, end=END, *, trend_step="0.1", swings=True, entry_prices=None):
    wave = ["0", "0.2", "0.4", "0.6", "0.4", "0.2", "0", "-0.2"]
    trend = []
    for i in range(250):
        c = Decimal(100) + Decimal(trend_step) * i + (Decimal(wave[i % 8]) if swings else 0)
        trend.append((c - Decimal("0.02"), c + Decimal("0.1"), c - Decimal("0.1"), c))
    trend_start = end - timedelta(minutes=5)
    trend_end = trend_start.replace(minute=trend_start.minute // 15 * 15, second=0, microsecond=0)
    entry = [("124.5", "124.6", "124.4", "124.5")] * 55 + [
        ("125.2", "125.6", "125.1", "125.5"),
        ("125.2", "125.25", "124.55", "125.0"),
        ("125.0", "125.10", "124.56", "124.95"),
        ("124.95", "125.05", "124.57", "124.90"),
        ("124.95", "125.65", "124.90", "125.60"),
    ]
    tb, to = evidence(trend, Timeframe.M15, trend_end, short=short)
    eb, eo = evidence(entry_prices or entry, Timeframe.M5, end, short=short)
    return {
        "trend_bars": tb,
        "trend_observations": to,
        "entry_bars": eb,
        "entry_observations": eo,
        "instrument_rules": rules(),
        "trigger_end": end,
        "evaluated_at": end + timedelta(seconds=5),
    }


def scan(inputs=None, *, short=False, **changes):
    return evaluate_trendpulse(spec(short), **{**(inputs or path(short)), **changes})


def replace_bar(inputs, stream, index, **changes):
    # Rehash genuine changed canonical evidence; no production detector helpers.
    data = dict(inputs)
    bars, observations = list(data[stream + "_bars"]), list(data[stream + "_observations"])
    bar = with_content_hash(bars[index].model_copy(update=changes))
    old = observations[index]
    bars[index] = bar
    observations[index] = observation_from_ohlcv(
        bar,
        identity=old.identity,
        observed_at=old.observed_at,
        receive_time=old.receive_time,
        freshness_state=old.freshness_state,
    ).model_copy(update={"recorded_at": old.recorded_at})
    data[stream + "_bars"], data[stream + "_observations"] = tuple(bars), tuple(observations)
    return data


@pytest.mark.parametrize(
    "short,expected",
    [
        (False, ("125.60", "124.54", "126.66", "124.55")),
        (True, ("174.40", "175.46", "173.34", "175.45")),
    ],
)
def test_long_and_exact_inverse_short_have_structural_stop_and_one_gross_r(short, expected):
    result = scan(short=short)
    assert result.status is TrendPulseStatus.QUALIFIED, result.reason
    signal = result.signal
    assert signal is not None
    assert (
        signal.entry,
        signal.structural_stop,
        signal.target,
        signal.structural_extreme,
    ) == tuple(Decimal(p) for p in expected)
    assert abs(signal.target - signal.entry) == abs(signal.entry - signal.structural_stop)
    assert signal.gross_reward_risk == 1
    assert len(signal.evidence) == 310
    assert signal.trend_end <= signal.trigger_end - timedelta(minutes=5)
    assert not signal.runtime_activated and not signal.execution_authorized
    assert not signal.management_authority and signal.performance is None


@pytest.mark.parametrize("minute,trend_minute", [(5, 0), (10, 0), (15, 0), (20, 15)])
@pytest.mark.parametrize("short", [False, True])
def test_delayed_arrivals_use_fixed_boundaries_and_post_close_decision(minute, trend_minute, short):
    end = END.replace(minute=minute)
    inputs = path(short, end=end)
    original_arrivals = tuple(
        (o.observed_at, o.receive_time)
        for o in inputs["trend_observations"] + inputs["entry_observations"]
    )
    result = scan(inputs, short=short)
    assert result.status is TrendPulseStatus.QUALIFIED
    assert result.signal.trend_end == end.replace(minute=trend_minute)
    assert result.signal.decision_at == end + timedelta(seconds=5)
    assert result.signal.known_at == end + timedelta(seconds=3)
    assert result.signal.evidence[-2].available_at > end - timedelta(minutes=5)
    assert all(ref.available_at <= result.signal.decision_at for ref in result.signal.evidence)
    assert all(
        o.receive_time > b.interval_end
        for b, o in zip(
            inputs["trend_bars"] + inputs["entry_bars"],
            inputs["trend_observations"] + inputs["entry_observations"],
            strict=True,
        )
    )
    assert original_arrivals == tuple(
        (o.observed_at, o.receive_time)
        for o in inputs["trend_observations"] + inputs["entry_observations"]
    )
    if minute in {5, 20}:
        assert result.signal.evidence[249].available_at > end - timedelta(minutes=5)


@pytest.mark.parametrize("minute", [5, 10, 15])
@pytest.mark.parametrize("short", [False, True])
def test_same_close_and_future_candles_cannot_change_an_earlier_decision(minute, short):
    end = END.replace(minute=minute)
    inputs = path(short, end=end)
    original = scan(inputs, short=short)
    future = evidence([(500, 510, 490, 505)], Timeframe.M15, END.replace(minute=15))
    future_entry = evidence([(500, 510, 490, 505)], Timeframe.M5, end + timedelta(minutes=5))
    result = scan(
        inputs,
        short=short,
        trend_bars=inputs["trend_bars"] + future[0],
        trend_observations=inputs["trend_observations"] + future[1],
        entry_bars=inputs["entry_bars"] + future_entry[0],
        entry_observations=inputs["entry_observations"] + future_entry[1],
    )
    assert result == original


@pytest.mark.parametrize("minute", [5, 10, 15])
@pytest.mark.parametrize("short", [False, True])
@pytest.mark.parametrize("stream,index", [("trend", -1), ("entry", -2), ("entry", -1)])
@pytest.mark.parametrize("clock", ["observed_at", "receive_time"])
def test_receipts_after_decision_cannot_retroactively_confirm(minute, short, stream, index, clock):
    end = END.replace(minute=minute)
    inputs = path(short, end=end)
    observations = list(inputs[stream + "_observations"])
    observations[index] = observations[index].model_copy(update={clock: end + timedelta(seconds=6)})
    inputs = {**inputs, stream + "_observations": tuple(observations)}
    result = scan(inputs, short=short)
    assert result.status is TrendPulseStatus.UNAVAILABLE
    assert result.signal is None and result.reason == "missing_causal_history"
    later = scan(inputs, short=short, evaluated_at=end + timedelta(seconds=7))
    assert later.status is TrendPulseStatus.QUALIFIED
    assert later.signal.known_at == end + timedelta(seconds=6)
    assert later.signal.decision_at == end + timedelta(seconds=7)
    assert scan(inputs, short=short) == result
    assert (
        scan(inputs, short=short, evaluated_at=end + timedelta(seconds=60)).reason
        == "trigger_expired"
    )


@pytest.mark.parametrize("stream,index", [("trend", 0), ("trend", -1), ("entry", 3), ("entry", -1)])
@pytest.mark.parametrize("minute", [5, 10, 15])
@pytest.mark.parametrize("short", [False, True])
def test_missing_history_is_unavailable_instead_of_using_older_context(
    stream, index, minute, short
):
    inputs = path(short, end=END.replace(minute=minute))
    bars, observations = list(inputs[stream + "_bars"]), list(inputs[stream + "_observations"])
    del bars[index], observations[index]
    result = scan(
        inputs,
        short=short,
        **{stream + "_bars": tuple(bars), stream + "_observations": tuple(observations)},
    )
    assert result.status is TrendPulseStatus.UNAVAILABLE
    assert result.reason == "missing_causal_history"


@pytest.mark.parametrize("stream", ["trend", "entry"])
def test_missing_observation_binding_fails_closed(stream):
    inputs = path()
    result = scan(inputs, **{stream + "_observations": inputs[stream + "_observations"][:-1]})
    assert result.status is TrendPulseStatus.UNAVAILABLE
    assert result.reason == "missing_observation_binding"


@pytest.mark.parametrize("change", [{"finality": Finality.FORMING}, {"provider_complete": False}])
def test_forming_or_incomplete_trigger_never_confirms(change):
    result = scan(replace_bar(path(), "entry", -1, **change))
    assert result.status is TrendPulseStatus.UNAVAILABLE
    assert result.reason == "closed_complete_candles_required"


@pytest.mark.parametrize("minute", [5, 10, 15])
@pytest.mark.parametrize("short", [False, True])
def test_trigger_receipt_must_be_known_and_expiry_is_bounded(minute, short):
    end = END.replace(minute=minute)
    inputs = path(short, end=end)
    observations = list(inputs["entry_observations"])
    observations[-1] = observations[-1].model_copy(
        update={"receive_time": end + timedelta(seconds=6)}
    )
    assert (
        scan(inputs, short=short, entry_observations=tuple(observations)).status
        is TrendPulseStatus.UNAVAILABLE
    )
    result = scan(
        inputs,
        short=short,
        entry_observations=tuple(observations),
        evaluated_at=end + timedelta(seconds=6),
    )
    assert result.status is TrendPulseStatus.QUALIFIED
    assert result.signal.known_at == end + timedelta(seconds=6)
    assert (
        scan(inputs, short=short, evaluated_at=end + timedelta(seconds=60)).reason
        == "trigger_expired"
    )
    assert (
        scan(inputs, short=short, evaluated_at=end + timedelta(seconds=59)).status
        is TrendPulseStatus.QUALIFIED
    )
    assert (
        scan(inputs, short=short, evaluated_at=end - timedelta(microseconds=1)).reason
        == "trigger_not_closed"
    )


def test_decision_time_is_hashed_but_retry_keeps_the_same_natural_signal_id():
    first = scan()
    retry = scan(evaluated_at=END + timedelta(seconds=7))
    assert first.signal.adapter_version == "trendpulse-1r-research/v2"
    assert first.signal.signal_id == retry.signal.signal_id
    assert first.signal.content_hash != retry.signal.content_hash
    assert first.signal.evidence == retry.signal.evidence


@pytest.mark.parametrize("stream", ["trend", "entry"])
def test_identical_receipts_and_reordered_input_converge_without_duplicate_signals(stream):
    inputs = path()
    original = scan(inputs)
    bars, observations = inputs[stream + "_bars"], inputs[stream + "_observations"]
    result = scan(
        inputs,
        **{
            stream + "_bars": tuple(reversed(bars + bars[-2:])),
            stream + "_observations": tuple(reversed(observations + observations[-2:])),
        },
    )
    assert result == original
    duplicate = scan(inputs, seen_signal_ids=frozenset({original.signal.signal_id}))
    assert duplicate.status is TrendPulseStatus.DUPLICATE
    assert duplicate.signal is None
    assert duplicate.duplicate_signal_id == original.signal.signal_id


@pytest.mark.parametrize("stream", ["trend", "entry"])
def test_conflicting_duplicate_revision_refuses_an_implicit_replay(stream):
    inputs = path()
    revision = replace_bar(inputs, stream, -1, revision=2)
    result = scan(
        inputs,
        **{
            stream + "_bars": inputs[stream + "_bars"] + revision[stream + "_bars"][-1:],
            stream + "_observations": inputs[stream + "_observations"]
            + revision[stream + "_observations"][-1:],
        },
    )
    assert result.status is TrendPulseStatus.REFUSED
    assert result.reason == "conflicting_duplicate_or_revision"


def test_hash_tampering_and_source_mismatch_are_refused():
    inputs = path()
    observations = list(inputs["trend_observations"])
    observations[-1] = observations[-1].model_copy(update={"payload_content_hash": "a" * 64})
    assert (
        scan(inputs, trend_observations=tuple(observations)).reason == "invalid_canonical_binding"
    )
    wrong = inputs["entry_observations"][0].identity
    wrong = wrong.model_copy(
        update={
            "provenance": wrong.provenance.model_copy(update={"detail": "another feed identity"})
        }
    )
    observations = tuple(
        with_content_hash(o.model_copy(update={"identity": wrong}))
        for o in inputs["entry_observations"]
    )
    assert scan(inputs, entry_observations=observations).reason == "mixed_market_identity"


@pytest.mark.parametrize("short", [False, True])
def test_weak_slope_and_unconfirmed_structure_never_qualify(short):
    assert scan(path(short, trend_step="0.005"), short=short).reason == "trend_not_confirmed"
    assert scan(path(short, swings=False), short=short).reason == "trend_not_confirmed"


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"close": Decimal("125.20"), "high": Decimal("125.65")}, "continuation_not_closed"),
        ({"open": Decimal("125.61")}, "continuation_not_closed"),
    ],
)
def test_wick_break_or_wrong_body_is_not_a_continuation(change, reason):
    assert scan(replace_bar(path(), "entry", -1, **change)).reason == reason


def test_pullback_tolerance_and_direction_are_required():
    assert scan(replace_bar(path(), "entry", -2, low=Decimal("120"))).reason == "pullback_too_deep"
    assert (
        scan(replace_bar(path(), "entry", -2, close=Decimal("125.02"))).reason
        == "pullback_not_confirmed"
    )


@pytest.mark.parametrize("short", [False, True])
def test_outward_stop_and_conservative_entry_rounding_preserve_exact_one_r(short):
    inputs = path(short)
    changes = {"close": Decimal("174.393")} if short else {"close": Decimal("125.607")}
    inputs = replace_bar(inputs, "entry", -1, **changes)
    result = scan(inputs, short=short, instrument_rules=rules(tick_size="0.03"))
    assert result.status is TrendPulseStatus.QUALIFIED, result.reason
    signal = result.signal
    assert all(
        price % Decimal("0.03") == 0
        for price in (signal.entry, signal.structural_stop, signal.target)
    )
    assert abs(signal.target - signal.entry) == abs(signal.entry - signal.structural_stop)
    if short:
        assert signal.entry <= changes["close"]
        assert signal.structural_stop > signal.structural_extreme
    else:
        assert signal.entry >= changes["close"]
        assert signal.structural_stop < signal.structural_extreme


@pytest.mark.parametrize("tick", ["1", "10", "200"])
def test_precision_that_cannot_preserve_risk_is_refused(tick):
    result = scan(instrument_rules=rules(tick_size=tick))
    assert result.status is TrendPulseStatus.REFUSED
    assert result.reason == "invalid_rounded_risk_geometry"


@pytest.mark.parametrize(
    "change", [{"base_currency": "ETH"}, {"contract_type": "INVERSE"}, {"contract_multiplier": "2"}]
)
def test_mismatched_instrument_rules_cannot_supply_executable_geometry(change):
    assert scan(instrument_rules=rules(**change)).reason == "instrument_rules_mismatch"


def test_numeric_bounds_and_input_bounds_are_explicit():
    assert (
        scan(instrument_rules=rules(tick_size="0.0000000000001")).reason
        == "numeric_precision_unsupported"
    )
    inputs = path()
    assert (
        scan(
            inputs,
            entry_bars=inputs["entry_bars"] * 18,
            entry_observations=inputs["entry_observations"] * 18,
        ).reason
        == "input_bound_exceeded"
    )


def test_ambient_decimal_precision_rounding_and_replay_do_not_change_result():
    inputs = path()
    original = scan(inputs)
    with localcontext() as context:
        context.prec, context.rounding = 6, ROUND_DOWN
        replay = scan(inputs)
    assert replay == original


@pytest.mark.parametrize(
    "change",
    [
        {"minimum_slope_ratio": "0.0001"},
        {"fast_ema_period": 10},
        {"trigger": "model_entry"},
        {"pullback_tolerance": 0.001},
    ],
)
def test_rule_changes_or_float_inputs_require_a_new_version(change):
    with pytest.raises(ValidationError):
        TrendPulseParameters.model_validate(change)


def test_naive_decision_clock_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        scan(evaluated_at=END.replace(tzinfo=None))


@pytest.mark.parametrize("short", [False, True])
def test_trigger_gap_is_refused_before_a_research_plan_can_be_formed(short):
    change = {"open": Decimal("174.50")} if short else {"open": Decimal("125.50")}
    result = scan(replace_bar(path(short), "entry", -1, **change), short=short)
    assert result.status is TrendPulseStatus.REFUSED
    assert result.reason == "trigger_open_gap_exceeded"


@pytest.mark.parametrize("short", [False, True])
def test_excessive_stop_distance_is_invalid_risk_even_with_a_closed_break(short):
    change = (
        {"close": Decimal("170"), "low": Decimal("169.9")}
        if short
        else {"close": Decimal("130"), "high": Decimal("130.1")}
    )
    result = scan(replace_bar(path(short), "entry", -1, **change), short=short)
    assert result.status is TrendPulseStatus.REFUSED
    assert result.reason == "invalid_rounded_risk_geometry"


@pytest.mark.parametrize("change", [{"tick_size": "0"}, {"tick_size": "-1"}, {"tick_size": 0.1}])
def test_invalid_tick_risk_inputs_are_rejected_by_the_existing_precision_contract(change):
    with pytest.raises(ValidationError):
        rules(**change)


@pytest.mark.parametrize("short", [False, True])
def test_entry_price_must_still_be_on_the_trend_side_of_ema50(short):
    inputs = path()
    prices = [
        tuple(value - Decimal(5) for value in (b.open, b.high, b.low, b.close))
        for b in inputs["entry_bars"]
    ]
    eb, eo = evidence(prices, Timeframe.M5, END, short=short)
    inputs = {**path(short), "entry_bars": eb, "entry_observations": eo}
    assert scan(inputs, short=short).reason == "trend_not_confirmed"


def test_equal_pivot_wings_are_not_confirmed_bullish_structure():
    inputs = path()
    result = scan(replace_bar(inputs, "trend", -6, high=inputs["trend_bars"][-7].high))
    assert result.reason == "trend_not_confirmed"


def test_pullback_that_never_touches_the_tolerance_band_does_not_qualify():
    inputs = path()
    for index in (-4, -3, -2):
        inputs = replace_bar(inputs, "entry", index, low=inputs["entry_bars"][index].close)
    assert scan(inputs).reason == "pullback_did_not_touch_ema20"


def test_unaligned_entry_timestamp_is_refused():
    assert scan(path(end=END + timedelta(seconds=1))).reason == "unaligned_candle"


def test_serializable_pure_request_roundtrips_canonical_evidence():
    from app.strategy_brain.trendpulse_1r.contracts import TrendPulseRequest

    inputs = path()
    request = TrendPulseRequest(spec=spec(), **inputs)
    request = TrendPulseRequest.model_validate_json(request.model_dump_json())
    args = {key: getattr(request, key) for key in TrendPulseRequest.model_fields if key != "spec"}
    assert evaluate_trendpulse(request.spec, **args) == scan(inputs)
