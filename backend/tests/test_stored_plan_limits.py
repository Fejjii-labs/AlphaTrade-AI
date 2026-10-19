"""Stored plans re-check breakers and current limits before approval."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Organization
from app.providers.exchange.governed_blofin_exit import VerifiedDemoExit
from app.schemas.trade_plan import EntrySide
from app.services.audit_service import AuditService
from app.services.automated_paper_loop import AutomatedPaperLoop
from app.services.canonical_paper_execution import _journal_paper_liquidation
from app.services.demo_lifecycle_resolution import (
    signed_verified_demo_pnl,
    verified_demo_funding_cash,
)
from app.services.governed_blofin_demo import DEMO_POLICY, GovernedBloFinDemoLoop
from app.services.risk_policy.governance import PolicyActor, PolicyChannel
from app.services.risk_policy.persistence import RiskPolicyService
from app.services.risk_policy.plan_sizing import (
    ActiveProfile,
    ProfileOpenBook,
    stored_plan_limit_refusal,
    stored_sizing_grade_cap,
)
from app.services.risk_policy.profile import Grade, PolicyKind, placeholder_profile, profile_with

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
ORG = uuid4()


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(Organization(id=ORG, name="Stored plan org"))
    session.commit()
    return session


def _amount(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=Decimal(value))


def _grade_marker(code: str = "4", percent: str = "1") -> SimpleNamespace:
    return SimpleNamespace(
        name="profile_sizing_grade",
        input_value=Decimal(code),
        result_value=Decimal(percent),
    )


def _plan(*, grade: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        execution_venue="BLOFIN_DEMO",
        execution_policy_version=DEMO_POLICY,
        quantity=_amount("1"),
        basis_policy=SimpleNamespace(execution_price=_amount("100")),
        risk_and_exits=SimpleNamespace(stop=_amount("90"), leverage=Decimal("2")),
        instrument_rules=SimpleNamespace(contract_multiplier=Decimal("1")),
        side=EntrySide.BUY,
        calculation_inputs=(_grade_marker(),) if grade else (),
    )


def _loop(cls: type, monkeypatch: pytest.MonkeyPatch):
    loop = object.__new__(cls)
    loop._clock = SimpleNamespace(now=lambda: NOW)
    loop._observed_kill_switch = False
    loop._experiment_context = None
    loop._scan_funding_rate = None
    envelope = SimpleNamespace(plan=_plan())
    loop._plan_service = lambda: SimpleNamespace(  # type: ignore[method-assign]
        get_scoped=lambda *_args, **_kwargs: envelope
    )

    def _approval(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("approval must not run while a stored plan is refused")

    monkeypatch.setattr("app.services.automated_paper_loop.ApprovalService", _approval)
    monkeypatch.setattr("app.services.governed_blofin_demo.ApprovalService", _approval)
    return loop


def _pause(session, *, daily: datetime | None, consecutive: datetime | None, latched: bool) -> None:
    RiskPolicyService(session, AuditService(session)).record_breaker(
        organization_id=ORG,
        account_id=uuid4(),
        equity_peak=Decimal("10000"),
        consecutive_losses=0,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        daily_paused_until=daily,
        consecutive_paused_until=consecutive,
        drawdown_latched=latched,
        now=NOW,
    )


def _execute(loop, session):
    return loop._execute_existing(
        session,
        target=SimpleNamespace(organization_id=ORG, user_id=uuid4()),
        candidate=SimpleNamespace(candidate_id=uuid4()),
        revision_id=uuid4(),
        account_id=uuid4(),
        eligibility_id=uuid4(),
        eligibility_state="eligible",
    )


def test_paper_stored_plan_refuses_a_daily_pause_set_after_sizing(monkeypatch) -> None:
    session = _session()
    _pause(session, daily=NOW + timedelta(hours=4), consecutive=None, latched=False)
    proof = _execute(_loop(AutomatedPaperLoop, monkeypatch), session)
    assert proof.stage == "blocked"
    assert proof.reason_code == "breaker_active"


def test_paper_stored_plan_refuses_a_drawdown_latch_after_the_kill_switch_is_off(
    monkeypatch,
) -> None:
    session = _session()
    _pause(session, daily=None, consecutive=None, latched=True)
    loop = _loop(AutomatedPaperLoop, monkeypatch)
    assert loop._observed_kill_switch is False
    proof = _execute(loop, session)
    assert proof.reason_code == "breaker_active"


def test_demo_stored_plan_refuses_a_consecutive_pause_set_after_sizing(monkeypatch) -> None:
    session = _session()
    _pause(session, daily=None, consecutive=NOW + timedelta(hours=4), latched=False)
    proof = _execute(_loop(GovernedBloFinDemoLoop, monkeypatch), session)
    assert proof.stage == "blocked"
    assert proof.reason_code == "breaker_active"


def test_demo_stored_plan_refuses_a_drawdown_latch_after_the_kill_switch_is_off(
    monkeypatch,
) -> None:
    session = _session()
    _pause(session, daily=None, consecutive=None, latched=True)
    loop = _loop(GovernedBloFinDemoLoop, monkeypatch)
    assert loop._observed_kill_switch is False
    proof = _execute(loop, session)
    assert proof.reason_code == "breaker_active"


def test_stored_plan_without_a_sizing_grade_is_uncomputed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.automated_paper_loop._load_active_profile",
        lambda *_args, **_kwargs: ActiveProfile(
            profile=placeholder_profile(PolicyKind.INTERNAL_PAPER),
            version_id=uuid4(),
        ),
    )
    monkeypatch.setattr(
        "app.services.automated_paper_loop._profile_open_book",
        lambda *_args, **_kwargs: ProfileOpenBook(
            open_risk_percent=Decimal("0"),
            open_notional=Decimal("0"),
            open_positions=0,
            remaining_daily_budget_percent=Decimal("1"),
        ),
    )
    monkeypatch.setattr(
        "app.services.automated_paper_loop._equity",
        lambda *_args, **_kwargs: Decimal("10000"),
    )
    session = _session()
    loop = _loop(AutomatedPaperLoop, monkeypatch)
    loop._plan_service = lambda: SimpleNamespace(  # type: ignore[method-assign]
        get_scoped=lambda *_args, **_kwargs: SimpleNamespace(plan=_plan(grade=False))
    )
    proof = _execute(loop, session)
    assert proof.reason_code == "stored_plan_limits_uncomputed"


def test_each_loop_resizes_a_stored_plan_against_the_current_book(monkeypatch) -> None:
    book = ProfileOpenBook(
        open_risk_percent=Decimal("0"),
        open_notional=Decimal("0"),
        open_positions=0,
        remaining_daily_budget_percent=Decimal("0"),
    )
    monkeypatch.setattr(
        "app.services.automated_paper_loop._load_active_profile",
        lambda *_args, **_kwargs: ActiveProfile(
            profile=placeholder_profile(PolicyKind.INTERNAL_PAPER),
            version_id=uuid4(),
        ),
    )
    monkeypatch.setattr(
        "app.services.automated_paper_loop._profile_open_book",
        lambda *_args, **_kwargs: book,
    )
    monkeypatch.setattr(
        "app.services.automated_paper_loop._equity",
        lambda *_args, **_kwargs: Decimal("10000"),
    )
    session = _session()
    for cls in (AutomatedPaperLoop, GovernedBloFinDemoLoop):
        proof = _execute(_loop(cls, monkeypatch), session)
        assert proof.reason_code == "stored_plan_exceeds_current_limits"


def test_stored_plan_cap_is_the_grade_that_sized_it_not_the_widest_grade() -> None:
    profile = profile_with(
        PolicyKind.INTERNAL_PAPER,
        risk_percent_by_grade={
            Grade.A_PLUS: Decimal("5"),
            Grade.A: Decimal("3"),
            Grade.B_PLUS: Decimal("2"),
            Grade.B: Decimal("1"),
            Grade.C: Decimal("0"),
        },
        daily_loss_limit_percent=Decimal("6"),
        max_aggregate_open_risk_percent=Decimal("6"),
        max_open_positions=2,
        max_notional_per_trade_percent=Decimal("50"),
        max_aggregate_notional_percent=Decimal("100"),
        paper_demo_data_collection=True,
    )
    book = ProfileOpenBook(
        open_risk_percent=Decimal("0"),
        open_notional=Decimal("0"),
        open_positions=0,
        remaining_daily_budget_percent=Decimal("6"),
    )
    # About 4% at the stop: inside an A+ 5% cap and outside a B 1% cap.
    plan = SimpleNamespace(
        calculation_inputs=(_grade_marker("4", "1"),),
    )
    assert stored_sizing_grade_cap(plan, profile) == Decimal("1")
    forged = SimpleNamespace(calculation_inputs=(_grade_marker("4", "5"),))
    assert stored_sizing_grade_cap(forged, profile) == Decimal("1")
    common = {
        "quantity": Decimal("1"),
        "entry": Decimal("100"),
        "stop": Decimal("20"),
        "contract_multiplier": Decimal("1"),
        "equity": Decimal("2000"),
        "book": book,
        "profile": profile,
        "execution_channel": "internal_paper",
        "include_book_buffer": False,
    }
    assert (
        stored_plan_limit_refusal(**common, sizing_grade_cap=Decimal("1"))
        == "risk_percent_exceeds_channel_ceiling"
    )
    assert stored_plan_limit_refusal(**common, sizing_grade_cap=Decimal("5")) is None
    assert (
        stored_plan_limit_refusal(**common, sizing_grade_cap=None)
        == "stored_plan_limits_uncomputed"
    )


def test_paper_unreadable_liquidation_is_not_labeled_through_the_stop() -> None:
    unread = {}
    _journal_paper_liquidation(
        unread,
        SimpleNamespace(  # type: ignore[arg-type]
            side=EntrySide.BUY,
            risk_and_exits=SimpleNamespace(
                leverage=Decimal("125"),
                stop=SimpleNamespace(value=Decimal("90")),
            ),
        ),
        Decimal("100"),
    )
    assert unread["post_fill_reason"] == "unreadable_liquidation"
    assert unread["exchange_write_attempted"] is False
    readable = {}
    _journal_paper_liquidation(
        readable,
        SimpleNamespace(  # type: ignore[arg-type]
            side=EntrySide.BUY,
            risk_and_exits=SimpleNamespace(
                leverage=Decimal("10"),
                stop=SimpleNamespace(value=Decimal("90")),
            ),
        ),
        Decimal("100"),
    )
    assert readable["post_fill_reason"] == "liquidation_through_stop"


def test_demo_close_uses_verified_funding_and_keeps_the_unverified_case_negative() -> None:
    verified = VerifiedDemoExit(
        "entry",
        "client",
        (),
        Decimal("1"),
        (),
        (),
        NOW,
        funding=Decimal("-0.25"),
    )
    assert verified.facts()["funding"] == "-0.25"
    cash = verified_demo_funding_cash(verified)
    net = signed_verified_demo_pnl(
        is_long=True,
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        reported_pnl=Decimal("20"),
        fees=Decimal("0"),
        funding=cash,
    )
    assert net == Decimal("10.25")
    missing = VerifiedDemoExit("entry", "client", (), Decimal("1"), (), (), NOW)
    assert verified_demo_funding_cash(missing) is None
    assert missing.facts()["funding"] is None
    unverified = signed_verified_demo_pnl(
        is_long=True,
        entry_price=Decimal("100"),
        exit_price=Decimal("110"),
        quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        reported_pnl=Decimal("20"),
        fees=Decimal("0"),
        funding=verified_demo_funding_cash(missing),
    )
    assert unverified < 0


def test_drawdown_reset_binding_is_account_specific() -> None:
    from app.services.risk_policy.governance import drawdown_reset_binding

    left, left_hash = drawdown_reset_binding(uuid4())
    right, right_hash = drawdown_reset_binding(uuid4())
    assert left.startswith("drawdown-reset:")
    assert left != right
    assert left_hash != right_hash
    assert PolicyActor("sofien", PolicyChannel.HUMAN, True).channel is PolicyChannel.HUMAN
