"""Synthetic SFP research scenarios; canonical fixture evidence and no providers."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.market_contracts.enums import Finality, FreshnessState
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.hashing import with_content_hash
from app.market_contracts.identity import binance_usdm_perpetual, interval_timedelta
from app.market_contracts.observation import observation_from_ohlcv
from app.market_contracts.ohlcv import build_ohlcv_bar
from app.schemas.common import Timeframe, TradeDirection
from app.schemas.nested_continuation import BrainSetupState, EvidenceAvailability
from app.strategy_brain.sfp.contracts import (
    LevelKind,
    QualityComponent,
    SfpCondition,
    SfpParameters,
    SfpSpec,
    StructuralLevel,
    SweepDirection,
)
from app.strategy_brain.sfp.detector import detect_sfp
from app.strategy_brain.sfp.levels import derive_levels

START = datetime(2026, 10, 1, 10, tzinfo=UTC)
# Independent explicit price paths, ordered open/high/low/close.
BULL = [
    (102, 104, 101, 103),
    (101, 102, 100, 101),
    (102, 104, 101, 103),
    (103, 105, 102, 104),
    (102, 103, 98, 101),
    (101, 105, "100.5", 104),
]
BEAR = [
    (98, 99, 96, 97),
    (99, 100, 98, 99),
    (98, 99, 96, 97),
    (97, 98, 95, 96),
    (98, 102, 97, 99),
    (99, "99.5", 95, 96),
]


def parameters(**changes):
    return SfpParameters.model_validate(
        {
            "level_lookback": 6,
            "pivot_width": 1,
            "minimum_level_significance": "2",
            "minimum_sweep_depth": "0.01",
            "maximum_sweep_depth": "0.05",
            "equal_level_tolerance": "0.001",
            "reclaim_window": 2,
            "confirmation_window": 2,
            "breakout_confirmation_closes": 2,
            "structural_invalidation_buffer": "0.005",
            "expiry_bars": 6,
            "required_evidence_max_age_bars": 1,
            "quality_lookback": 3,
            "htf_alignment_tolerance": "0.01",
            **changes,
        }
    )


def spec(*, bearish=False, **changes):
    return SfpSpec(
        symbol="BTCUSDT",
        trigger_timeframe=Timeframe.M15,
        direction=TradeDirection.SHORT if bearish else TradeDirection.LONG,
        parameters=parameters(**changes),
    )


def evidence(
    prices=BULL,
    *,
    start=START,
    timeframe=Timeframe.M15,
    volumes=None,
    forming=False,
    market_identity=None,
):
    identity = market_identity or first_slice_identity(timeframe=timeframe, replay=True)
    delta = interval_timedelta(timeframe)
    bars, observations = [], []
    for i, row in enumerate(prices):
        opening = start + delta * i
        observed = (
            opening + delta if not (forming and i == len(prices) - 1) else opening + delta / 2
        )
        o, h, low, c = [Decimal(str(value)) for value in row]
        volume = Decimal(str(volumes[i])) if volumes else Decimal(100)
        bar = build_ohlcv_bar(
            instrument=identity.instrument,
            timeframe=timeframe,
            interval_start=opening,
            open_=o,
            high=h,
            low=low,
            close=c,
            base_volume=volume,
            quote_volume=volume * c,
            evaluated_at=observed,
            grace=timedelta(0),
            adapter_version=identity.source.adapter_version,
        )
        bars.append(bar)
        observations.append(
            observation_from_ohlcv(
                bar,
                identity=identity,
                observed_at=observed,
                receive_time=observed,
                freshness_state=FreshnessState.FRESH,
            ).model_copy(update={"recorded_at": observed})
        )
    return tuple(bars), tuple(observations)


def scan(prices=BULL, *, bearish=False, config=None, **kwargs):
    bars, observations = evidence(prices, **kwargs)
    return detect_sfp(
        bars,
        observations,
        config or spec(bearish=bearish),
        evaluated_at=observations[-1].observed_at,
    )


def at_level(result, price="100"):
    return [e for e in result.events if e.sweep.reference_level.price == Decimal(price)]


def test_bullish_valid_sfp_has_a_separate_closed_reclaim_and_confirmation():
    events = at_level(scan())
    assert [e.state for e in events] == [BrainSetupState.FORMING, BrainSetupState.CONFIRMED]
    assert [e.condition for e in events] == [
        SfpCondition.CONFIRMED_RECLAIM,
        SfpCondition.CONFIRMED_SFP,
    ]
    forming, confirmed = events
    assert forming.setup_id == confirmed.setup_id
    assert confirmed.direction is TradeDirection.LONG
    assert confirmed.sweep.direction is SweepDirection.BELOW
    assert confirmed.sweep.depth == Decimal(2)
    assert confirmed.sweep.extreme == Decimal(98)
    assert confirmed.sweep.depth_ratio == Decimal("0.02")
    assert confirmed.sweep.candle_closed and not confirmed.provisional
    assert forming.confirmation_observation_id is None
    assert confirmed.confirmation_observation_id == confirmed.evidence.observation_id
    assert confirmed.sweep.reference_level.known_at <= confirmed.sweep.event_time
    assert confirmed.sweep.event_time == START + timedelta(minutes=60)
    assert confirmed.sweep.candle_end == START + timedelta(minutes=75)
    assert confirmed.sweep.observed_at == confirmed.sweep.candle_end
    assert confirmed.quality.level_importance.unit == "structural_points"


def test_bearish_valid_sfp_uses_the_same_model_with_independent_prices():
    events = at_level(scan(BEAR, bearish=True))
    assert [e.state for e in events] == [BrainSetupState.FORMING, BrainSetupState.CONFIRMED]
    assert events[-1].condition is SfpCondition.CONFIRMED_SFP
    assert events[-1].direction is TradeDirection.SHORT
    assert events[-1].sweep.direction is SweepDirection.ABOVE
    assert events[-1].sweep.extreme == Decimal(102)
    assert events[-1].sweep.depth_ratio == Decimal("0.02")
    assert events[0].reclaim_observation_id == events[0].evidence.observation_id


@pytest.mark.parametrize("bearish", [False, True])
def test_forming_wick_is_not_a_closed_reclaim_even_when_price_is_back_inside(bearish):
    prices = BEAR if bearish else BULL
    result = scan(prices[:5], bearish=bearish, forming=True)
    event = at_level(result)[-1]
    assert event.state is BrainSetupState.FORMING
    assert event.condition is SfpCondition.WICK_THROUGH
    assert event.provisional and not event.sweep.candle_closed
    assert event.reclaim_observation_id is None
    assert event.sweep.finality is Finality.FORMING


def test_wick_touching_the_level_at_close_has_no_reclaim():
    prices = [*BULL[:4], (102, 103, 98, 100)]
    event = at_level(scan(prices))[-1]
    assert event.condition is SfpCondition.WICK_THROUGH
    assert event.reclaim_observation_id is None
    assert event.state is BrainSetupState.FORMING


@pytest.mark.parametrize("bearish", [False, True])
def test_temporary_excursion_can_reclaim_within_the_window(bearish):
    prices = (
        [*BEAR[:4], (98, 102, 97, 101), (101, "101.5", 98, 99), (99, 100, 96, 97)]
        if bearish
        else [*BULL[:4], (102, 103, 98, 99), (99, 102, "98.5", 101), (101, 104, 100, 103)]
    )
    events = at_level(scan(prices, bearish=bearish))
    assert [e.condition for e in events] == [
        SfpCondition.TEMPORARY_EXCURSION,
        SfpCondition.CONFIRMED_RECLAIM,
        SfpCondition.CONFIRMED_SFP,
    ]
    assert events[-1].quality.reclaim_speed.value == Decimal(1)


@pytest.mark.parametrize("bearish", [False, True])
def test_sustained_breakout_is_invalidated_not_an_sfp(bearish):
    prices = (
        [*BEAR[:4], (98, 102, 97, 101), (101, 102, 100, "101.5")]
        if bearish
        else [*BULL[:4], (102, 103, 98, 99), (99, 100, 98, "98.5")]
    )
    events = at_level(scan(prices, bearish=bearish))
    assert events[0].condition is SfpCondition.TEMPORARY_EXCURSION
    assert events[-1].condition is SfpCondition.SUCCESSFUL_BREAKOUT
    assert events[-1].state is BrainSetupState.INVALIDATED
    assert not any(e.state is BrainSetupState.CONFIRMED for e in events)


@pytest.mark.parametrize("bearish", [False, True])
def test_failed_reclaim_is_explicit_and_terminal(bearish):
    prices = (
        [*BEAR[:5], (99, "101.5", 98, 101), (101, "101.5", 94, 95)]
        if bearish
        else [*BULL[:5], (101, 102, "98.5", 99), (99, 106, 99, 105)]
    )
    events = at_level(scan(prices, bearish=bearish))
    assert events[-1].condition is SfpCondition.FAILED_RECLAIM
    assert events[-1].state is BrainSetupState.INVALIDATED
    assert not any(e.state is BrainSetupState.CONFIRMED for e in events)


@pytest.mark.parametrize("bearish", [False, True])
def test_confirmed_sfp_can_be_structurally_invalidated(bearish):
    prices = [*BEAR, (96, 103, 95, 97)] if bearish else [*BULL, (104, 105, 97, 103)]
    events = at_level(scan(prices, bearish=bearish))
    assert events[-2].state is BrainSetupState.CONFIRMED
    assert events[-1].state is BrainSetupState.INVALIDATED
    assert events[-1].condition is SfpCondition.INVALIDATED_SFP
    assert events[-1].setup_id == events[-2].setup_id


def test_forming_structural_breach_does_not_invalidate_closed_history():
    prices = [*BULL, (104, 105, 97, 103)]
    event = at_level(scan(prices, forming=True))[-1]
    assert event.state is BrainSetupState.CONFIRMED
    assert event.provisional
    assert event.reason_codes == ("provisional_candle_cannot_confirm_or_invalidate",)


@pytest.mark.parametrize("bearish", [False, True])
def test_confirmation_window_expiry_cannot_resume(bearish):
    prices = (
        [*BEAR[:5], (99, 100, 98, 99), (99, 100, 98, 99), (99, 100, 94, 95)]
        if bearish
        else [*BULL[:5], (101, 102, 100, 101), (101, 102, 100, 101), (101, 106, 100, 105)]
    )
    events = at_level(
        scan(prices, bearish=bearish, config=spec(bearish=bearish, confirmation_window=1))
    )
    assert events[-1].state is BrainSetupState.EXPIRED
    assert events[-1].reason_codes == ("confirmation_window_elapsed",)
    assert not any(e.state is BrainSetupState.CONFIRMED for e in events)


def test_setup_expiry_wins_over_confirmation_at_its_exact_deadline():
    events = at_level(scan(config=spec(expiry_bars=1)))
    assert events[-1].state is BrainSetupState.EXPIRED
    assert events[-1].event_time == events[-1].expires_at
    assert not any(e.state is BrainSetupState.CONFIRMED for e in events)


def test_confirmed_setup_expires_after_its_fixed_sweep_deadline():
    prices = [*BULL, (104, 105, 103, 104), (104, 105, 103, 104)]
    events = at_level(scan(prices, config=spec(expiry_bars=3)))
    assert any(e.state is BrainSetupState.CONFIRMED for e in events)
    assert events[-1].state is BrainSetupState.EXPIRED
    assert events[0].expires_at == events[-1].expires_at


def test_exact_duplicates_and_restart_replay_converge_event_and_setup_ids():
    bars, observations = evidence()
    config = spec()
    original = detect_sfp(bars, observations, config, evaluated_at=bars[-1].interval_end)
    duplicate = detect_sfp(
        (*bars, bars[4], bars[5]),
        (*observations, observations[4], observations[5]),
        config,
        evaluated_at=bars[-1].interval_end,
    )
    assert duplicate == original
    replay = detect_sfp(bars, observations, config, evaluated_at=bars[-1].interval_end)
    assert replay == original
    assert len({e.event_id for e in original.events}) == len(original.events)


def test_candle_revision_conflicts_fail_closed():
    bars, observations = evidence()
    revised = with_content_hash(bars[-1].model_copy(update={"revision": 2}))
    obs = observation_from_ohlcv(
        revised,
        identity=observations[-1].identity,
        observed_at=revised.interval_end,
        receive_time=revised.interval_end,
        freshness_state=FreshnessState.FRESH,
    )
    with pytest.raises(ValueError, match="Conflicting candle revisions"):
        detect_sfp(
            (*bars, revised), (*observations, obs), spec(), evaluated_at=revised.interval_end
        )


def test_closed_candle_prefix_causality_and_future_observation_filtering():
    bars, observations = evidence()
    full = detect_sfp(bars, observations, spec(), evaluated_at=bars[-1].interval_end)
    for count in range(1, len(bars) + 1):
        asof = bars[count - 1].interval_end
        prefix = detect_sfp(bars[:count], observations[:count], spec(), evaluated_at=asof)
        with_future = detect_sfp(bars, observations, spec(), evaluated_at=asof)
        assert prefix == with_future
        assert prefix.events == tuple(e for e in full.events if e.observed_at <= asof)
        assert all(e.event_time <= e.observed_at <= asof for e in prefix.events)
    assert not any(
        e.state is BrainSetupState.CONFIRMED
        for e in detect_sfp(bars, observations, spec(), evaluated_at=bars[-1].interval_start).events
    )


def test_a_pivot_cannot_be_used_before_its_right_wing_closes():
    prices = [
        (102, 104, 101, 103),
        (101, 103, 100, 101),
        (102, 104, 101, 103),
        (103, 104, 98, 101),
        (101, 106, 100, 105),
    ]
    result = scan(prices, config=spec(pivot_width=2))
    assert not at_level(result)
    assert not any(e.state is BrainSetupState.CONFIRMED for e in result.events)


def test_observation_delay_is_preserved_and_not_backdated():
    bars, observations = evidence()
    delayed = observations[4].model_copy(
        update={
            "observed_at": bars[4].interval_end + timedelta(seconds=5),
            "receive_time": bars[4].interval_end + timedelta(seconds=5),
        }
    )
    observations = (*observations[:4], delayed, observations[5])
    result = detect_sfp(bars, observations, spec(), evaluated_at=bars[-1].interval_end)
    event = at_level(result)[0]
    assert event.sweep.observed_at == delayed.observed_at
    assert event.event_time == bars[4].interval_end
    assert event.observed_at > event.event_time


def test_missing_optional_evidence_is_explicit_and_never_fabricated():
    event = at_level(scan(volumes=[0] * len(BULL)))[-1]
    assert event.state is BrainSetupState.CONFIRMED
    quality = event.quality
    for component in [quality.higher_timeframe_alignment, quality.volume]:
        assert component.availability is EvidenceAvailability.MISSING
        assert component.value is None
    for component in [quality.cvd, quality.order_flow, quality.open_interest]:
        assert component.availability is EvidenceAvailability.UNSUPPORTED
        assert component.value is None and not component.observation_ids
    assert quality.market_regime.availability is EvidenceAvailability.AVAILABLE
    assert "proxy" in quality.market_regime.reason
    with pytest.raises(ValidationError, match="fabricated"):
        QualityComponent(
            availability=EvidenceAvailability.MISSING, value="0.8", unit="ratio", reason="missing"
        )


@pytest.mark.parametrize("cause", ["age", "envelope", "arrival"])
def test_stale_required_evidence_cannot_confirm(cause):
    bars, observations = evidence()
    asof = bars[-1].interval_end
    if cause == "age":
        asof += timedelta(minutes=15)
    elif cause == "envelope":
        observations = (
            *observations[:-1],
            with_content_hash(
                observations[-1].model_copy(update={"freshness_state": FreshnessState.STALE})
            ),
        )
    else:
        asof += timedelta(minutes=15)
        observations = (
            *observations[:-1],
            observations[-1].model_copy(update={"observed_at": asof, "receive_time": asof}),
        )
    result = detect_sfp(bars, observations, spec(), evaluated_at=asof)
    assert result.required_evidence is EvidenceAvailability.STALE
    assert not result.events


def test_missing_required_and_gapped_evidence_fail_closed():
    assert (
        detect_sfp((), (), spec(), evaluated_at=START).required_evidence
        is EvidenceAvailability.MISSING
    )
    bars, observations = evidence()
    assert (
        detect_sfp(bars, (), spec(), evaluated_at=bars[-1].interval_end).required_evidence
        is EvidenceAvailability.MISSING
    )
    result = detect_sfp(
        (*bars[:2], *bars[3:]),
        (*observations[:2], *observations[3:]),
        spec(),
        evaluated_at=bars[-1].interval_end,
    )
    assert result.required_evidence is EvidenceAvailability.INCOMPLETE
    assert not result.events


@pytest.mark.parametrize(
    "field,value", [("minimum_sweep_depth", "0.03"), ("minimum_level_significance", "100")]
)
def test_minimum_thresholds_filter_insignificant_or_shallow_events(field, value):
    assert not at_level(scan(config=spec(**{field: value})))


@pytest.mark.parametrize("bearish", [False, True])
def test_sweep_depth_inclusive_boundaries_and_optional_maximum(bearish):
    prices = BEAR if bearish else BULL
    for maximum in ["0.02", None]:
        events = at_level(
            scan(
                prices,
                bearish=bearish,
                config=spec(
                    bearish=bearish, minimum_sweep_depth="0.02", maximum_sweep_depth=maximum
                ),
            )
        )
        assert events[-1].state is BrainSetupState.CONFIRMED
    event = at_level(
        scan(prices, bearish=bearish, config=spec(bearish=bearish, maximum_sweep_depth="0.019"))
    )[0]
    assert event.state is BrainSetupState.INVALIDATED
    assert event.reason_codes == ("maximum_sweep_depth_exceeded",)


def test_touch_without_crossing_is_not_a_sweep_even_with_zero_minimum():
    result = scan([*BULL[:4], (102, 103, 100, 101)], config=spec(minimum_sweep_depth="0"))
    assert not at_level(result)


def test_same_candle_reclaim_is_allowed_at_zero_reclaim_window():
    assert at_level(scan(config=spec(reclaim_window=0)))[-1].state is BrainSetupState.CONFIRMED
    prices = [*BULL[:4], (102, 103, 98, 99), (99, 102, "98.5", 101)]
    assert at_level(scan(prices, config=spec(reclaim_window=0)))[-1].reason_codes == (
        "reclaim_window_elapsed",
    )


def test_confirmation_exact_window_boundary_is_allowed():
    events = at_level(scan(config=spec(confirmation_window=1)))
    assert events[-1].state is BrainSetupState.CONFIRMED


def test_structural_buffer_exact_boundary_is_intact_then_breach_invalidates():
    intact = at_level(scan([*BULL, (104, 105, "97.5", 103)]))[-1]
    assert intact.state is BrainSetupState.CONFIRMED
    breached = at_level(scan([*BULL, (104, 105, "97.49", 103)]))[-1]
    assert breached.state is BrainSetupState.INVALIDATED


@pytest.mark.parametrize(
    "changes",
    [
        {"pivot_width": 0},
        {"pivot_width": 3},
        {"level_lookback": 2},
        {"minimum_sweep_depth": "-0.1"},
        {"maximum_sweep_depth": "0.001"},
        {"confirmation_window": 0},
        {"reclaim_window": -1},
        {"expiry_bars": 0},
        {"structural_invalidation_buffer": "1"},
        {"minimum_level_significance": True},
        {"minimum_sweep_depth": 0.02},
        {"maximum_sweep_depth": "NaN"},
        {"version": "sfp-research/v9"},
        {"breakout_confirmation_closes": 0},
    ],
)
def test_invalid_parameter_boundaries_are_rejected(changes):
    with pytest.raises(ValidationError):
        parameters(**changes)


def test_versioned_authored_config_changes_detection_identity():
    first = at_level(scan())[-1]
    changed = at_level(scan(config=spec(confirmation_window=3)))[-1]
    assert first.spec_hash != changed.spec_hash
    assert first.setup_id != changed.setup_id
    config = spec()
    assert SfpSpec.model_validate_json(config.model_dump_json()) == config
    with pytest.raises(ValidationError):
        config.parameters.reclaim_window = 100


@pytest.mark.parametrize("side", ["high", "low"])
def test_causal_structural_representation_supports_swings_ranges_and_equal_levels(side):
    prices = [
        (102, 104, 101, 103),
        (101, 105, 100, 102),
        (102, 104, 101, 103),
        (101, 105, 100, 102),
        (102, 104, 101, 103),
        (102, 104, 101, 103),
    ]
    bars, observations = evidence(prices)
    levels = derive_levels(bars, observations, parameters(), evaluated_at=bars[-1].interval_end)
    kinds = {level.kind for level in levels}
    expected = (
        {LevelKind.SWING_HIGH, LevelKind.RANGE_HIGH, LevelKind.EQUAL_HIGHS}
        if side == "high"
        else {LevelKind.SWING_LOW, LevelKind.RANGE_LOW, LevelKind.EQUAL_LOWS}
    )
    assert expected <= kinds
    for level in levels:
        assert level.known_at >= level.basis_bars[-1].interval_end
        assert level.significance.touches >= 1
        assert level.significance.points >= Decimal(level.significance.touches)
        assert (
            level.level_id == StructuralLevel.model_validate_json(level.model_dump_json()).level_id
        )


def test_htf_context_requires_real_closed_proof_and_known_time():
    htf_bars, htf_obs = evidence(BULL[:4], start=START - timedelta(hours=5), timeframe=Timeframe.H1)
    context = derive_levels(
        htf_bars, htf_obs, parameters(), evaluated_at=START, higher_timeframe=True
    )
    assert LevelKind.HTF_SUPPORT in {level.kind for level in context}
    bars, observations = evidence()
    result = detect_sfp(
        bars, observations, spec(), evaluated_at=bars[-1].interval_end, context_levels=context
    )
    event = at_level(result)[-1]
    assert event.quality.higher_timeframe_alignment.availability is EvidenceAvailability.AVAILABLE
    assert event.quality.higher_timeframe_alignment.observation_ids
    with pytest.raises(ValidationError, match="match their evidence"):
        StructuralLevel.model_validate({**context[0].model_dump(), "price": "12345"})


def test_future_htf_proof_does_not_change_past_setup_quality():
    htf_bars, htf_obs = evidence(BULL[:4], start=START - timedelta(hours=1), timeframe=Timeframe.H1)
    context = derive_levels(
        htf_bars,
        htf_obs,
        parameters(),
        evaluated_at=START + timedelta(hours=4),
        higher_timeframe=True,
    )
    bars, observations = evidence()
    result = detect_sfp(
        bars, observations, spec(), evaluated_at=bars[-1].interval_end, context_levels=context
    )
    assert (
        at_level(result)[-1].quality.higher_timeframe_alignment.availability
        is EvidenceAvailability.MISSING
    )


@pytest.mark.parametrize("mutation", ["payload", "bar", "instrument", "future_close"])
def test_unbound_or_impossible_required_evidence_is_rejected(mutation):
    bars, observations = evidence()
    if mutation == "bar":
        bars = (*bars[:-1], bars[-1].model_copy(update={"close": Decimal("103.5")}))
    elif mutation == "payload":
        observations = (
            *observations[:-1],
            with_content_hash(
                observations[-1].model_copy(update={"payload_content_hash": "f" * 64})
            ),
        )
    elif mutation == "instrument":
        bars = (
            *bars[:-1],
            with_content_hash(
                bars[-1].model_copy(update={"instrument": binance_usdm_perpetual("ETHUSDT")})
            ),
        )
    else:
        observations = (
            *observations[:-1],
            observations[-1].model_copy(
                update={
                    "observed_at": bars[-1].interval_start,
                    "receive_time": bars[-1].interval_start,
                }
            ),
        )
    with pytest.raises(ValueError):
        detect_sfp(bars, observations, spec(), evaluated_at=START + timedelta(hours=2))


def test_reclaim_quality_and_evidence_remain_separate_from_win_probability():
    event = at_level(scan())[-1]
    assert event.quality.reclaim_speed.value == Decimal(0)
    assert event.quality.volume.value == Decimal(1)
    assert event.quality.available_target_space.value == Decimal(1)
    assert not hasattr(event.quality, "score")
    assert not hasattr(event, "candidate_id")
    assert event.quality.rejection_strength.value == Decimal("0.6")
    assert event.quality.rejection_strength.observation_ids == (event.reclaim_observation_id,)


@pytest.mark.parametrize("bearish", [False, True])
def test_provisional_confirmation_candle_cannot_confirm(bearish):
    result = scan(BEAR if bearish else BULL, bearish=bearish, forming=True)
    event = at_level(result)[-1]
    assert event.state is BrainSetupState.FORMING
    assert event.condition is SfpCondition.CONFIRMED_RECLAIM
    assert event.provisional and event.confirmation_observation_id is None


def test_incomplete_provider_candle_cannot_confirm():
    bars, observations = evidence()
    incomplete = with_content_hash(bars[-1].model_copy(update={"provider_complete": False}))
    observation = observation_from_ohlcv(
        incomplete,
        identity=observations[-1].identity,
        observed_at=incomplete.interval_end,
        receive_time=incomplete.interval_end,
        freshness_state=FreshnessState.FRESH,
    )
    result = detect_sfp(
        (*bars[:-1], incomplete),
        (*observations[:-1], observation),
        spec(),
        evaluated_at=incomplete.interval_end,
    )
    event = at_level(result)[-1]
    assert event.provisional and event.state is BrainSetupState.FORMING
    assert event.confirmation_observation_id is None


def test_stale_optional_htf_context_cannot_become_required_reference_or_alignment():
    htf_bars, htf_obs = evidence(BULL[:4], start=START - timedelta(hours=5), timeframe=Timeframe.H1)
    stale = tuple(
        with_content_hash(o.model_copy(update={"freshness_state": FreshnessState.STALE}))
        for o in htf_obs
    )
    context = derive_levels(
        htf_bars, stale, parameters(), evaluated_at=START, higher_timeframe=True
    )
    bars, observations = evidence()
    result = detect_sfp(
        bars, observations, spec(), evaluated_at=bars[-1].interval_end, context_levels=context
    )
    event = at_level(result)[-1]
    assert event.state is BrainSetupState.CONFIRMED
    assert event.sweep.reference_level.kind is LevelKind.SWING_LOW
    assert event.quality.higher_timeframe_alignment.availability is EvidenceAvailability.STALE
    assert event.quality.higher_timeframe_alignment.value is None


def test_htf_label_requires_an_actual_higher_timeframe():
    bars, observations = evidence()
    context = derive_levels(
        bars, observations, parameters(), evaluated_at=bars[-1].interval_end, higher_timeframe=True
    )
    with pytest.raises(ValueError, match="actual higher timeframe"):
        detect_sfp(
            bars, observations, spec(), evaluated_at=bars[-1].interval_end, context_levels=context
        )


def test_same_price_swing_range_and_equal_aliases_create_one_episode():
    prices = [
        (102, 104, 101, 103),
        (101, 105, 100, 102),
        (102, 104, 101, 103),
        (101, 105, 100, 102),
        (102, 104, 101, 103),
        (102, 104, 101, 103),
        (102, 104, 98, 101),
        (101, 107, 101, 106),
    ]
    events = at_level(scan(prices))
    assert len({event.setup_id for event in events}) == 1
    assert events[0].sweep.reference_level.kind is LevelKind.EQUAL_LOWS
    assert [event.state for event in events] == [BrainSetupState.FORMING, BrainSetupState.CONFIRMED]


def test_new_structure_after_terminal_failure_allows_a_distinct_same_price_episode():
    prices = [
        *BULL,
        (104, 104, "98.5", 99),
        (101, 104, 101, 103),
        (102, 103, 100, 102),
        (103, 105, 102, 104),
        (102, 103, 98, 101),
        (101, 105, "100.5", 104),
    ]
    events = at_level(scan(prices))
    confirmed = [event for event in events if event.condition is SfpCondition.CONFIRMED_SFP]
    assert len(confirmed) == 2
    assert confirmed[0].setup_id != confirmed[1].setup_id
    assert confirmed[1].sweep.reference_level.established_at >= confirmed[0].sweep.candle_end


def test_replayed_transport_metadata_preserves_semantic_event_identity():
    bars, observations = evidence()
    first = at_level(detect_sfp(bars, observations, spec(), evaluated_at=bars[-1].interval_end))[-1]
    received = bars[-1].interval_end + timedelta(seconds=7)
    replayed = observations[-1].model_copy(
        update={"observed_at": received, "receive_time": received, "recorded_at": received}
    )
    second = at_level(
        detect_sfp(bars, (*observations[:-1], replayed), spec(), evaluated_at=received)
    )[-1]
    assert first.event_id == second.event_id
    assert first.setup_id == second.setup_id
    assert second.observed_at == received


def test_clock_expiry_uses_existing_evidence_without_inventing_a_closed_candle():
    bars, observations = evidence(forming=True)
    config = spec(expiry_bars=1)
    asof = bars[-1].interval_end
    result = detect_sfp(bars, observations, config, evaluated_at=asof)
    event = at_level(result)[-1]
    assert event.state is BrainSetupState.EXPIRED
    assert event.event_time == event.expires_at == asof
    assert event.observed_at == asof
    assert event.evidence.finality is Finality.FORMING
    assert not event.provisional


def test_level_significance_exact_minimum_is_inclusive():
    assert (
        at_level(scan(config=spec(minimum_level_significance="2.04")))[-1].state
        is BrainSetupState.CONFIRMED
    )
    assert not at_level(scan(config=spec(minimum_level_significance="2.0401")))


def test_reclaim_at_last_allowed_bar_is_inclusive():
    prices = [*BULL[:4], (102, 103, 98, 99), (99, 102, "98.5", 101), (101, 104, 100, 103)]
    events = at_level(scan(prices, config=spec(reclaim_window=1)))
    assert events[-1].state is BrainSetupState.CONFIRMED


def test_a_breakout_with_a_new_extreme_still_has_explicit_breakout_classification():
    prices = [*BULL[:4], (102, 103, 98, 99), (99, 100, 97, "97.5")]
    event = at_level(scan(prices))[-1]
    assert event.condition is SfpCondition.SUCCESSFUL_BREAKOUT
    assert event.state is BrainSetupState.INVALIDATED


def test_paper_scope_is_a_fixed_schema_contract():
    with pytest.raises(ValidationError):
        SfpSpec.model_validate({**spec().model_dump(), "paper_only": False})


def test_late_historical_evidence_can_support_a_later_sweep_after_it_is_known():
    bars, observations = evidence()
    received = bars[2].interval_end
    old = observations[0].model_copy(update={"observed_at": received, "receive_time": received})
    result = detect_sfp(bars, (old, *observations[1:]), spec(), evaluated_at=bars[-1].interval_end)
    event = at_level(result)[-1]
    assert result.required_evidence is EvidenceAvailability.AVAILABLE
    assert event.state is BrainSetupState.CONFIRMED
    assert event.sweep.reference_level.known_at == received
    assert received <= event.sweep.event_time


def test_delayed_previous_close_cannot_backdate_a_sweep_precondition():
    bars, observations = evidence()
    received = bars[4].interval_end + timedelta(seconds=5)
    late = observations[3].model_copy(update={"observed_at": received, "receive_time": received})
    result = detect_sfp(
        bars,
        (*observations[:3], late, *observations[4:]),
        spec(),
        evaluated_at=bars[-1].interval_end,
    )
    assert not at_level(result)


def test_late_reclaim_cannot_backdate_confirmation_on_an_earlier_received_candle():
    prices = [*BULL[:4], (102, 103, 98, 99), (99, 102, "98.5", 101), (101, 104, 100, 103)]
    bars, observations = evidence(prices)
    received = bars[-1].interval_end + timedelta(seconds=5)
    late = observations[5].model_copy(update={"observed_at": received, "receive_time": received})
    result = detect_sfp(
        bars, (*observations[:5], late, observations[6]), spec(), evaluated_at=received
    )
    events = at_level(result)
    assert events[-1].condition is SfpCondition.CONFIRMED_RECLAIM
    assert events[-1].observed_at == received
    assert not any(event.state is BrainSetupState.CONFIRMED for event in events)


def test_expiry_event_time_is_the_deadline_when_confirmation_arrives_late():
    bars, observations = evidence()
    received = bars[-1].interval_end + timedelta(minutes=20)
    late = observations[-1].model_copy(update={"observed_at": received, "receive_time": received})
    config = spec(expiry_bars=2, required_evidence_max_age_bars=2)
    result = detect_sfp(bars, (*observations[:-1], late), config, evaluated_at=received)
    event = at_level(result)[-1]
    assert event.state is BrainSetupState.EXPIRED
    assert event.event_time == event.expires_at < event.observed_at
    assert event.event_time > bars[-1].interval_end
