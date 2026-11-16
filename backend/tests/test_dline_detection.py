"""Synthetic OHLCV interpretations of seven screenshots, never historical data."""
from datetime import timedelta
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from app.schemas.common import Timeframe, TradeDirection
from app.strategy_brain.dline.contracts import DlineParameters, DlineSpec, SourcedLevel
from app.strategy_brain.dline.detector import detect_dline
from tests.test_sfp_detector import START, evidence

# Invented coil inspired by checklist + chart 1: descending highs at 1/4/7,
# confirmed low at 8 and closed break at 10. No screenshot prices are hardcoded.
COIL = [(108,109,107,108),(108,112,107,109),(107,108,105,107),
        (106,107,104,106),(106,109,105,107),(104,106,102,104),
        (103,104,101,103),(103,106,102,104),(101,103,100,102),
        (102,103,101,102),(102,105,101,104)]


def spec(**changes):
    return DlineSpec(parameters=DlineParameters(atr_period=3, pivot_width=1,
                     ema_period=3, ema_slope_bars=1, **changes))


def scan(prices=COIL, config=None, **kwargs):
    config = config or spec()
    bars, obs = evidence(prices, timeframe=config.trigger_timeframe)
    htf, hobs = evidence([(90+i,92+i,89+i,91+i) for i in range(6)],
                         start=START-timedelta(days=1), timeframe=Timeframe.H4)
    return detect_dline(bars, obs, config, evaluated_at=obs[-1].observed_at,
                        tick_size=D('.1'), htf_bars=htf, htf_observations=hobs, **kwargs)


def test_dline_checklist_and_chart1_three_touch_body_break_fib_plan():
    result = scan()
    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert c.line.touches == (1,4,7)
    assert c.entry == D(104)  # reference only, never a next-open fill
    assert c.stop < D(100)
    assert c.targets[0].price == D('107.4')
    assert c.targets[-1].price == D(112)
    assert [t.fraction for t in c.targets] == [D('.5'), D('.5')]
    assert c.gross_rr >= 2 and c.execution_permission == 'none'
    assert {'automated_ob_provider','automated_poc_provider'} <= set(c.unavailable)


def test_dline_wicks_and_equality_never_trigger():
    for close in (102,103):
        assert not scan(COIL[:-1]+[(102,110,101,close)]).candidates


@pytest.mark.parametrize('changes', [{'slope_min_atr': D('1')}, {'slope_max_atr': D('.001')}])
def test_dline_slope_band_refuses_flat_or_steep(changes):
    assert not scan(config=spec(**changes)).candidates


def test_dline_low_rr_refused_and_risk_cap():
    assert not scan(config=spec(minimum_rr=D('10'))).candidates
    with pytest.raises(ValidationError):
        spec(risk_fraction=D('.031'))


def test_dline_closed_only_future_receipts_and_prefix_invariance():
    bars, obs=evidence(COIL)
    assert not detect_dline(bars,obs,spec(),evaluated_at=bars[-1].interval_end-timedelta(seconds=1),tick_size=D('.1')).candidates
    assert not scan(COIL[:-1]).candidates
    first=scan().candidates[0]
    later=scan(COIL+[(104,108,103,107),(107,150,106,140)]).candidates[0]
    assert first == later
    delayed=tuple(o.model_copy(update={'receive_time': o.receive_time+timedelta(days=1)}) for o in obs)
    assert not detect_dline(bars,delayed,spec(),evaluated_at=bars[-1].interval_end,tick_size=D('.1')).candidates


def test_dline_short_mirror():
    prices=[(220-o,220-low,220-h,220-c) for o,h,low,c in COIL]
    config=spec().model_copy(update={'direction':TradeDirection.SHORT})
    bars,obs=evidence(prices)
    htf,hobs=evidence([(130-i,131-i,128-i,129-i) for i in range(6)], start=START-timedelta(days=1),timeframe=Timeframe.H4)
    result=detect_dline(bars,obs,config,evaluated_at=obs[-1].observed_at,tick_size=D('.1'),htf_bars=htf,htf_observations=hobs)
    assert len(result.candidates)==1
    c=result.candidates[0]
    assert c.stop > c.entry > c.targets[0].price > c.targets[-1].price


def level(price, kind='poc', **changes):
    return SourcedLevel(price=D(str(price)),kind=kind,symbol='BTCUSDT',timeframe=Timeframe.H2,
        source='synthetic manual snapshot',evidence_ref='fixture-proof',known_at=START,
        expires_at=START+timedelta(days=2),**changes)


def test_dline_chart2_sourced_htf_ladder_and_unavailable_levels():
    c=scan(levels=(level(108),level(111,'daily'),level(115,'ob'))).candidates[0]
    assert len(c.targets)==4 and c.runner_fraction==D('.2')
    assert sum(t.fraction for t in c.targets)+c.runner_fraction==1
    assert c.targets[0].fraction==D('.5')
    assert c.confluences['sourced_target']=='present'


def test_dline_chart3_attempt_cap_and_retest_expiry():
    repeated=COIL+[(102,103,101,102),(102,104,101,104),(100,101,99,100),(101,103,100,102)]
    assert max(c.attempt for c in scan(repeated).candidates)<=2
    config=spec(entry_mode='retest')
    assert not scan(config=config).candidates
    result=scan(COIL+[(104,105,103,104)],config=config)
    assert result.candidates[0].entry==D(103)
    assert not scan(COIL+[(105,106,104,105)]*7+[(104,105,103,104)],config=config).candidates


def test_dline_chart5_5m_ob_cap_does_not_target_through_ob():
    config=spec(htf_required=False).model_copy(update={'trigger_timeframe':Timeframe.M5})
    c=scan(config=config,levels=(level(108),level(110,'ob'),level(120))).candidates[0]
    assert c.targets[-1].price==D('109.9') and c.runner_fraction==0
    assert all(t.price<=D('109.9') for t in c.targets)


def test_dline_ranging_first_target_must_clear_2r():
    assert not scan(config=spec(range_efficiency_threshold=D('1'))).candidates
    c=scan(config=spec(range_efficiency_threshold=D('1'),ranging_first_target_min_rr=D('.5'))).candidates[0]
    assert c.aggressive_tp and len(c.targets)==1
    assert c.targets[0].fraction==1 and c.runner_fraction==0


def test_dline_missing_htf_and_symbol_or_gap_refused():
    bars,obs=evidence(COIL)
    assert not detect_dline(bars,obs,spec(),evaluated_at=obs[-1].observed_at,tick_size=D('.1')).candidates
    assert not scan(config=spec().model_copy(update={'symbol':'ETHUSDT'})).candidates
    assert not detect_dline(bars[1:5]+bars[6:],obs[1:5]+obs[6:],spec(),evaluated_at=obs[-1].observed_at,tick_size=D('.1')).candidates
