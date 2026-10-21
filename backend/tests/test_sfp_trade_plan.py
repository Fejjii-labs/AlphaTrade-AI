"""SFP structural plans: geometry refusal and unchanged paper sizing."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.schemas.common import TradeDirection
from app.services.automated_paper_loop import _plan_terms, _quantity
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


def _assembled(prices, *, quote: str):
    bars, observations = evidence(prices)
    observed = observations[-1].observed_at
    return bars, SimpleNamespace(
        current_price=SimpleNamespace(
            price=Decimal(quote),
            source_time=observed,
            provider_symbol="BTCUSDT",
        ),
        evaluated_at=observed,
        bundle=SimpleNamespace(bars_15m=bars, tick_size=Decimal("0.1")),
        assessment_command=SimpleNamespace(
            public_observations=observations,
            selected_roles=tuple(EvidenceRole.STRUCTURE for _ in observations),
        ),
    )


def _terms(*, bearish: bool, quote: str, stop_buffer: str | None = None):
    prices = BEAR if bearish else BULL
    _bars, assembled = _assembled(prices, quote=quote)
    now = assembled.evaluated_at + timedelta(seconds=1)
    world = make_world()
    candidate = world.candidate.model_copy(
        update={
            "direction": TradeDirection.SHORT if bearish else TradeDirection.LONG,
            "created_at": now - timedelta(minutes=1),
            "valid_until": now + timedelta(hours=1),
        }
    )
    changes = {} if stop_buffer is None else {"stop_buffer": stop_buffer}
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


def test_paper_assembly_uses_confirmation_close_not_the_quote() -> None:
    terms = _terms(bearish=False, quote="90")
    assert not isinstance(terms, str)
    assert terms.basis_policy.execution_price.value == Decimal("104")
    assert terms.entry_zone.lower == Decimal("104")
    assert terms.risk_and_exits.stop.value == Decimal("97.9")
    assert terms.risk_and_exits.targets[0].price.value == Decimal("105")
    assert terms.risk_and_exits.targets[0].derivation.formula_id == (
        "sfp-nearest-opposing-structure"
    )
    assert terms.execution_venue == "PAPER_INTERNAL"
    distance = abs(terms.risk_and_exits.stop.value - Decimal("104"))
    assert terms.quantity.value == _quantity(
        equity=Decimal("10000"), entry=Decimal("104"), distance=distance
    )
    _assert_exit_monitor_accepts(terms, direction=TradeDirection.LONG)
    short = _terms(bearish=True, quote="110")
    assert not isinstance(short, str)
    assert short.basis_policy.execution_price.value == Decimal("96")
    assert short.risk_and_exits.stop.value == Decimal("102.1")
    assert short.risk_and_exits.targets[0].price.value == Decimal("95")
    short_distance = abs(short.risk_and_exits.stop.value - Decimal("96"))
    assert short.quantity.value == _quantity(
        equity=Decimal("10000"), entry=Decimal("96"), distance=short_distance
    )
    _assert_exit_monitor_accepts(short, direction=TradeDirection.SHORT)
    assert _terms(bearish=False, quote="90", stop_buffer="0.99") == ("sfp_trade_plan_invalid")
    # Quote freshness is unchanged: a missing quote still refuses before a plan.
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
    )
    assert missing == "evidence_unavailable"
