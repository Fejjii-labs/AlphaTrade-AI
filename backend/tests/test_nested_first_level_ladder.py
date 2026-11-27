"""Nested ladder and break-even switch chosen by the three-year backtest (26 Nov 2026).

- ``GOVERNED_DEMO_NESTED_LADDER_TARGETS=first_level``: TP1 15% at the configured R
  (0.75R on staging), TP2 50% at whichever comes first of the measured move and the
  confirmed swings of the next chart up (4x the trigger chart, built from its own
  candles), TP3 20% at the next one, a 15% runner. Default ``measured_move`` keeps
  spec v1 (TP2 measured move, TP3 1.618 extension).
- ``GOVERNED_DEMO_LADDER_BREAKEVEN=false``: the stop no longer moves to break-even
  after TP1; the runner trail still applies. Default true keeps today's behaviour.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.common import Timeframe
from app.services.automated_paper_loop import first_level_ladder_legs, htf_swing_levels
from tests.test_nested_demo_gates import _confirmed, _loop, _policy
from tests.test_partial_exit_demo import (
    COMMAND,
    MemoryStore,
    SimVenue,
    _client,
    _position,
    _settings,
)

D = Decimal
T0 = datetime(2026, 11, 1, tzinfo=UTC)


def _hours(blocks: list[tuple[str, str]], *, offset_hours: int = 0, tail: int = 0):
    """1h candles: four per (high, low) 4h block; the block's high and low on its 2nd hour."""

    bars = []
    start = T0 + timedelta(hours=offset_hours)
    for b, (high, low) in enumerate(blocks):
        for k in range(4):
            at = start + timedelta(hours=4 * b + k)
            peak = k == 1
            bars.append(
                SimpleNamespace(
                    interval_start=at,
                    high=D(high) if peak else D(low) + 1,
                    low=D(low) if peak else D(high) - 1,
                )
            )
    for k in range(tail):  # an incomplete block at the end
        at = start + timedelta(hours=4 * len(blocks) + k)
        bars.append(SimpleNamespace(interval_start=at, high=D(999), low=D(1)))
    return tuple(bars)


BLOCKS = [("10", "5"), ("11", "6"), ("15", "2"), ("12", "7"), ("11", "6"),
          ("13", "4"), ("20", "8"), ("14", "7"), ("12", "6")]  # fmt: skip


def test_htf_swings_from_aligned_complete_blocks() -> None:
    assert htf_swing_levels(_hours(BLOCKS), is_long=True) == [D(15), D(20)]
    assert htf_swing_levels(_hours(BLOCKS), is_long=False) == [D(2), D(4)]
    # An incomplete last block is dropped (its 999 high would otherwise count).
    assert htf_swing_levels(_hours(BLOCKS, tail=3), is_long=True) == [D(15), D(20)]


def test_htf_blocks_start_on_the_higher_chart_boundary() -> None:
    # Starting at 01:00, the first three hours are skipped and blocks re-align at 04:00.
    shifted = _hours(BLOCKS, offset_hours=1)
    assert all(b.interval_start.hour % 4 == 1 for b in shifted[::4])
    assert htf_swing_levels(shifted[3:], is_long=True) == htf_swing_levels(
        _hours(BLOCKS)[4:], is_long=True
    )
    assert htf_swing_levels((), is_long=True) == []


def _legs(**kw):
    base = {"is_long": True, "entry": D(100), "stop": D(98), "measured_move": D(106)}
    base["htf_levels"] = [D(103), D(104), D(99)]
    base.update(kw)
    return first_level_ladder_legs(**base)


def test_tp2_is_the_first_level_beyond_tp1_and_tp3_the_next() -> None:
    legs, runner = _legs()
    assert [(p, f) for p, f, _ in legs] == [
        (D("101.50"), D("0.15")),
        (D(103), D("0.5")),
        (D(104), D("0.2")),
    ]
    assert [f for _, _, f in legs] == [
        "nested-tp1-r-multiple",
        "nested-tp2-first-structure-level",
        "nested-tp3-next-structure-level",
    ]
    assert runner["activation_target_order"] == 3
    assert runner["remaining_quantity_fraction"] == "0.15"
    assert runner["expression"] == "lookback_bars=5"


