"""D-line live (d_line/v2) wiring: compiler, scanner, plan terms, governed BloFin demo.

SQLite and simulated venues only. Demo only: D-line is refused on internal paper,
needs demo data mode (risk v2, real trading off) and demo partial exits.
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.strategy_brain import BrainSetupRow
from app.market_contracts.first_slice import first_slice_identity
from app.providers.exchange.demo_order_book import DemoOrderBook
from app.providers.exchange.governed_blofin import DemoVenueSnapshot
from app.schemas.common import Timeframe, TradeDirection
from app.schemas.trade_plan import EntrySide
from app.services.automated_paper_loop import _plan_terms, paper_spec_refusal
from app.services.canonical_strategy_evaluation import (
    project_executable_to_market,
    resolve_executable_strategy_policy,
)
from app.services.compiled_setup_service import CompiledSetupService
from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
from app.signal_fusion.errors import StrategyEvaluationPolicyError
from app.strategy_brain.dline.live import DLINE_LIVE_KIND, DlineLiveSpec
from app.strategy_brain.service import create_template
from tests.support.phase7_trade_plan import make_world
from tests.test_dline_live import COIL, HTF_START, HTF_UP, TICK, mirror_rows, params
from tests.test_sfp_detector import START, evidence
from tests.test_strategy_brain_nested import tenant_store  # noqa: F401

FLAT = (108, 108, 108, 108)
HTF_FLAT = (100, 100, 100, 100)


def live_spec(direction=TradeDirection.LONG, symbol="BTCUSDT", **changes):
    return DlineLiveSpec(symbol=symbol, direction=direction, parameters=params(**changes))


def approved(session, org, user, spec):
    template = create_template(session, organization_id=org, user_id=user, spec=spec)
    service = CompiledSetupService(session)
    result = service.compile_version(template["version_id"], organization_id=org, user_id=user)
    service.approve_version(
        template["version_id"], organization_id=org, user_id=user, confirm_message="I confirm"
    )
    session.commit()
    return template, result


# --- compiler / approval / projection ---------------------------------------------------


def test_dline_v2_compiles_executable_approves_and_projects(tenant_store):  # noqa: F811
    session, org, user = tenant_store
    template, result = approved(session, org, user, live_spec())
    assert result.status == "executable"
    policy = resolve_executable_strategy_policy(
        session, organization_id=org, strategy_version_id=template["version_id"]
    )
    assert isinstance(policy.authored_spec, DlineLiveSpec)
    assert policy.adapter_id == DLINE_LIVE_KIND
    assert policy.fusion_policy.policy_version == DLINE_LIVE_KIND
    projected = project_executable_to_market(policy, symbol="ethusdt", timeframe="30m")
    assert projected.authored_spec.symbol == "ETHUSDT"
    assert projected.authored_spec.trigger_timeframe is Timeframe.M30
    with pytest.raises(StrategyEvaluationPolicyError):
        project_executable_to_market(policy, symbol="ETHUSDT", timeframe="5m")


def test_research_dline_v1_stays_non_executable():
    from app.services.setup_ast_compiler import compile_from_authored
    from app.strategy_brain.dline.contracts import DlineSpec

    card = SimpleNamespace(strategy_name="D-line draft research")
    result = compile_from_authored(
        card=card, rules=None, pattern_spec=DlineSpec().model_dump(mode="json")
    )
    assert result.status == "non_executable"
    assert result.failures[0].code == "dline_draft_only"


def test_unknown_family_still_refused_closed():
    assert paper_spec_refusal(object(), None, None) == "spec_not_paper_executable"
    assert paper_spec_refusal(live_spec(), None, TradeDirection.LONG) is None
    assert paper_spec_refusal(live_spec(TradeDirection.SHORT), None, TradeDirection.SHORT) is None


# --- scanner: assembly, assessment, durable setup ---------------------------------------


def _scripted_source(trigger_rows, htf_rows):
    from tests.support.live_market_monitor import ScriptedPerpetualSource

    pad = 250
    bars, _ = evidence([FLAT] * pad + list(trigger_rows), start=START - timedelta(minutes=15) * pad)
    hpad = 60
    htf, _ = evidence(
        [HTF_FLAT] * hpad + list(htf_rows),
        start=HTF_START - timedelta(hours=hpad),
        timeframe=Timeframe.H1,
    )

    class Source(ScriptedPerpetualSource):
        def fetch_closed_ohlcv(
            self, *, identity, instrument, timeframe, min_final_bars, evaluated_at
        ):
            from app.market_contracts.ohlcv import require_closed_series

            series = {Timeframe.M15: bars, Timeframe.H1: htf}[timeframe]
            return require_closed_series(
                list(series),
                identity=identity,
                timeframe=timeframe,
                evaluated_at=evaluated_at,
                min_bars=min(min_final_bars, len(series)),
            )

    return Source(replay=True, bars_15m=list(bars)), bars, htf


def _assemble(session, org, policy, rows=COIL, htf_rows=HTF_UP):
    from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
    from app.evidence_pipeline.watcher_port import AssemblingWatcherScanEvidence
    from app.strategy_brain.dline.runtime import assemble_dline

    source, bars, _ = _scripted_source(rows, htf_rows)
    now = bars[-1].interval_end + timedelta(seconds=5)
    port = AssemblingWatcherScanEvidence(
        FirstSliceEvidenceAssembler(source, replay=True, clock=lambda: now),
        session=session,
        watcher_store=None,
        symbol="BTCUSDT",
    )
    assembled = assemble_dline(
        port._assembler, executable=policy, organization_id=org, session=session
    )
    return assembled, port, now


@pytest.mark.parametrize("direction", [TradeDirection.LONG, TradeDirection.SHORT])
def test_scanner_confirms_dline_and_records_family(tenant_store, direction):  # noqa: F811
    from app.signal_fusion.strategy_evaluation_policy import evaluate_canonical_strategy

    session, org, user = tenant_store
    short = direction is TradeDirection.SHORT
    template, _ = approved(session, org, user, live_spec(direction))
    policy = resolve_executable_strategy_policy(
        session, organization_id=org, strategy_version_id=template["version_id"]
    )
    rows = mirror_rows(COIL) if short else COIL
    htf_rows = mirror_rows(HTF_UP) if short else HTF_UP
    assembled, _, now = _assemble(session, org, policy, rows, htf_rows)
    session.commit()
    assert assembled.bundle.bars_4h, "HTF bars travel with the bundle"
    assessment = evaluate_canonical_strategy(
        executable_policy=policy,
        command=assembled.assessment_command,
        evidence=assembled.bundle,
        evaluated_at=assembled.evaluated_at,
    )
    assert assessment.state.value == "confirmed_setup"
    later = evaluate_canonical_strategy(
        executable_policy=policy,
        command=assembled.assessment_command,
        evidence=assembled.bundle,
        evaluated_at=now + timedelta(minutes=15),
    )
    assert later.state.value != "confirmed_setup"
    rows_db = list(session.scalars(select(BrainSetupRow)))
    assert len(rows_db) == 1
    assert rows_db[0].payload["family"] == "d_line"
    assert rows_db[0].state == "CONFIRMED"


def test_scanner_tampered_htf_is_not_confirmed(tenant_store):  # noqa: F811
    from app.signal_fusion.strategy_evaluation_policy import evaluate_canonical_strategy

    session, org, user = tenant_store
    template, _ = approved(session, org, user, live_spec())
    policy = resolve_executable_strategy_policy(
        session, organization_id=org, strategy_version_id=template["version_id"]
    )
    assembled, _, _ = _assemble(session, org, policy)
    tampered = assembled.bundle.model_copy(update={"bars_4h": assembled.bundle.bars_4h[:-1]})
    assessment = evaluate_canonical_strategy(
        executable_policy=policy,
        command=assembled.assessment_command,
        evidence=tampered,
        evaluated_at=assembled.evaluated_at,
    )
    assert assessment.state.value != "confirmed_setup"


def test_wick_only_scan_has_no_candidate(tenant_store):  # noqa: F811
    from app.signal_fusion.strategy_evaluation_policy import evaluate_canonical_strategy

    session, org, user = tenant_store
    template, _ = approved(session, org, user, live_spec())
    policy = resolve_executable_strategy_policy(
        session, organization_id=org, strategy_version_id=template["version_id"]
    )
    rows = [*COIL[:-1], (102, 110, 101, 102)]
    assembled, _, _ = _assemble(session, org, policy, rows)
    assessment = evaluate_canonical_strategy(
        executable_policy=policy,
        command=assembled.assessment_command,
        evidence=assembled.bundle,
        evaluated_at=assembled.evaluated_at,
    )
    assert assessment.state.value != "confirmed_setup"


# --- plan terms --------------------------------------------------------------------------


def _assembled_stub(short=False, quote="103.5"):
    rows = mirror_rows(COIL) if short else COIL
    htf_rows = mirror_rows(HTF_UP) if short else HTF_UP
    bars, observations = evidence(rows)
    htf, _ = evidence(htf_rows, start=HTF_START, timeframe=Timeframe.H1)
    observed = observations[-1].observed_at
    price = Decimal(quote)
    return SimpleNamespace(
        current_price=SimpleNamespace(
            price=price, bid=price, ask=price, source_time=observed, provider_symbol="BTCUSDT"
        ),
        evaluated_at=observed,
        trigger_bar=bars[-1],
        bundle=SimpleNamespace(bars_15m=bars, bars_4h=htf, tick_size=TICK),
    )


def _common(short=False, quote="103.5"):
    assembled = _assembled_stub(short, quote)
    now = assembled.evaluated_at + timedelta(seconds=1)
    world = make_world()
    candidate = world.candidate.model_copy(
        update={
            "direction": TradeDirection.SHORT if short else TradeDirection.LONG,
            "created_at": now - timedelta(minutes=1),
            "valid_until": now + timedelta(hours=1),
        }
    )
    policy = SimpleNamespace(
        authored_spec=live_spec(TradeDirection.SHORT if short else TradeDirection.LONG),
        strategy_id=uuid4(),
        organization_id=candidate.organization_id,
        strategy_version_id=candidate.strategy_version_id,
    )
    kwargs = {
        "candidate": candidate,
        "assembled": assembled,
        "policy": policy,
        "account_id": uuid4(),
        "equity": Decimal("10000"),
        "now": now,
        "eligibility_valid_until": candidate.valid_until,
        "eligibility_id": uuid4(),
    }
    return kwargs


def test_internal_paper_refuses_dline():
    kwargs = _common()
    assert _plan_terms(**kwargs, internal_paper_sfp=True) == "dline_execution_demo_only"


@pytest.mark.parametrize("short", [False, True])
def test_demo_plan_terms_carry_the_ladder_and_runner(short):
    kwargs = _common(short, quote="96.5" if short else "103.5")
    terms = _plan_terms(**kwargs, governed_demo_dline=True, accept_unprofiled_minimum=True)
    assert not isinstance(terms, str), terms
    exits = terms.risk_and_exits
    assert exits.stop.value == (Decimal("100.1") if short else Decimal("99.9"))
    assert [(t.price.value, t.quantity_fraction) for t in exits.targets] == (
        [
            (Decimal("92.3"), Decimal(".15")),
            (Decimal(88), Decimal(".5")),
            (Decimal(85), Decimal(".2")),
        ]
        if short
        else [
            (Decimal("107.7"), Decimal(".15")),
            (Decimal(112), Decimal(".5")),
            (Decimal(115), Decimal(".2")),
        ]
    )
    assert exits.runner.enabled is True
    assert exits.runner.remaining_quantity_fraction == Decimal(".15")
    assert exits.runner.activation_target_order == 3
    assert exits.runner.rule_id == "closed-candle-trail"
    assert exits.runner.expression == "lookback_bars=5"
    assert [t.derivation.formula_id for t in exits.targets] == [
        "dline-tp1-half-to-structure",
        "dline-tp2-structure-extreme",
        "dline-tp3-htf-swing",
    ]
    notes = terms.presentation_metadata.notes
    assert "entry_mode=split" in notes
    assert f"retest_limit={'97' if short else '103'}" in notes


def test_demo_plan_terms_quote_rr_below_two_refused():
    kwargs = _common(quote="104.9")
    assert (
        _plan_terms(**kwargs, governed_demo_dline=True, accept_unprofiled_minimum=True)
        == "dline_quote_reward_risk_below_minimum"
    )


def test_ladder_terms_parse_for_partial_exits():
    from app.services.partial_exits.ladder import LadderRefusal
    from app.services.partial_exits.paper_terms import parse_partial_exit_terms

    kwargs = _common()
    terms = _plan_terms(**kwargs, governed_demo_dline=True, accept_unprofiled_minimum=True)
    parsed = parse_partial_exit_terms(
        terms.model_dump(mode="json"),
        direction=TradeDirection.LONG,
        entry_price=Decimal("103.5"),
        position_size=Decimal("10"),
    )
    assert not isinstance(parsed, LadderRefusal), parsed
    assert [leg.quantity for leg in parsed.ladder.target_legs] == [
        Decimal("1.5"),
        Decimal("5"),
        Decimal("2"),
    ]
    assert parsed.ladder.runner_leg.quantity == Decimal("1.5")
    # Break-even (plus costs) after a complete TP1 comes from the shared partial exits.
    from app.services.partial_exits.position import ExitFill, apply_fill, plan_breakeven_move

    state = parsed.open_state()
    assert state.current_stop == Decimal("99.9")
    assert plan_breakeven_move(state) is None
    filled = apply_fill(
        state,
        ExitFill(
            fill_id="tp1",
            kind="target",
            leg_order=1,
            quantity=Decimal("1.5"),
            price=Decimal("107.7"),
            occurred_at=kwargs["now"],
        ),
    ).state
    move = plan_breakeven_move(filled)
    assert move is not None and move.new_stop >= Decimal("103.5")


# --- governed BloFin demo --------------------------------------------------------------


def demo_settings(**changes):
    from app.core.config import ExchangeMode

    base = {
        "risk_model_v2_enabled": True,
        "governed_demo_data_mode_enabled": True,
        "governed_demo_partial_exits_enabled": True,
        # The fixtures are 15m; the default (30m, 1h) is covered below.
        "governed_demo_dline_timeframes": ["15m", "30m", "1h"],
    }
    settings = Settings(_env_file=None, **{**base, **changes})
    return settings.model_copy(update={"exchange_mode": ExchangeMode.PAPER_EXCHANGE_DEMO})


def demo_loop(settings, short=False, price=None):
    kwargs = _common(short, quote="96.5" if short else "103.5")
    now = kwargs["now"]
    loop = GovernedBloFinDemoLoop(None, settings, SimpleNamespace(now=lambda: now))
    side = EntrySide.SELL if short else EntrySide.BUY
    price = Decimal(price or ("96.5" if short else "103.5"))
    levels = ((price, Decimal("1000000")),)
    loop._snapshot = DemoVenueSnapshot(
        instrument="BTC-USDT",
        price=price,
        observed_at=now,
        tick=Decimal("0.1"),
        lot=Decimal("0.01"),
        minimum=Decimal("0.01"),
        multiplier=Decimal("1"),
        equity=Decimal("10000"),
        available=Decimal("10000"),
        maximum=Decimal("1000000"),
        book=DemoOrderBook(now, side, levels, levels),
        account_observed_at=now,
        venue_equity=Decimal("10000"),
        account_leverage=Decimal("10"),
        margin_mode="isolated",
        maintenance_tiers=((Decimal("0"), Decimal("0.004")),),
    )
    loop._demo_risk_config = SimpleNamespace(
        grades_gate_entries=False,
        grade_risk_overrides={},
        risk_per_trade_pct=Decimal("1"),
        daily_stop_pct=Decimal("5"),
    )
    loop._leverage_config = SimpleNamespace(leverage_decimal=Decimal("10"), margin_mode="isolated")
    loop._leverage_write_pending = True
    return loop, kwargs


@pytest.mark.parametrize("short", [False, True])
def test_demo_data_mode_plans_half_size_breakout_with_ladder(short):
    loop, kwargs = demo_loop(demo_settings(), short)
    terms = loop._plan_terms(**kwargs)
    assert not isinstance(terms, str), terms
    assert terms.execution_venue == "BLOFIN_DEMO"
    assert len(terms.risk_and_exits.targets) == 3
    assert terms.risk_and_exits.runner.enabled is True
    # Split entry: only the breakout half is sent; the stop budget is 1% of equity.
    stop_cash = terms.quantity.value * abs(
        terms.basis_policy.execution_price.value - terms.risk_and_exits.stop.value
    )
    assert stop_cash <= Decimal("50")
    assert stop_cash > Decimal("40")
    notes = terms.presentation_metadata.notes
    assert "breakout_fraction=0.5" in notes
    assert "retest_leg=not_sent_demo_market_entries_only" in notes


def test_demo_breakout_mode_sends_full_size():
    loop, kwargs = demo_loop(demo_settings())
    kwargs["policy"].authored_spec = live_spec(entry_mode="breakout")
    terms = loop._plan_terms(**kwargs)
    assert not isinstance(terms, str), terms
    stop_cash = terms.quantity.value * abs(
        terms.basis_policy.execution_price.value - terms.risk_and_exits.stop.value
    )
    assert Decimal("90") < stop_cash <= Decimal("100")


def test_demo_retest_only_mode_is_refused_on_demo():
    loop, kwargs = demo_loop(demo_settings())
    kwargs["policy"].authored_spec = live_spec(entry_mode="retest")
    assert loop._plan_terms(**kwargs) == "dline_retest_entry_unsupported_on_demo"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"governed_demo_data_mode_enabled": False}, "dline_requires_demo_data_mode"),
        ({"risk_model_v2_enabled": False}, "dline_requires_demo_data_mode"),
        ({"governed_demo_partial_exits_enabled": False}, "dline_requires_demo_partial_exits"),
    ],
)
def test_demo_gates_refuse_dline(changes, reason):
    loop, kwargs = demo_loop(demo_settings(**changes))
    assert loop._plan_terms(**kwargs) == reason


def test_demo_quote_below_two_r_refused():
    loop, kwargs = demo_loop(demo_settings(), price="104.9")
    assert loop._plan_terms(**kwargs) == "dline_quote_reward_risk_below_minimum"


def test_demo_bound_refuses_dline_before_any_venue_call_when_not_enabled(monkeypatch):
    loop, kwargs = demo_loop(demo_settings(governed_demo_data_mode_enabled=False))
    monkeypatch.setattr(loop, "_get_provider", lambda: pytest.fail("venue touched"))
    proof = loop._bound(
        None,
        target=SimpleNamespace(
            organization_id=kwargs["candidate"].organization_id,
            user_id=uuid4(),
            symbol="BTCUSDT",
        ),
        candidate=kwargs["candidate"],
        assessment=None,
        window=None,
        assembled=kwargs["assembled"],
        policy=kwargs["policy"],
        account=SimpleNamespace(id=kwargs["account_id"]),
    )
    assert proof.reason_code == "dline_requires_demo_data_mode"


def test_kill_switch_holds_dline_before_planning(monkeypatch):
    loop, kwargs = demo_loop(demo_settings())
    monkeypatch.setattr(loop, "_bound", lambda *a, **k: pytest.fail("killed loop planned"))
    proof = loop.continue_confirmed_setup(
        None,
        target=None,
        candidate=kwargs["candidate"],
        assessment=None,
        window=None,
        assembled=kwargs["assembled"],
        policy=kwargs["policy"],
        kill_switch_active=True,
    )
    assert proof.reason_code == "kill_switch_active"


def test_real_money_path_is_unreachable_for_dline():
    from pydantic import ValidationError

    from app.services.demo_data_mode import demo_data_mode_enabled
    from app.services.partial_exits.flags import BLOFIN_DEMO_VENUE, partial_exits_enabled

    # The spec itself is paper/demo only.
    with pytest.raises(ValidationError):
        DlineLiveSpec.model_validate({**live_spec().model_dump(mode="json"), "paper_only": False})
    # Real trading switches off both prerequisites, so every demo plan refuses.
    real = SimpleNamespace(
        governed_demo_data_mode_enabled=True,
        risk_model_v2_enabled=True,
        enable_real_trading=True,
    )
    assert demo_data_mode_enabled(real) is False
    with pytest.raises(ValidationError):
        demo_settings(enable_real_trading=True, governed_demo_sfp_enabled=True)
    try:
        settings = demo_settings(enable_real_trading=True)
    except ValidationError:
        return  # configuration refuses real trading next to demo mode
    assert partial_exits_enabled(settings, BLOFIN_DEMO_VENUE) is False
    loop, kwargs = demo_loop(settings)
    assert loop._plan_terms(**kwargs) == "dline_requires_demo_data_mode"
    # Internal paper and any other caller refuse without the demo opt-in.
    assert _plan_terms(**_common()) == "dline_execution_demo_only"


def test_daily_stop_still_applies_to_dline(monkeypatch):
    """The v2 5% daily stop blocks a D-line plan like any other strategy."""

    loop, kwargs = demo_loop(demo_settings())
    monkeypatch.setattr(loop, "_check_demo_equity", lambda *a, **k: "account_equity_stop")
    monkeypatch.setattr(
        "app.services.governed_blofin_demo.has_demo_entry_history", lambda *a, **k: False
    )
    monkeypatch.setattr("app.services.automated_paper_loop._load_active_profile", lambda *a: None)
    monkeypatch.setattr(
        loop,
        "_demo_leverage_settings",
        lambda *a, **k: SimpleNamespace(leverage_decimal=Decimal("10"), margin_mode="isolated"),
    )
    loop._runtime = SimpleNamespace(
        plan_store=SimpleNamespace(get_by_candidate_scope=lambda **k: None)
    )
    provider = SimpleNamespace(
        snapshot=lambda **k: loop._snapshot,
        submit=lambda *a, **k: pytest.fail("submitted"),
        read_funding_rate=lambda **k: None,
    )
    monkeypatch.setattr(loop, "_get_provider", lambda: provider)
    session = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
    proof = loop._bound(
        session,
        target=SimpleNamespace(
            organization_id=kwargs["candidate"].organization_id,
            user_id=uuid4(),
            symbol="BTCUSDT",
        ),
        candidate=kwargs["candidate"],
        assessment=None,
        window=None,
        assembled=kwargs["assembled"],
        policy=kwargs["policy"],
        account=SimpleNamespace(id=kwargs["account_id"]),
    )
    assert proof.reason_code == "account_equity_stop"


def test_every_scanned_coin_and_timeframe_has_a_bigger_trend():
    identity = first_slice_identity(timeframe=Timeframe.M30, replay=True)
    assert identity.timeframe is Timeframe.M30
    for symbol in ("BTCUSDT", "ETHUSDT", "HYPEUSDT", "TAOUSDT", "ZECUSDT"):
        for tf in (Timeframe.M15, Timeframe.M30, Timeframe.H1):
            spec = DlineLiveSpec(symbol=symbol, trigger_timeframe=tf)
            assert spec.htf_timeframe in {Timeframe.H1, Timeframe.H2, Timeframe.H4}


def test_later_scan_does_not_expire_a_traded_setup_row():
    """Expiry is shown from expires_at; no EXPIRED event may block the journal close link."""

    from app.strategy_brain.dline.runtime import detections
    from tests.test_dline_live import BE_PATH, scan

    result = scan([*COIL, *BE_PATH])
    events = detections(result, evaluated_at=START + timedelta(days=1))
    assert {e.state.value for e in events} == {"CONFIRMED"}


def test_dline_demo_defaults_trade_30m_and_1h_with_a_one_percent_minimum_stop():
    settings = Settings(_env_file=None)
    assert settings.governed_demo_dline_timeframes == ["30m", "1h"]
    assert settings.governed_demo_dline_min_stop_distance_percent == Decimal("1")
    parsed = Settings(_env_file=None, governed_demo_dline_timeframes="1H, 30m")
    assert parsed.governed_demo_dline_timeframes == ["1h", "30m"]


def test_demo_dline_15m_is_refused_by_default():
    loop, kwargs = demo_loop(demo_settings(governed_demo_dline_timeframes=["30m", "1h"]))
    assert loop._plan_terms(**kwargs) == "dline_timeframe_disabled_on_demo"


@pytest.mark.parametrize("short", [False, True])
def test_demo_dline_stop_closer_than_the_minimum_is_refused(short):
    # Stop 99.9 / 100.1. Entry 100.5 / 99.5 is 0.6% away: R:R passes, fees would not.
    loop, kwargs = demo_loop(demo_settings(), short, price="99.5" if short else "100.5")
    assert loop._plan_terms(**kwargs) == "dline_stop_too_close_for_fees"
    loop, kwargs = demo_loop(
        demo_settings(governed_demo_dline_min_stop_distance_percent=Decimal("0.5")),
        short,
        price="99.5" if short else "100.5",
    )
    assert loop._plan_terms(**kwargs) != "dline_stop_too_close_for_fees"
