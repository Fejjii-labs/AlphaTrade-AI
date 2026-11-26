"""Stepped exits for Nested and SFP on the BloFin demo (strategy spec v1).

TP1 at about 1R (15%, then break-even by the shared partial-exit manager), TP2 50%
at the strategy's structure target, TP3 20% at the next structure level (merged
into TP2 when absent), and a 15% runner trailing the last 5 closed candles. Off by
default: only families listed in GOVERNED_DEMO_LADDER_FAMILIES, with demo partial
exits on.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.config import ExchangeMode, Settings
from app.services.automated_paper_loop import nested_extension_target, structure_ladder_legs
from tests.test_governed_sfp_planning import planning

D = Decimal


def _legs(**kw):
    base = {"family": "nested", "is_long": True, "entry": D(100), "stop": D(98), "tp2": D(106)}
    base["tp3"] = None
    base.update(kw)
    return structure_ladder_legs(**base)


def test_long_ladder_with_tp3_and_runner():
    legs, runner = _legs(tp3=D(110))
    assert [(p, f) for p, f, _ in legs] == [
        (D(102), D("0.15")),
        (D(106), D("0.5")),
        (D(110), D("0.2")),
    ]
    assert sum(f for _, f, _ in legs) + D(runner["remaining_quantity_fraction"]) == 1
    assert runner["activation_target_order"] == 3
    assert runner["rule_id"] == "closed-candle-trail" and runner["expression"] == "lookback_bars=5"
    assert [f for _, _, f in legs] == [
        "nested-tp1-r-multiple",
        "nested-tp2-measured-move",
        "nested-tp3-extension-1618",
    ]


def test_missing_or_inside_tp3_merges_into_tp2():
    for tp3 in (None, D(105)):
        legs, runner = _legs(tp3=tp3)
        assert [(p, f) for p, f, _ in legs] == [(D(102), D("0.15")), (D(106), D("0.7"))]
        assert runner["activation_target_order"] == 2


def test_short_ladder_and_tp1_never_past_sixty_percent_of_tp2():
    legs, _ = _legs(family="sfp", is_long=False, entry=D(100), stop=D(101), tp2=D(98.5), tp3=D(97))
    # 1R would be 99; 60% of the way to TP2 is 99.1, which is nearer and wins.
    assert [p for p, _, _ in legs] == [D("99.1"), D("98.5"), D(97)]
    assert legs[0][2] == "sfp-tp1-r-multiple"


@pytest.mark.parametrize(
    "kw,reason",
    [({"stop": D(101)}, "invalidated_by_price"), ({"tp2": D(99)}, "invalidation_unavailable")],
)
def test_bad_geometry_is_refused(kw, reason):
    assert _legs(**kw) == reason


def test_nested_extension_is_1618_of_the_impulse():
    bars = (
        SimpleNamespace(high=D(101), low=D(100)),  # anchor
        SimpleNamespace(high=D(110), low=D(104)),  # impulse
    )
    event = SimpleNamespace(anchor_index=0, impulse_index=1)
    assert nested_extension_target(bars, event, is_long=True) == D(110) + D("1.618") * D(10)
    short_bars = (
        SimpleNamespace(high=D(110), low=D(109)),
        SimpleNamespace(high=D(105), low=D(100)),
    )
    assert nested_extension_target(short_bars, event, is_long=False) == D(100) - D("1.618") * D(10)
    assert (
        nested_extension_target(
            bars, SimpleNamespace(anchor_index=5, impulse_index=1), is_long=True
        )
        is None
    )


def test_settings_default_off_and_parse():
    settings = Settings(_env_file=None)
    assert settings.governed_demo_ladder_families == []
    assert settings.governed_demo_ladder_tp1_r == D(1)
    parsed = Settings(_env_file=None, governed_demo_ladder_families="Nested, SFP")
    assert parsed.governed_demo_ladder_families == ["nested", "sfp"]


def _ladder_on(loop, families=("sfp",)):
    loop._settings = loop._settings.model_copy(
        update={
            "governed_demo_ladder_families": list(families),
            "governed_demo_partial_exits_enabled": True,
            "exchange_mode": ExchangeMode.PAPER_EXCHANGE_DEMO,
        }
    )
    return loop


@pytest.mark.parametrize("bearish", [False, True])
def test_governed_sfp_plan_without_the_switch_keeps_one_target(bearish):
    loop, kwargs = planning(bearish=bearish)
    terms = loop._plan_terms(**kwargs)
    assert not isinstance(terms, str), terms
    assert len(terms.risk_and_exits.targets) == 1
    assert terms.risk_and_exits.runner.enabled is False
    # Listed family but partial exits off: still one target.
    loop = _ladder_on(loop)
    loop._settings = loop._settings.model_copy(
        update={"governed_demo_partial_exits_enabled": False}
    )
    terms = loop._plan_terms(**kwargs)
    assert not isinstance(terms, str) and len(terms.risk_and_exits.targets) == 1


@pytest.mark.parametrize("bearish", [False, True])
def test_governed_sfp_plan_with_the_switch_has_stepped_exits(bearish):
    loop, kwargs = planning(bearish=bearish)
    single = loop._plan_terms(**kwargs)
    loop = _ladder_on(loop)
    terms = loop._plan_terms(**kwargs)
    if isinstance(terms, str):
        # A near structure target can fail the weighted 1R check once TP1 is nearer.
        assert terms == "planned_reward_risk_below_minimum"
        return
    exits = terms.risk_and_exits
    prices = [t.price.value for t in exits.targets]
    fractions = [t.quantity_fraction for t in exits.targets]
    assert fractions[0] == D("0.15") and sum(fractions) == D("0.85")
    assert exits.runner.enabled is True
    assert exits.runner.remaining_quantity_fraction == D("0.15")
    assert exits.runner.activation_target_order == len(prices)
    entry = terms.basis_policy.execution_price.value
    ordered = sorted(prices, reverse=bearish)
    assert prices == ordered and ((prices[0] < entry) if bearish else (prices[0] > entry))
    # TP2 is the old single structure target, on the venue tick.
    assert prices[1] == single.risk_and_exits.targets[0].price.value
    assert all(p % loop._snapshot.tick == 0 for p in prices)
    assert exits.targets[0].derivation.formula_id == "sfp-tp1-r-multiple"