def test_measured_move_first_when_it_is_nearer() -> None:
    legs, _ = _legs(measured_move=D(102), htf_levels=[D(110)])
    assert [p for p, _, _ in legs] == [D("101.50"), D(102), D(110)]


def test_one_level_merges_and_none_falls_back_to_one_r_past_tp1() -> None:
    legs, runner = _legs(htf_levels=[])
    assert [(p, f) for p, f, _ in legs] == [(D("101.50"), D("0.15")), (D(106), D("0.7"))]
    assert runner["activation_target_order"] == 2
    legs, _ = _legs(measured_move=D(101), htf_levels=[D(100.5)])
    assert [(p, f) for p, f, _ in legs] == [(D("101.50"), D("0.15")), (D("103.50"), D("0.7"))]


def test_short_mirror_and_bad_geometry() -> None:
    legs, _ = first_level_ladder_legs(
        is_long=False,
        entry=D(100),
        stop=D(102),
        measured_move=D(94),
        htf_levels=[D(97), D(96), D(101)],
    )
    assert [p for p, _, _ in legs] == [D("98.50"), D(97), D(96)]
    assert _legs(stop=D(100)) == "invalidated_by_price"


def test_settings_defaults_keep_todays_behaviour() -> None:
    settings = Settings(_env_file=None)
    assert settings.governed_demo_ladder_breakeven is True
    assert settings.governed_demo_nested_ladder_targets == "measured_move"
    parsed = Settings(
        _env_file=None,
        governed_demo_ladder_breakeven="false",
        governed_demo_nested_ladder_targets="first_level",
    )
    assert parsed.governed_demo_ladder_breakeven is False
    assert parsed.governed_demo_nested_ladder_targets == "first_level"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, governed_demo_nested_ladder_targets="nearest")


def test_the_demo_loop_passes_the_target_rule(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return "captured"

    monkeypatch.setattr("app.services.governed_blofin_demo._plan_terms", capture)
    loop = _loop(
        governed_demo_ladder_families=["nested"],
        governed_demo_ladder_allow_no_backup_stop=True,
        governed_demo_partial_exits_enabled=True,
        governed_demo_nested_ladder_targets="first_level",
        governed_demo_ladder_tp1_r=D("0.75"),
    )
    from app.core.config import ExchangeMode

    loop._settings = loop._settings.model_copy(
        update={"exchange_mode": ExchangeMode.PAPER_EXCHANGE_DEMO}
    )
    # Only the plan builder's inputs matter here: a snapshot with equity, demo risk off.
    loop._snapshot = SimpleNamespace(equity=D(10000), available=D(10000), venue_equity=None)
    monkeypatch.setattr(loop, "_uses_demo_risk_v2", lambda: False)
    assembled = _confirmed(monkeypatch, "N2")
    result = loop._plan_terms(
        candidate=SimpleNamespace(),
        assembled=assembled,
        policy=_policy(Timeframe.H1),
        account_id=None,
        equity=D(10000),
        now=T0,
        eligibility_valid_until=T0,
        eligibility_id=None,
    )
    assert result == "captured"
    assert seen["structure_ladder"] is True
    assert seen["nested_ladder_targets"] == "first_level"
    assert seen["structure_ladder_tp1_r"] == D("0.75")


# The exit manager with the break-even move off.


def test_breakeven_off_keeps_the_stop_after_tp1() -> None:
    from app.services.partial_exits.demo import GovernedDemoPartialExitManager

    venue = SimVenue(mark=D("110"))
    store = MemoryStore()
    manager = GovernedDemoPartialExitManager(
        settings=_settings(),
        venue=venue,
        store=store,
        hold=lambda organization_id: None,
        breakeven=False,
    )
    manager.step(_position())
    assert venue.placed == [(_client("t1"), "sell", D("0.300"))]
    manager.step(_position())  # TP1 fill read: no move
    assert store.load(COMMAND).stop_moves == ()
    venue.mark = D("100.5")  # back through where break-even would sit: nothing closes
    manager.step(_position())
    assert len(venue.placed) == 1
    venue.mark = D("94.9")  # the original stop still closes the rest
    manager.step(_position())
    assert venue.placed[1] == (_client("x"), "sell", D("0.700"))
