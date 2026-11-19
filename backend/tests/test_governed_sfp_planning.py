"""Opt-in SFP plans use structural exits and executable demo prices."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.providers.exchange.demo_order_book import DemoOrderBook
from app.providers.exchange.governed_blofin import DemoVenueSnapshot
from app.schemas.common import TradeDirection
from app.schemas.trade_plan import EntrySide
from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
from tests.support.phase7_trade_plan import make_world
from tests.test_sfp_detector import BEAR, BULL, spec
from tests.test_sfp_trade_plan import _assembled


@pytest.mark.parametrize(
    "value,expected", [(None, False), ("", False), ("false", False), ("true", True)]
)
def test_governed_sfp_flag(value, expected):
    kwargs = {} if value is None else {"governed_demo_sfp_enabled": value}
    assert Settings(_env_file=None, **kwargs).governed_demo_sfp_enabled is expected


@pytest.mark.parametrize("value", ["garbage", "yes", "1", 1, [], {}])
def test_governed_sfp_flag_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, governed_demo_sfp_enabled=value)


def planning(
    *, bearish=False, enabled=True, price="100", tick="0.1", equity="10000", floor="0", prices=None
):
    _, assembled = _assembled(prices or (BEAR if bearish else BULL), quote="100")
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
        authored_spec=spec(bearish=bearish),
        strategy_id=uuid4(),
        organization_id=candidate.organization_id,
        strategy_version_id=candidate.strategy_version_id,
    )
    settings = Settings(
        _env_file=None, governed_demo_sfp_enabled=enabled, sfp_min_stop_atr_multiple=Decimal(floor)
    )
    loop = GovernedBloFinDemoLoop(None, settings, SimpleNamespace(now=lambda: now))
    side = EntrySide.SELL if bearish else EntrySide.BUY
    levels = ((Decimal(price), Decimal("1000000")),)
    loop._snapshot = DemoVenueSnapshot(
        instrument="BTC-USDT",
        price=Decimal(price),
        observed_at=now,
        tick=Decimal(tick),
        lot=Decimal("0.01"),
        minimum=Decimal("0.01"),
        multiplier=Decimal("1"),
        equity=Decimal(equity),
        available=Decimal(equity),
        maximum=Decimal("1000000"),
        book=DemoOrderBook(now, side, levels, levels),
        account_observed_at=now,
    )
    loop._remaining_exposure = Decimal(equity)
    kwargs = {
        "candidate": candidate,
        "assembled": assembled,
        "policy": policy,
        "account_id": uuid4(),
        "equity": Decimal(equity),
        "now": now,
        "eligibility_valid_until": candidate.valid_until,
        "eligibility_id": uuid4(),
    }
    return loop, kwargs


@pytest.mark.parametrize("bearish", [False, True])
def test_governed_sfp_real_planner_no_haircut_and_structural_exits(bearish):
    loop, kwargs = planning(bearish=bearish)
    terms = loop._plan_terms(**kwargs)
    assert not isinstance(terms, str), terms
    entry = terms.basis_policy.execution_price.value
    stop = terms.risk_and_exits.stop.value
    target = terms.risk_and_exits.targets[0].price.value
    assert entry == Decimal("100")
    assert (target < entry < stop) if bearish else (stop < entry < target)
    assert abs(target - entry) >= abs(stop - entry)
    assert terms.quantity.value % loop._snapshot.lot == 0
    assert terms.quantity.value * entry >= 5
    assert terms.execution_venue == "BLOFIN_DEMO"
    assert terms.basis_policy.evidence_price.value == Decimal("96" if bearish else "104")


def test_governed_sfp_off_refuses_planning():
    loop, kwargs = planning(enabled=False)
    assert loop._plan_terms(**kwargs) == "sfp_execution_plan_not_authorized"


def rounded_planning(bearish=False):
    prices = (
        [*BEAR[:3], (97, 98, "95.1", 96), *BEAR[4:]]
        if bearish
        else [*BULL[:3], (103, "104.9", 102, 104), *BULL[4:]]
    )
    return planning(bearish=bearish, price="99" if bearish else "101", tick="1", prices=prices)


@pytest.mark.parametrize("bearish", [False, True])
def test_governed_sfp_rounded_sub_one_r_refuses(bearish):
    loop, kwargs = rounded_planning(bearish)
    # These executable entries are on the venue tick and clear 1R before
    # rounding. The coarser venue stop/target then reduce gross reward to 3/4R.
    fine = replace(loop._snapshot, tick=Decimal("0.1"))
    loop._snapshot = fine
    assert not isinstance(loop._plan_terms(**kwargs), str)
    loop._snapshot = replace(fine, tick=Decimal("1"))
    assert loop._plan_terms(**kwargs) == "sfp_quote_reward_risk_below_minimum"


def test_governed_sfp_stale_book_and_minimum_refuse():
    loop, kwargs = planning()
    loop._snapshot = replace(loop._snapshot, observed_at=kwargs["now"] - timedelta(seconds=10))
    assert loop._plan_terms(**kwargs) == "demo_quote_stale"
    loop, kwargs = planning(equity="0.01")
    assert loop._plan_terms(**kwargs) == "demo_size_below_minimum"


def test_governed_sfp_atr_floor_is_applied(monkeypatch):
    monkeypatch.setattr("app.strategy_brain.sfp.detector.wilder_atr", lambda *a, **k: Decimal("3"))
    loop, kwargs = planning(floor="0.5")
    terms = loop._plan_terms(**kwargs)
    assert not isinstance(terms, str), terms
    assert terms.risk_and_exits.stop.value == Decimal("96.5")
    monkeypatch.setattr("app.strategy_brain.sfp.detector.wilder_atr", lambda *a, **k: None)
    assert loop._plan_terms(**kwargs) == "sfp_trade_plan_invalid"


@pytest.mark.parametrize("percent", [None, Decimal("1.2"), Decimal("0.1")])
def test_governed_sfp_default_and_explicit_stop_risk_ceiling(percent):
    loop, kwargs = planning()
    terms = loop._plan_terms(**kwargs, fixed_risk_percent=percent)
    if percent == Decimal("0.1"):
        assert terms == "stop_loss_below_band"
        return
    assert not isinstance(terms, str), terms
    stop_cash = terms.quantity.value * abs(
        terms.basis_policy.execution_price.value - terms.risk_and_exits.stop.value
    )
    assert stop_cash <= kwargs["equity"] * (percent or Decimal("1")) / 100
    assert terms.risk_and_exits.fee_allowance.value > 0


def test_governed_sfp_kill_switch_prevents_even_planning(monkeypatch):
    loop, kwargs = planning()
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


def test_governed_sfp_real_order_path_unreachable():
    from unittest.mock import Mock

    from app.providers.exchange.blofin_execution import (
        BloFinDemoExecutionProvider,
        DemoExecutionDisabledError,
    )

    with pytest.raises(ValidationError):
        Settings(_env_file=None, governed_demo_sfp_enabled=True, enable_real_trading=True)
    client, account = Mock(), Mock()
    provider = BloFinDemoExecutionProvider(
        client, account, real_trading_enabled=True, exchange_demo_active=True
    )
    with pytest.raises(DemoExecutionDisabledError):
        provider.place_order(Mock())
    client.request.assert_not_called()
    account.get_position_mode.assert_not_called()


def test_governed_sfp_invalid_rounded_plan_is_audited_without_dispatch(monkeypatch, tmp_path):
    from unittest.mock import Mock

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.db.base import Base
    from app.db.models import AuditLog
    from app.services.automated_paper_loop import AutomatedPaperLoop, _proof

    loop, kwargs = rounded_planning()
    reason = loop._plan_terms(**kwargs)
    assert reason == "sfp_quote_reward_risk_below_minimum"
    monkeypatch.setattr(
        "app.services.governed_blofin_demo.has_demo_entry_history", lambda *a, **k: False
    )
    monkeypatch.setattr("app.services.automated_paper_loop._load_active_profile", lambda *a: None)
    monkeypatch.setattr(
        "app.services.demo_leverage_config.load_demo_leverage_config",
        lambda *a, **k: SimpleNamespace(source="default"),
    )
    monkeypatch.setattr(
        "app.services.governed_blofin_demo.demo_remaining_total_exposure",
        lambda *a, **k: Decimal("10000"),
    )
    loop._runtime = SimpleNamespace(
        plan_store=SimpleNamespace(get_by_candidate_scope=lambda **k: None)
    )
    provider = Mock()
    provider.snapshot.return_value = loop._snapshot
    monkeypatch.setattr(loop, "_get_provider", lambda: provider)
    monkeypatch.setattr(
        AutomatedPaperLoop, "_bound", lambda *a, **k: _proof(kwargs["candidate"], "skipped", reason)
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        proof = loop._bound(
            session,
            target=SimpleNamespace(
                organization_id=kwargs["candidate"].organization_id,
                user_id=uuid4(),
                symbol="BTCUSDT",
                scan_scope="sfp:15m",
            ),
            candidate=kwargs["candidate"],
            assessment=None,
            window=None,
            assembled=kwargs["assembled"],
            policy=kwargs["policy"],
            account=SimpleNamespace(id=kwargs["account_id"]),
        )
        assert proof.reason_code == reason
        session.commit()
        row = session.scalar(select(AuditLog))
        assert row is not None
        assert row.redacted_metadata["reason_code"] == reason
        provider.submit.assert_not_called()
    engine.dispose()


def test_governed_sfp_non_demo_host_never_sends():
    import httpx

    from app.providers.exchange.blofin_client import BloFinClient

    with pytest.raises(ValueError):
        BloFinClient(
            base_url="https://openapi.blofin.com",
            api_key="simulated-key",
            api_secret="simulated-secret",
            api_passphrase="simulated-pass",
            transport=httpx.MockTransport(lambda r: pytest.fail("non-demo host reached transport")),
        )


@pytest.mark.parametrize("price", ["90", "110"])
def test_governed_sfp_demo_quote_outside_structure_refuses(price):
    loop, kwargs = planning(price=price)
    assert loop._plan_terms(**kwargs) == "demo_price_invalidates_setup"


def test_governed_sfp_wrong_book_side_refuses():
    loop, kwargs = planning()
    loop._snapshot = replace(loop._snapshot, book=replace(loop._snapshot.book, side=EntrySide.SELL))
    assert loop._plan_terms(**kwargs) == "demo_quote_side_mismatch"
