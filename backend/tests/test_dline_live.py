"""D-line live (d_line/v2): Sofien's checklist only, synthetic OHLCV, never historical data.

Long fixture: descending highs at 1/4/7 (line 112 - (k - 1)), structure low 100 at 8,
body close above the line at 10. The short fixture mirrors every price around 100.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.common import Timeframe, TradeDirection
from app.strategy_brain.dline.live import (
    DlineLiveParameters,
    DlineLiveSpec,
    angle_confidence,
    detect_dline_live,
    htf_timeframe_for,
    simulate_split_entry,
    slope_angle_degrees,
)
from tests.test_sfp_detector import START, evidence

TICK = Decimal(".1")
COIL = [
    (108, 109, 107, 108),
    (108, 112, 107, 109),
    (107, 108, 105, 107),
    (106, 107, 104, 106),
    (106, 109, 105, 107),
    (104, 106, 102, 104),
    (103, 104, 101, 103),
    (103, 106, 102, 104),
    (101, 103, 100, 102),
    (102, 103, 101, 102),
    (102, 105, 101, "103.5"),
]
# 1h higher highs (110 -> 115) and higher lows (96 -> 100), ending at the trigger hour.
HTF_UP = [
    (100, 102, 99, 101),
    (101, 105, 100, 104),
    (104, 110, 103, 108),
    (108, 109, 101, 102),
    (102, 104, 96, 97),
    (97, 103, "97.5", 102),
    (102, 108, 101, 107),
    (107, 115, 106, 113),
    (113, 114, 104, 105),
    (105, 106, 100, 101),
    (101, 107, "101.5", 106),
    (106, 109, 105, 108),
]
HTF_START = START + timedelta(hours=2) - timedelta(hours=len(HTF_UP))


def mirror_rows(rows):
    # (o, h, l, c) -> (200-o, 200-l, 200-h, 200-c)
    return [
        (
            Decimal(200) - Decimal(str(o)),
            Decimal(200) - Decimal(str(low)),
            Decimal(200) - Decimal(str(high)),
            Decimal(200) - Decimal(str(c)),
        )
        for o, high, low, c in rows
    ]


def params(**changes):
    base = {
        "atr_period": 3,
        "pivot_width": 1,
        "htf_pivot_width": 1,
        "atr_per_bar_at_45_degrees": Decimal(".3"),
    }
    return DlineLiveParameters(**{**base, **changes})


def spec(direction=TradeDirection.LONG, **changes):
    return DlineLiveSpec(symbol="BTCUSDT", direction=direction, parameters=params(**changes))


def scan(prices=COIL, htf=HTF_UP, config=None, evaluated_extra=timedelta(0)):
    config = config or spec()
    bars, obs = evidence(prices, timeframe=config.trigger_timeframe)
    htf_bars, _ = evidence(htf, start=HTF_START, timeframe=config.htf_timeframe)
    return detect_dline_live(
        bars,
        htf_bars,
        config,
        evaluated_at=obs[-1].observed_at + evaluated_extra,
        tick_size=TICK,
    )


def only(result):
    assert len(result.signals) == 1, result.rejections
    return result.signals[0]


# --- spec and parameters -------------------------------------------------------------


def test_defaults_match_sofiens_spec():
    p = DlineLiveParameters()
    assert p.preferred_touches == 3 and p.minimum_touches == 2
    assert p.ideal_angle_degrees == 45 and p.angle_tolerance_degrees == 15
    assert p.entry_mode == "split" and p.breakout_fraction == Decimal(".5")
    assert p.tp1_distance_fraction == Decimal(".5")
    assert (p.tp1_fraction, p.tp2_fraction, p.tp3_fraction, p.runner_fraction) == (
        Decimal(".15"),
        Decimal(".5"),
        Decimal(".2"),
        Decimal(".15"),
    )
    assert p.minimum_reward_risk == 2 and p.max_attempts_per_line == 2
    assert p.retest_expiry_bars >= 1


def test_timeframes_and_bigger_trend_map():
    assert htf_timeframe_for(Timeframe.M15) is Timeframe.H1
    assert htf_timeframe_for(Timeframe.M30) is Timeframe.H2
    assert htf_timeframe_for(Timeframe.H1) is Timeframe.H4
    for tf in (Timeframe.M15, Timeframe.M30, Timeframe.H1):
        assert DlineLiveSpec(symbol="ETHUSDT", trigger_timeframe=tf).trigger_timeframe is tf
    with pytest.raises(ValidationError):
        DlineLiveSpec(symbol="BTCUSDT", trigger_timeframe=Timeframe.M5)


def test_ladder_fractions_must_sum_to_one():
    with pytest.raises(ValidationError):
        DlineLiveParameters(tp1_fraction=Decimal(".3"))


def test_no_fib_or_moving_average_parameters():
    names = set(DlineLiveParameters.model_fields)
    assert not {n for n in names if "fib" in n or "ema" in n or "ribbon" in n or "ma_" in n}


# --- slope normalization -------------------------------------------------------------


def test_slope_angle_normalization_is_deterministic():
    unit = Decimal(".25")
    assert slope_angle_degrees(Decimal(".25"), Decimal(1), unit) == pytest.approx(45)
    assert slope_angle_degrees(Decimal("-.25"), Decimal(1), unit) == pytest.approx(45)
    assert slope_angle_degrees(Decimal(0), Decimal(1), unit) == pytest.approx(0)
    assert slope_angle_degrees(Decimal(".5"), Decimal(2), unit) == pytest.approx(45)


def test_confidence_decreases_only_above_45():
    p = DlineLiveParameters()
    assert angle_confidence(45, p) == 1
    assert angle_confidence(35, p) == 1
    assert angle_confidence(52.5, p) == pytest.approx(0.75)
    assert angle_confidence(60, p) == pytest.approx(0.5)
    assert angle_confidence(50, p) > angle_confidence(55, p)


# --- long breakout -------------------------------------------------------------------


def test_long_three_touch_body_close_breakout_plan():
    s = only(scan())
    assert s.direction is TradeDirection.LONG
    assert s.touches == (1, 4, 7) and s.touch_count == 3
    assert s.trigger_index == 10
    assert s.entry == Decimal("103.5")  # close of the trigger candle; demo enters next open
    assert s.breakout_level == Decimal(103)  # frozen line price at the trigger
    assert s.structure_extreme == Decimal(112)  # structure high (line start)
    assert s.structure_stop_anchor == Decimal(100)  # structure low
    assert s.stop == Decimal("99.9")  # below the structure low by one tick
    assert [(t.label, t.price, t.fraction) for t in s.targets] == [
        ("tp1", Decimal("107.7"), Decimal(".15")),
        ("tp2", Decimal(112), Decimal(".5")),
        ("tp3", Decimal(115), Decimal(".2")),
    ]
    assert s.runner_fraction == Decimal(".15")
    assert s.reward_risk == (Decimal(112) - Decimal("103.5")) / (
        Decimal("103.5") - Decimal("99.9")
    )
    assert s.htf_trend == "higher_highs_higher_lows"
    assert s.attempt == 1
    assert 30 <= s.angle_degrees <= 60
    assert 0 < s.confidence <= 1


def test_two_touch_line_is_lower_quality():
    # Move the third touch away: the line stays valid on two touches.
    rows = list(COIL)
    rows[7] = (103, "104.5", 102, 104)
    two = only(scan(rows))
    three = only(scan())
    assert two.touch_count == 2
    assert two.confidence < three.confidence


def test_wick_only_or_equal_close_is_no_signal():
    for close in (102, 103):
        result = scan([*COIL[:-1], (102, 110, 101, close)])
        assert result.signals == ()


def test_slope_out_of_band_is_rejected():
    steep = scan(config=spec(atr_per_bar_at_45_degrees=Decimal(".05")))
    flat = scan(config=spec(atr_per_bar_at_45_degrees=Decimal(5)))
    for result in (steep, flat):
        assert result.signals == ()
        assert "slope_out_of_band" in {reason for _, reason in result.rejections}


def test_band_is_configurable():
    wide = scan(
        config=spec(atr_per_bar_at_45_degrees=Decimal(".05"), angle_tolerance_degrees=Decimal(44))
    )
    assert len(wide.signals) == 1


def test_confidence_lower_when_steeper_above_45():
    near = only(scan(config=spec(atr_per_bar_at_45_degrees=Decimal(".33"))))
    steeper = only(scan(config=spec(atr_per_bar_at_45_degrees=Decimal(".24"))))
    assert steeper.angle_degrees > near.angle_degrees > 40
    assert steeper.confidence < near.confidence


# --- bigger trend --------------------------------------------------------------------


def test_long_requires_htf_higher_highs_and_higher_lows():
    down = mirror_rows(HTF_UP)
    result = scan(htf=down)
    assert result.signals == ()
    assert "htf_structure_not_aligned" in {r for _, r in result.rejections}


def test_missing_htf_structure_is_refused():
    result = scan(htf=HTF_UP[-3:])
    assert result.signals == ()
    assert "htf_structure_unavailable" in {r for _, r in result.rejections}


def test_htf_bar_closing_after_the_trigger_is_ignored():
    crash = [*HTF_UP, (108, 108, 50, 51)]  # closes one hour after the last base bar
    s = only(scan(htf=crash))
    assert s.htf_trend == "higher_highs_higher_lows"


# --- reward/risk ----------------------------------------------------------------------


def test_reward_risk_below_two_is_rejected():
    result = scan([*COIL[:-1], (102, 105, 101, "104.9")])
    assert result.signals == ()
    assert "reward_risk_below_minimum" in {r for _, r in result.rejections}


def test_tp3_falls_back_into_tp2_when_no_htf_swing_beyond():
    flat_top = [(o, min(h, 111), low, min(c, 111)) for o, h, low, c in HTF_UP]
    flat_top = [(min(o, 111), h, low, c) for o, h, low, c in flat_top]
    s = only(scan(htf=flat_top))
    assert [(t.label, t.fraction) for t in s.targets] == [
        ("tp1", Decimal(".15")),
        ("tp2", Decimal(".7")),
    ]
    assert "tp3_unavailable_merged_into_tp2" in s.reason_codes


# --- short mirror ----------------------------------------------------------------------


def test_short_mirrors_long():
    s = only(scan(mirror_rows(COIL), mirror_rows(HTF_UP), spec(TradeDirection.SHORT)))
    assert s.direction is TradeDirection.SHORT
    assert s.touches == (1, 4, 7)
    assert s.entry == Decimal("96.5")
    assert s.breakout_level == Decimal(97)
    assert s.structure_extreme == Decimal(88)
    assert s.structure_stop_anchor == Decimal(100)
    assert s.stop == Decimal("100.1")
    assert [(t.price, t.fraction) for t in s.targets] == [
        (Decimal("92.3"), Decimal(".15")),
        (Decimal(88), Decimal(".5")),
        (Decimal(85), Decimal(".2")),
    ]
    assert s.htf_trend == "lower_highs_lower_lows"


def test_short_refuses_htf_uptrend():
    result = scan(mirror_rows(COIL), HTF_UP, spec(TradeDirection.SHORT))
    assert result.signals == ()


# --- split entry ------------------------------------------------------------------------


AFTER_TOUCH = [(104, 105, "102.8", 104), (104, 106, 103, 105)]
AFTER_NO_TOUCH = [(104, 106, "103.5", 105)] * 7


def _signal_and_bars(after):
    bars, _ = evidence([*COIL, *after])
    s = only(scan())
    return s, bars


def test_split_entry_half_at_next_open_half_limit_at_line():
    s, bars = _signal_and_bars(AFTER_TOUCH)
    legs = simulate_split_entry(s, bars, spec().parameters)
    assert [(leg.kind, leg.fraction, leg.status, leg.price) for leg in legs] == [
        ("breakout", Decimal(".5"), "filled", Decimal(104)),
        ("retest", Decimal(".5"), "filled", Decimal(103)),
    ]
    assert legs[1].filled_index == 11


def test_retest_leg_expires_without_touch():
    s, bars = _signal_and_bars(AFTER_NO_TOUCH)
    legs = simulate_split_entry(s, bars, spec().parameters)
    assert legs[0].status == "filled"
    assert legs[1].status == "expired" and legs[1].price is None
    assert s.retest_expires_at == bars[10].interval_end + timedelta(minutes=15) * 6


def test_retest_after_expiry_does_not_fill():
    late = [*AFTER_NO_TOUCH[:6], (104, 105, 102, 103)]
    s, bars = _signal_and_bars(late)
    legs = simulate_split_entry(s, bars, spec(retest_expiry_bars=6).parameters)
    assert legs[1].status == "expired"


def test_entry_mode_breakout_is_single_full_leg():
    s, bars = _signal_and_bars(AFTER_TOUCH)
    legs = simulate_split_entry(s, bars, spec(entry_mode="breakout").parameters)
    assert [(leg.kind, leg.fraction) for leg in legs] == [("breakout", Decimal(1))]


# --- attempts ----------------------------------------------------------------------------


def test_first_attempt_stopped_at_structure_invalidates_line():
    after = [
        (103, "103.5", 99, 100),  # stop 99.9 hit before TP1
        (100, "101.5", "99.95", "100.5"),
        (100, 104, "99.95", 103),
        (103, 104, "102.5", "102.8"),
        (103, 105, "102.6", "103.8"),
    ]
    result = scan([*COIL, *after])
    assert [s.trigger_index for s in result.signals] == [10]
    assert "line_invalidated_after_stop" in {r for _, r in result.rejections}


BE_PATH = [
    (104, 108, "103.6", 107),  # TP1 107.7 hit
    (107, "107.5", "103.4", "103.8"),  # back to break-even 103.5
    ("103.8", 104, "102.5", "102.8"),  # closes back at the breakout level 103
    ("102.8", "104.6", "102.6", "103.8"),  # body close above again -> attempt 2
]


def test_second_attempt_only_after_break_even_close_with_structure_intact():
    result = scan([*COIL, *BE_PATH])
    assert [(s.trigger_index, s.attempt) for s in result.signals] == [(10, 1), (14, 2)]
    second = result.signals[1]
    assert second.line_id == result.signals[0].line_id
    assert second.stop == Decimal("99.9") and second.entry == Decimal("103.8")
    assert second.setup_id != result.signals[0].setup_id


def test_no_third_attempt():
    again = [
        ("103.8", 108, "103.7", 107),
        (107, "107.5", "103.5", "103.9"),
        ("103.9", 104, "102.5", "102.8"),
        ("102.8", "104.6", "102.6", "103.8"),
    ]
    result = scan([*COIL, *BE_PATH, *again])
    assert [s.attempt for s in result.signals] == [1, 2]
    assert "attempt_cap_reached" in {r for _, r in result.rejections}


def test_attempt_cap_is_configurable():
    result = scan([*COIL, *BE_PATH], config=spec(max_attempts_per_line=1))
    assert [s.attempt for s in result.signals] == [1]


def test_no_second_attempt_while_first_still_open():
    open_path = [
        (104, 106, "103.6", 105),  # neither TP1 nor stop
        (105, 106, "102.5", "102.8"),
        ("102.8", "104.6", "102.6", "103.8"),
    ]
    result = scan([*COIL, *open_path])
    assert [s.attempt for s in result.signals] == [1]


# --- no lookahead --------------------------------------------------------------------------


def test_prefix_scans_never_change_earlier_signals():
    full = scan([*COIL, *BE_PATH])
    for n in range(len(COIL) - 1, len(COIL) + len(BE_PATH) + 1):
        bars = [*COIL, *BE_PATH][:n]
        prefix = scan(bars)
        assert prefix.signals == tuple(s for s in full.signals if s.trigger_index < n)


def test_forming_trigger_candle_is_ignored():
    config = spec()
    bars, obs = evidence(COIL, timeframe=config.trigger_timeframe)
    htf_bars, _ = evidence(HTF_UP, start=HTF_START, timeframe=config.htf_timeframe)
    early = detect_dline_live(
        bars,
        htf_bars,
        config,
        evaluated_at=bars[-1].interval_end - timedelta(seconds=1),
        tick_size=TICK,
    )
    assert early.signals == ()


def test_line_is_frozen_at_trigger():
    s = only(scan())
    later = scan([*COIL, *BE_PATH]).signals[0]
    assert later.line_id == s.line_id
    assert later.breakout_level == s.breakout_level
    assert later.touches == s.touches
