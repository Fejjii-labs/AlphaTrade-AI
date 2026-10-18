"""Adversarial sizing: stops, gaps, costs, tiers, budget, and concurrent trades."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.services.risk_policy.adjusters import AdjusterInputs
from app.services.risk_policy.breakers import (
    BreakerConfig,
    BreakerSnapshot,
    BreakerVerdict,
    evaluate_breakers,
)
from app.services.risk_policy.decision import decide_trade, post_fill_recheck
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.exchange_margin import unwired_margin_writer
from app.services.risk_policy.grader import GradeDecision
from app.services.risk_policy.liquidation import (
    MaintenanceTier,
    check_liquidation_buffer,
    required_buffer,
)
from app.services.risk_policy.profile import Grade, PolicyKind, profile_with
from app.services.risk_policy.sizer import SizingInput, size_concurrent, size_trade


def _order(**overrides: object) -> SizingInput:
    base: dict[str, object] = {
        "equity": Decimal("10000"),
        "entry": Decimal("100"),
        "stop": Decimal("98"),
        "direction": "long",
        "grade": Grade.A,
        "grade_risk_percent": Decimal("1"),
        "atr": Decimal("1"),
        "gap_beyond_stop": Decimal("0"),
        "fee_rate": Decimal("0"),
        "funding_rate": Decimal("0"),
        "slippage_rate": Decimal("0"),
        "remaining_daily_budget_percent": Decimal("6"),
        "open_risk_percent": Decimal("0"),
        "open_notional": Decimal("0"),
        "open_positions": 0,
        "leverage": Decimal("1"),
        "margin_mode": "isolated",
    }
    base.update(overrides)
    return SizingInput(**base)  # type: ignore[arg-type]


def _wide():
    return profile_with(
        PolicyKind.DEMO,
        max_leverage=Decimal("5"),
        max_open_positions=2,
        max_notional_per_trade_percent=Decimal("80"),
        max_aggregate_notional_percent=Decimal("90"),
        max_aggregate_open_risk_percent=Decimal("6"),
        daily_loss_limit_percent=Decimal("6"),
    )


def test_tight_stop_is_refused() -> None:
    profile = _wide()
    with pytest.raises(RiskPolicyRejected) as caught:
        size_trade(profile, _order(stop=Decimal("99.9")))
    assert caught.value.code == "stop_too_tight"


def test_gap_past_the_stop_reduces_quantity() -> None:
    profile = _wide()
    tight = size_trade(profile, _order())
    gapped = size_trade(profile, _order(gap_beyond_stop=Decimal("2")))
    assert gapped.quantity < tight.quantity
    assert gapped.adverse_distance == tight.stop_distance + Decimal("2")
    assert gapped.effective_risk_percent <= Decimal("1")


def test_fees_funding_and_slippage_sit_inside_r_and_can_refuse() -> None:
    profile = _wide()
    sized = size_trade(
        profile,
        _order(
            fee_rate=Decimal("0.001"),
            funding_rate=Decimal("-0.0005"),
            slippage_rate=Decimal("0.001"),
        ),
    )
    assert sized.cost_amount > 0
    assert sized.cost_share_of_r <= Decimal("0.20")
    assert sized.risk_amount <= Decimal("100")
    with pytest.raises(RiskPolicyRejected) as caught:
        size_trade(profile, _order(fee_rate=Decimal("0.01")))
    assert caught.value.code == "costs_exceed_r"


def test_missing_cost_input_refuses() -> None:
    with pytest.raises(RiskPolicyRejected) as caught:
        size_trade(_wide(), _order(fee_rate=None))
    assert caught.value.code == "fee_rate_missing"


def test_budget_exhaustion_and_open_risk_reduce_the_trade() -> None:
    profile = _wide()
    with pytest.raises(RiskPolicyRejected) as caught:
        size_trade(
            profile,
            _order(remaining_daily_budget_percent=Decimal("1"), open_risk_percent=Decimal("1")),
        )
    assert caught.value.code == "budget_exhausted"
    reduced = size_trade(
        profile,
        _order(remaining_daily_budget_percent=Decimal("1"), open_risk_percent=Decimal("0.4")),
    )
    assert reduced.effective_risk_percent <= Decimal("0.6")


def test_notional_cap_reduces_quantity_and_leverage_only_sets_margin() -> None:
    capped_profile = profile_with(
        PolicyKind.DEMO,
        max_leverage=Decimal("5"),
        max_notional_per_trade_percent=Decimal("10"),
        max_aggregate_notional_percent=Decimal("20"),
    )
    capped = size_trade(capped_profile, _order())
    assert capped.notional_capped
    assert capped.notional <= Decimal("1000")
    assert capped.effective_risk_percent < Decimal("1")
    wide = _wide()
    at_one = size_trade(wide, _order(leverage=Decimal("1")))
    at_two = size_trade(wide, _order(leverage=Decimal("2")))
    assert at_one.quantity == at_two.quantity
    assert at_two.margin < at_one.margin
    with pytest.raises(RiskPolicyRejected) as caught:
        size_trade(wide, _order(leverage=Decimal("6")))
    assert caught.value.code == "leverage_exceeds_ceiling"


def test_concurrent_trades_that_each_fit_are_refused_together() -> None:
    profile = profile_with(
        PolicyKind.DEMO,
        max_open_positions=2,
        max_aggregate_open_risk_percent=Decimal("1"),
        daily_loss_limit_percent=Decimal("1"),
        max_notional_per_trade_percent=Decimal("80"),
        max_aggregate_notional_percent=Decimal("90"),
    )
    first = _order(remaining_daily_budget_percent=Decimal("1"))
    second = _order(remaining_daily_budget_percent=Decimal("1"))
    assert size_trade(profile, first).effective_risk_percent > 0
    with pytest.raises(RiskPolicyRejected) as caught:
        size_concurrent(profile, (first, second))
    assert caught.value.code == "concurrent_budget_exhausted"
    with pytest.raises(RiskPolicyRejected) as sequential:
        size_trade(
            profile,
            _order(remaining_daily_budget_percent=Decimal("1"), open_risk_percent=Decimal("1")),
        )
    assert sequential.value.code == "budget_exhausted"


def test_cross_margin_and_wrong_side_stop_refuse() -> None:
    profile = _wide()
    with pytest.raises(RiskPolicyRejected) as mode:
        size_trade(profile, _order(margin_mode="cross"))
    assert mode.value.code == "margin_mode_not_isolated"
    with pytest.raises(RiskPolicyRejected) as stop:
        size_trade(profile, _order(stop=Decimal("101")))
    assert stop.value.code == "stop_on_wrong_side"


def test_liquidation_buffer_uses_the_larger_requirement_and_the_tier() -> None:
    assert required_buffer(Decimal("10"), Decimal("1")) == Decimal("20")
    assert required_buffer(Decimal("2"), Decimal("10")) == Decimal("17")
    low = (MaintenanceTier(Decimal("0"), Decimal("0.004")),)
    high = (
        MaintenanceTier(Decimal("0"), Decimal("0.004")),
        MaintenanceTier(Decimal("5000"), Decimal("0.09")),
    )
    passed = check_liquidation_buffer(
        entry=Decimal("100"),
        stop_distance=Decimal("2"),
        atr=Decimal("1"),
        leverage=Decimal("10"),
        notional=Decimal("1000"),
        margin_mode="isolated",
        tiers=low,
    )
    assert passed.accepted
    refused = check_liquidation_buffer(
        entry=Decimal("100"),
        stop_distance=Decimal("2"),
        atr=Decimal("1"),
        leverage=Decimal("10"),
        notional=Decimal("6000"),
        margin_mode="isolated",
        tiers=high,
    )
    assert refused.reason == "liquidation_inside_buffer"
    missing = check_liquidation_buffer(
        entry=Decimal("100"),
        stop_distance=Decimal("2"),
        atr=Decimal("1"),
        leverage=Decimal("10"),
        notional=Decimal("1000"),
        margin_mode="isolated",
        tiers=None,
    )
    assert missing.reason == "maintenance_tiers_unavailable"
    cross = check_liquidation_buffer(
        entry=Decimal("100"),
        stop_distance=Decimal("2"),
        atr=None,
        leverage=Decimal("10"),
        notional=Decimal("1000"),
        margin_mode="cross",
        tiers=low,
    )
    assert cross.reason == "margin_mode_not_isolated"


def test_post_fill_recheck_does_not_call_the_unwired_writer() -> None:
    outcome = post_fill_recheck(
        fill_price=Decimal("100"),
        stop_distance=Decimal("2"),
        atr=Decimal("1"),
        leverage=Decimal("10"),
        notional=Decimal("6000"),
        margin_mode="isolated",
        tiers=(
            MaintenanceTier(Decimal("0"), Decimal("0.004")),
            MaintenanceTier(Decimal("5000"), Decimal("0.09")),
        ),
    )
    assert outcome.action == "close_or_reduce"
    assert outcome.exchange_write_attempted is False
    writer = unwired_margin_writer()
    with pytest.raises(NotImplementedError):
        writer.set_leverage(symbol="BTC-USDT", leverage=Decimal("2"), margin_mode="isolated")


def test_decision_records_the_calculation_and_ignores_a_higher_ai_grade() -> None:
    from datetime import UTC, datetime

    profile = _wide()
    quiet = BreakerVerdict(
        daily_loss_active=False,
        drawdown_active=False,
        consecutive_loss_active=False,
        trip_kill_switch=False,
        paused_until=None,
        equity_peak=Decimal("10000"),
        refuse_new_trades=False,
        human_reset_required=False,
    )
    version_id = uuid4()
    decision = decide_trade(
        profile=profile,
        profile_version_id=version_id,
        grade=GradeDecision(Grade.A, "deterministic-grade/v1", Decimal("1"), Decimal("0.5"), None),
        adjusters=AdjusterInputs("normal", True, True),
        sizing=_order(),
        breaker=quiet,
        tiers=(MaintenanceTier(Decimal("0"), Decimal("0.004")),),
        ai_suggested_grade=Grade.A_PLUS,
        ai_explanation="The model prefers A+.",
    )
    assert decision.accepted
    assert decision.record is not None
    assert decision.record.grade is Grade.A
    assert decision.record.profile_version_id == version_id
    assert decision.record.grader_version == "deterministic-grade/v1"
    assert "effective_risk_percent" in decision.record.calculation
    assert decision.record.ai_explanation == "The model prefers A+."
    tripped = evaluate_breakers(
        BreakerConfig(Decimal("1"), Decimal("10"), 4),
        BreakerSnapshot(
            equity=Decimal("80"),
            equity_peak=Decimal("100"),
            reference_equity=Decimal("100"),
            realized_pnl_today=Decimal("0"),
            consecutive_losses=0,
            drawdown_latched=False,
            now=datetime(2026, 10, 18, tzinfo=UTC),
        ),
    )
    blocked = decide_trade(
        profile=profile,
        profile_version_id=version_id,
        grade=GradeDecision(Grade.A, "deterministic-grade/v1", Decimal("1"), Decimal("1"), None),
        adjusters=AdjusterInputs("normal", True, True),
        sizing=_order(),
        breaker=tripped,
        tiers=(MaintenanceTier(Decimal("0"), Decimal("0.004")),),
    )
    assert blocked.reason == "breaker_active"
    assert tripped.trip_kill_switch
