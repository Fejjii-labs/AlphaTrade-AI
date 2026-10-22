"""SFP structural plans: geometry refusal and unchanged paper sizing."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.schemas.common import TradeDirection
from app.services.automated_paper_loop import AutomatedPaperLoop, _plan_terms, _quantity
from app.services.paper_fill import (
    DEFAULT_PAPER_FILL_MAJOR_SPREAD_HAIRCUT,
    DEFAULT_PAPER_FILL_SPREAD_HAIRCUT,
    paper_fill_price,
    paper_fill_spread_haircut_for_symbol,
)
from app.signal_fusion.enums import EvidenceRole
from app.strategy_brain.sfp.trade_plan import derive_sfp_trade_plan
from tests.support.phase7_trade_plan import make_world
from tests.test_sfp_detector import BEAR, BULL, evidence, spec


def test_plan_refuses_wrong_side_and_zero_distance(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.strategy_brain.sfp.trade_plan.nearest_opposing_level",
        lambda *_args, **_kwargs: SimpleNamespace(price=Decimal("90")),
    )
    refused = derive_sfp_trade_plan(
        direction=TradeDirection.LONG,
        entry=Decimal("100"),
        sweep_extreme=Decimal("95"),
        reference_price=Decimal("100"),
        stop_buffer=Decimal("0.001"),
        opposing_levels=(),
    )
    assert refused is None
    monkeypatch.setattr(
        "app.strategy_brain.sfp.trade_plan.nearest_opposing_level",
        lambda *_args, **_kwargs: SimpleNamespace(price=Decimal("110")),
    )
    zero_distance = derive_sfp_trade_plan(
        direction=TradeDirection.LONG,
        entry=Decimal("100"),
        sweep_extreme=Decimal("100"),
        reference_price=Decimal("100"),
        stop_buffer=Decimal("0"),
        opposing_levels=(),
    )
    assert zero_distance is None
    wrong_stop = derive_sfp_trade_plan(
        direction=TradeDirection.LONG,
        entry=Decimal("100"),
        sweep_extreme=Decimal("105"),
        reference_price=Decimal("100"),
        stop_buffer=Decimal("0"),
        opposing_levels=(),
    )
    assert wrong_stop is None


def _assembled(
    prices,
    *,
    quote: str,
    tick: str = "0.1",
    bid: str | None = None,
    ask: str | None = None,
    book: bool = True,
):
    bars, observations = evidence(prices)
    observed = observations[-1].observed_at
    price = Decimal(quote)
    return bars, SimpleNamespace(
        current_price=SimpleNamespace(
            price=price,
            bid=Decimal(bid) if bid is not None else (price if book else None),
            ask=Decimal(ask) if ask is not None else (price if book else None),
            source_time=observed,
            provider_symbol="BTCUSDT",
        ),
        evaluated_at=observed,
        bundle=SimpleNamespace(bars_15m=bars, tick_size=Decimal(tick)),
        assessment_command=SimpleNamespace(
            public_observations=observations,
            selected_roles=tuple(EvidenceRole.STRUCTURE for _ in observations),
        ),
    )


def _terms(
    *,
    bearish: bool,
    quote: str,
    tick: str = "0.1",
    internal_paper_sfp: bool = True,
    bid: str | None = None,
    ask: str | None = None,
    book: bool = True,
    side_aware_fill: bool = True,
    spread_haircut: Decimal = Decimal("0"),
    min_stop_atr_floor: Decimal = Decimal("0"),
    **changes: object,
):
    prices = BEAR if bearish else BULL
    _bars, assembled = _assembled(prices, quote=quote, tick=tick, bid=bid, ask=ask, book=book)
    now = assembled.evaluated_at + timedelta(seconds=1)
    world = make_world()
    candidate = world.candidate.model_copy(
        update={
            "direction": TradeDirection.SHORT if bearish else TradeDirection.LONG,
            "created_at": now - timedelta(minutes=1),
            "valid_until": now + timedelta(hours=1),
        }
    )
    policy = SimpleNamespace(
        authored_spec=spec(bearish=bearish, **changes),
        strategy_id=uuid4(),
        organization_id=candidate.organization_id,
        strategy_version_id=candidate.strategy_version_id,
    )
    return _plan_terms(
        candidate=candidate,
        assembled=assembled,  # type: ignore[arg-type]
        policy=policy,  # type: ignore[arg-type]
        account_id=uuid4(),
        equity=Decimal("10000"),
        now=now,
        eligibility_valid_until=candidate.valid_until,
        eligibility_id=uuid4(),
        internal_paper_sfp=internal_paper_sfp,
        side_aware_fill=side_aware_fill,
        spread_haircut=spread_haircut,
        min_stop_atr_floor=min_stop_atr_floor,
    )


def _assert_exit_monitor_accepts(terms: object, *, direction: TradeDirection) -> None:
    """The assembled plan is an internal paper trade the exit monitor can parse."""

    from app.db.canonical_trade_plans import PLAN_AUTHORITY_CANONICAL
    from app.schemas.trade_plan import TradePlanRevisionCreate
    from app.services.paper_exit_monitor import (
        INTERNAL_PAPER_POLICY,
        INTERNAL_PAPER_VENUE,
        parse_open_trade,
    )

    assert isinstance(terms, TradePlanRevisionCreate)
    account = terms.account_id
    organization_id = uuid4()
    user_id = uuid4()
    parsed = parse_open_trade(
        journal_trade_id=uuid4(),
        organization_id=organization_id,
        user_id=user_id,
        account_id=account,
        command_id=uuid4(),
        direction=direction,
        entry_price=terms.basis_policy.execution_price.value,
        entry_time=terms.valid_from,
        journal_symbol=terms.execution_instrument,
        journal_timeframe=terms.timeframe,
        plan_authority=PLAN_AUTHORITY_CANONICAL,
        execution_venue=INTERNAL_PAPER_VENUE,
        execution_policy_version=INTERNAL_PAPER_POLICY,
        execution_instrument=terms.execution_instrument,
        plan_organization_id=organization_id,
        plan_user_id=user_id,
        plan_account_id=account,
        revision_id=uuid4(),
        semantic_payload=terms.model_dump(mode="json"),
    )
    assert parsed is not None
    assert parsed.entry_price == terms.basis_policy.execution_price.value
    assert parsed.stop == terms.risk_and_exits.stop.value
    assert parsed.target == terms.risk_and_exits.targets[0].price.value


def test_larger_stop_floor_can_land_on_exactly_one_reward_risk(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.strategy_brain.sfp.trade_plan.nearest_opposing_level",
        lambda *_args, **_kwargs: SimpleNamespace(price=Decimal("112")),
    )
    # Percent buffer is 0.1. Two ticks of 1.0 beat both that buffer and 1*ATR.
    exact = derive_sfp_trade_plan(
        direction=TradeDirection.LONG,
        entry=Decimal("104"),
        sweep_extreme=Decimal("98"),
        reference_price=Decimal("100"),
        stop_buffer=Decimal("0.001"),
        opposing_levels=(),
        tick_size=Decimal("1"),
        min_stop_ticks=2,
        atr=Decimal("1"),
        min_stop_atr_multiple=Decimal("1"),
    )
    assert exact is not None
    assert exact.stop == Decimal("96")
    assert exact.entry - exact.stop == exact.take_profit - exact.entry
    # ATR 5 beats two ticks and the percent buffer.
    atr_wins = derive_sfp_trade_plan(
        direction=TradeDirection.LONG,
        entry=Decimal("104"),
        sweep_extreme=Decimal("98"),
        reference_price=Decimal("100"),
        stop_buffer=Decimal("0.001"),
        opposing_levels=(),
        tick_size=Decimal("1"),
        min_stop_ticks=2,
        atr=Decimal("5"),
        min_stop_atr_multiple=Decimal("1"),
    )
    assert atr_wins is not None and atr_wins.stop == Decimal("93")
    monkeypatch.setattr(
        "app.strategy_brain.sfp.trade_plan.nearest_opposing_level",
        lambda *_args, **_kwargs: SimpleNamespace(price=Decimal("110")),
    )
    missing_atr = derive_sfp_trade_plan(
        direction=TradeDirection.LONG,
        entry=Decimal("100"),
        sweep_extreme=Decimal("95"),
        reference_price=Decimal("100"),
        stop_buffer=Decimal("0.001"),
        opposing_levels=(),
        min_stop_atr_multiple=Decimal("1"),
        atr=None,
    )
    assert missing_atr is None


def test_quote_past_stop_or_target_refuses_long_and_short() -> None:
    assert _terms(bearish=False, quote="90") == "sfp_quote_beyond_plan"
    assert _terms(bearish=False, quote="97.9") == "sfp_quote_beyond_plan"
    assert _terms(bearish=False, quote="105") == "sfp_quote_beyond_plan"
    assert _terms(bearish=False, quote="106") == "sfp_quote_beyond_plan"
    assert _terms(bearish=True, quote="110") == "sfp_quote_beyond_plan"
    assert _terms(bearish=True, quote="102.1") == "sfp_quote_beyond_plan"
    assert _terms(bearish=True, quote="95") == "sfp_quote_beyond_plan"
    assert _terms(bearish=True, quote="90") == "sfp_quote_beyond_plan"


def test_quote_rebase_accepts_exactly_one_r_and_refuses_less() -> None:
    below = _terms(bearish=False, quote="104")
    assert below == "sfp_quote_reward_risk_below_minimum"
    assert _terms(bearish=False, quote="101.46") == "sfp_quote_reward_risk_below_minimum"
    assert _terms(bearish=True, quote="96") == "sfp_quote_reward_risk_below_minimum"
    exact = _terms(bearish=False, quote="101.45")
    assert not isinstance(exact, str)
    entry = exact.basis_policy.execution_price.value
    stop = exact.risk_and_exits.stop.value
    target = exact.risk_and_exits.targets[0].price.value
    assert entry == Decimal("101.45")
    assert stop == Decimal("97.9")
    assert target == Decimal("105")
    assert target - entry == entry - stop
    short = _terms(bearish=True, quote="98.55")
    assert not isinstance(short, str)
    short_entry = short.basis_policy.execution_price.value
    short_stop = short.risk_and_exits.stop.value
    short_target = short.risk_and_exits.targets[0].price.value
    assert short_entry == Decimal("98.55")
    assert short_stop == Decimal("102.1")
    assert short_target == Decimal("95")
    assert short_entry - short_target == short_stop - short_entry


def test_paper_assembly_rebases_a_valid_quote_and_keeps_sizing() -> None:
    terms = _terms(bearish=False, quote="100")
    assert not isinstance(terms, str)
    assert terms.basis_policy.execution_price.value == Decimal("100")
    assert terms.entry_zone.lower == Decimal("100")
    assert terms.risk_and_exits.stop.value == Decimal("97.9")
    assert terms.risk_and_exits.targets[0].price.value == Decimal("105")
    assert terms.risk_and_exits.targets[0].derivation.formula_id == (
        "sfp-nearest-opposing-structure"
    )
    assert terms.execution_venue == "PAPER_INTERNAL"
    distance = abs(terms.risk_and_exits.stop.value - Decimal("100"))
    assert terms.quantity.value == _quantity(
        equity=Decimal("10000"), entry=Decimal("100"), distance=distance
    )
    _assert_exit_monitor_accepts(terms, direction=TradeDirection.LONG)
    short = _terms(bearish=True, quote="100")
    assert not isinstance(short, str)
    assert short.basis_policy.execution_price.value == Decimal("100")
    assert short.risk_and_exits.stop.value == Decimal("102.1")
    assert short.risk_and_exits.targets[0].price.value == Decimal("95")
    short_distance = abs(short.risk_and_exits.stop.value - Decimal("100"))
    assert short.quantity.value == _quantity(
        equity=Decimal("10000"), entry=Decimal("100"), distance=short_distance
    )
    _assert_exit_monitor_accepts(short, direction=TradeDirection.SHORT)
    assert _terms(bearish=False, quote="100", stop_buffer="0.99") == "sfp_trade_plan_invalid"
    # Quote presence is unchanged: a missing quote still refuses before geometry.
    _bars, assembled = _assembled(BULL, quote="104")
    assembled.current_price = None
    world = make_world()
    missing = _plan_terms(
        candidate=world.candidate.model_copy(
            update={
                "direction": TradeDirection.LONG,
                "created_at": assembled.evaluated_at - timedelta(minutes=1),
                "valid_until": assembled.evaluated_at + timedelta(hours=1),
            }
        ),
        assembled=assembled,  # type: ignore[arg-type]
        policy=SimpleNamespace(  # type: ignore[arg-type]
            authored_spec=spec(),
            strategy_id=uuid4(),
            organization_id=world.candidate.organization_id,
            strategy_version_id=world.candidate.strategy_version_id,
        ),
        account_id=uuid4(),
        equity=Decimal("10000"),
        now=assembled.evaluated_at + timedelta(seconds=1),
        eligibility_valid_until=assembled.evaluated_at + timedelta(hours=1),
        eligibility_id=uuid4(),
        internal_paper_sfp=True,
    )
    assert missing == "evidence_unavailable"


def test_coarse_tick_widens_the_paper_stop() -> None:
    terms = _terms(bearish=False, quote="100", tick="1", min_stop_ticks=3)
    assert not isinstance(terms, str)
    assert terms.basis_policy.execution_price.value == Decimal("100")
    # 3 ticks of 1.0 beat the 0.1 percent buffer, so the stop is 98 - 3.
    assert terms.risk_and_exits.stop.value == Decimal("95")
    assert terms.risk_and_exits.targets[0].price.value == Decimal("105")
    entry = terms.basis_policy.execution_price.value
    assert terms.risk_and_exits.targets[0].price.value - entry == (
        entry - terms.risk_and_exits.stop.value
    )
    short = _terms(bearish=True, quote="100", tick="1", min_stop_ticks=3)
    assert not isinstance(short, str)
    assert short.risk_and_exits.stop.value == Decimal("105")
    assert short.basis_policy.execution_price.value == Decimal("100")


def test_sfp_execution_plan_stays_unauthorized_outside_internal_paper() -> None:
    assert _terms(bearish=False, quote="100", internal_paper_sfp=False) == (
        "sfp_execution_plan_not_authorized"
    )
    assert _terms(bearish=True, quote="100", internal_paper_sfp=False) == (
        "sfp_execution_plan_not_authorized"
    )


def test_side_aware_fill_uses_ask_for_long_and_bid_for_short() -> None:
    long = _terms(bearish=False, quote="100", bid="99.5", ask="100.4")
    assert not isinstance(long, str)
    assert long.basis_policy.execution_price.value == Decimal("100.4")
    short = _terms(bearish=True, quote="100", bid="99.6", ask="100.4")
    assert not isinstance(short, str)
    assert short.basis_policy.execution_price.value == Decimal("99.6")
    one_sided = _terms(bearish=False, quote="100", ask="100.2", book=False)
    assert not isinstance(one_sided, str)
    assert one_sided.basis_policy.execution_price.value == Decimal("100.2")


def test_mid_haircut_is_wider_for_non_major_bases() -> None:
    major = DEFAULT_PAPER_FILL_MAJOR_SPREAD_HAIRCUT
    wider = DEFAULT_PAPER_FILL_SPREAD_HAIRCUT
    assert paper_fill_spread_haircut_for_symbol("BTCUSDT") == major
    assert paper_fill_spread_haircut_for_symbol("ETH-USDT") == Decimal("0.0001")
    assert paper_fill_spread_haircut_for_symbol("eth/usdc") == Decimal("0.0001")
    assert paper_fill_spread_haircut_for_symbol("BTCUSDT.PERP") == Decimal("0.0001")
    assert paper_fill_spread_haircut_for_symbol("ETHFIUSDT") == wider
    assert paper_fill_spread_haircut_for_symbol("SOLUSDT") == Decimal("0.0005")
    assert paper_fill_spread_haircut_for_symbol(None) == Decimal("0.0005")
    assert paper_fill_spread_haircut_for_symbol("  ") == Decimal("0.0005")


def test_mid_only_quote_applies_the_spread_haircut_and_fails_closed() -> None:
    haircut = DEFAULT_PAPER_FILL_SPREAD_HAIRCUT
    long = _terms(bearish=False, quote="100", book=False, spread_haircut=haircut)
    assert not isinstance(long, str)
    assert long.basis_policy.execution_price.value == Decimal("100") * (Decimal(1) + haircut)
    short = _terms(bearish=True, quote="100", book=False, spread_haircut=haircut)
    assert not isinstance(short, str)
    assert short.basis_policy.execution_price.value == Decimal("100") * (Decimal(1) - haircut)
    # Exact 1R at the mid is worse than 1R after the long pays the haircut.
    assert _terms(bearish=False, quote="101.45", book=False, spread_haircut=haircut) == (
        "sfp_quote_reward_risk_below_minimum"
    )
    _bars, assembled = _assembled(BULL, quote="100", book=False)
    assembled.current_price.bid = Decimal("101")
    assembled.current_price.ask = Decimal("100")
    world = make_world()
    now = assembled.evaluated_at + timedelta(seconds=1)
    crossed = _plan_terms(
        candidate=world.candidate.model_copy(
            update={
                "direction": TradeDirection.LONG,
                "created_at": now - timedelta(minutes=1),
                "valid_until": now + timedelta(hours=1),
            }
        ),
        assembled=assembled,  # type: ignore[arg-type]
        policy=SimpleNamespace(  # type: ignore[arg-type]
            authored_spec=spec(),
            strategy_id=uuid4(),
            organization_id=world.candidate.organization_id,
            strategy_version_id=world.candidate.strategy_version_id,
        ),
        account_id=uuid4(),
        equity=Decimal("10000"),
        now=now,
        eligibility_valid_until=now + timedelta(hours=1),
        eligibility_id=uuid4(),
        internal_paper_sfp=True,
        side_aware_fill=True,
        spread_haircut=haircut,
        min_stop_atr_floor=Decimal("0"),
    )
    assert crossed == "evidence_unavailable"
    assert paper_fill_price(None, is_long=True, spread_haircut=haircut) is None
    assert (
        paper_fill_price(
            SimpleNamespace(price=None, bid=None, ask=None),
            is_long=False,
            spread_haircut=haircut,
        )
        is None
    )
    assert paper_fill_price(
        SimpleNamespace(price=Decimal("100"), bid=None, ask=None),
        is_long=True,
        spread_haircut=Decimal("0"),
    ) == Decimal("100")


def test_paper_atr_floor_widens_the_stop_and_refuses_without_atr(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.strategy_brain.sfp.detector.wilder_atr",
        lambda *_args, **_kwargs: Decimal("3"),
    )
    widened = _terms(bearish=False, quote="100", min_stop_atr_floor=Decimal("0.5"))
    assert not isinstance(widened, str)
    assert widened.risk_and_exits.stop.value == Decimal("96.5")
    authored = _terms(
        bearish=False,
        quote="100",
        min_stop_atr_floor=Decimal("0.5"),
        min_stop_atr_multiple="1",
    )
    assert not isinstance(authored, str)
    assert authored.risk_and_exits.stop.value == Decimal("95")
    monkeypatch.setattr(
        "app.strategy_brain.sfp.detector.wilder_atr",
        lambda *_args, **_kwargs: None,
    )
    assert _terms(bearish=False, quote="100", min_stop_atr_floor=Decimal("0.5")) == (
        "sfp_trade_plan_invalid"
    )


def test_internal_paper_loop_forwards_side_aware_fill_and_atr_floor(monkeypatch) -> None:
    from app.core.config import Settings

    seen: dict[str, object] = {}

    def capture(**kwargs: object) -> str:
        seen.update(kwargs)
        return "evidence_unavailable"

    monkeypatch.setattr("app.services.automated_paper_loop._plan_terms", capture)
    settings = Settings()
    loop = AutomatedPaperLoop(None, settings, SimpleNamespace())  # type: ignore[arg-type]
    result = loop._plan_terms(
        candidate=SimpleNamespace(),  # type: ignore[arg-type]
        assembled=SimpleNamespace(),  # type: ignore[arg-type]
        policy=SimpleNamespace(),  # type: ignore[arg-type]
        account_id=uuid4(),
        equity=Decimal("1"),
        now=SimpleNamespace(),  # type: ignore[arg-type]
        eligibility_valid_until=SimpleNamespace(),  # type: ignore[arg-type]
        eligibility_id=uuid4(),
    )
    assert result == "evidence_unavailable"
    assert seen["side_aware_fill"] is True
    assert seen["internal_paper_sfp"] is True
    assert seen["spread_haircut"] == settings.paper_fill_spread_haircut
    assert seen["spread_haircut"] == Decimal("0.0005")
    assert seen["min_stop_atr_floor"] == settings.sfp_min_stop_atr_multiple
    btc = SimpleNamespace(current_price=SimpleNamespace(provider_symbol="BTCUSDT"))
    loop._plan_terms(
        candidate=SimpleNamespace(),  # type: ignore[arg-type]
        assembled=btc,  # type: ignore[arg-type]
        policy=SimpleNamespace(),  # type: ignore[arg-type]
        account_id=uuid4(),
        equity=Decimal("1"),
        now=SimpleNamespace(),  # type: ignore[arg-type]
        eligibility_valid_until=SimpleNamespace(),  # type: ignore[arg-type]
        eligibility_id=uuid4(),
    )
    assert seen["spread_haircut"] == settings.paper_fill_major_spread_haircut
    assert seen["spread_haircut"] == Decimal("0.0001")
    assert settings.enable_real_trading is False


def test_demo_loop_does_not_opt_into_side_aware_fill(monkeypatch) -> None:
    from app.core.config import Settings
    from app.services.governed_blofin_demo import GovernedBloFinDemoLoop

    seen: dict[str, object] = {}

    def capture(**kwargs: object) -> str:
        seen.update(kwargs)
        return "evidence_unavailable"

    monkeypatch.setattr("app.services.governed_blofin_demo._plan_terms", capture)
    settings = Settings()
    loop = GovernedBloFinDemoLoop(None, settings, SimpleNamespace())  # type: ignore[arg-type]
    monkeypatch.setattr(
        loop,
        "_require_snapshot",
        lambda: SimpleNamespace(equity=Decimal("10"), available=Decimal("10")),
    )
    result = loop._plan_terms(
        candidate=SimpleNamespace(),  # type: ignore[arg-type]
        assembled=SimpleNamespace(),  # type: ignore[arg-type]
        policy=SimpleNamespace(),  # type: ignore[arg-type]
        account_id=uuid4(),
        equity=Decimal("20"),
        now=SimpleNamespace(),  # type: ignore[arg-type]
        eligibility_valid_until=SimpleNamespace(),  # type: ignore[arg-type]
        eligibility_id=uuid4(),
    )
    assert result == "evidence_unavailable"
    assert "side_aware_fill" not in seen
    assert "spread_haircut" not in seen
    assert "min_stop_atr_floor" not in seen
    assert "internal_paper_sfp" not in seen
