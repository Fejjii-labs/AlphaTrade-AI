"""Exact deterministic planned-R floor, without mutating approved levels/history."""

from copy import deepcopy
from decimal import Decimal, localcontext
from types import SimpleNamespace

import pytest

from app.schemas.trade_plan import CalculationInput, TradePlanRevisionCreate
from app.services.canonical_serialization import canonical_sha256
from app.services.canonical_trade_plan_errors import CanonicalTradePlanNotEligibleError
from app.services.planned_reward_risk import (
    MANUAL_CONNECTIVITY_RR_POLICY,
    PlannedRewardRiskError,
    execution_reward_risk,
    measure_planned_reward_risk,
    planned_reward_risk,
)
from tests.support.phase7_trade_plan import make_world, plan_command, plan_terms


def terms(*, side="SELL", entry="100", stop="110", targets=("90",), fractions=("1",), runner="0"):
    payload = plan_terms(make_world().candidate).model_dump(mode="python")
    payload["side"] = side
    payload["entry_zone"].update(lower=entry, upper=entry)
    exits = payload["risk_and_exits"]
    exits["stop"]["value"] = stop
    template = exits["targets"][0]
    exits["targets"] = [
        {
            **deepcopy(template),
            "order": index + 1,
            "price": {"value": price, "unit": "USDT"},
            "quantity_fraction": fractions[index],
        }
        for index, price in enumerate(targets)
    ]
    exits["runner"].update(
        enabled=runner != "0",
        remaining_quantity_fraction=runner,
        activation_target_order=len(targets) if runner != "0" else None,
    )
    return TradePlanRevisionCreate.model_validate(payload)


@pytest.mark.parametrize("side,stop,target", [("BUY", "90", "110"), ("SELL", "110", "90")])
@pytest.mark.parametrize(
    "delta,allowed", [("0", True), ("0.000000000000000000000000000001", False)]
)
def test_exact_one_to_one_boundary_is_directional_and_context_independent(
    side, stop, target, delta, allowed
):
    with localcontext() as context:
        context.prec = 60
        worse = str(Decimal(target) + (Decimal(delta) if side == "SELL" else -Decimal(delta)))
    plan = terms(side=side, stop=stop, targets=(worse,))
    for precision in (2, 28, 60):
        with localcontext() as context:
            context.prec = precision
            if allowed:
                assert planned_reward_risk(plan).ratio == 1
            else:
                with pytest.raises(PlannedRewardRiskError) as error:
                    planned_reward_risk(plan)
                assert error.value.reason == "planned_reward_risk_below_minimum"


def test_multiple_target_allocations_and_runner_are_not_normalized_or_assumed_profitable():
    # 80% at 0.5R + 20% at 3R = exactly 1R; target order/allocations remain exact.
    plan = terms(targets=("95", "70"), fractions=("0.8", "0.2"))
    before = canonical_sha256(plan)
    assert planned_reward_risk(plan).ratio == 1
    assert canonical_sha256(plan) == before
    # A 50% runner has no promised price: 50% at 1R gives 0.5R for the position.
    with pytest.raises(PlannedRewardRiskError, match="at least 1:1"):
        planned_reward_risk(terms(targets=("90",), fractions=("0.5",), runner="0.5"))
    assert planned_reward_risk(terms(targets=("80",), fractions=("0.5",), runner="0.5")).ratio == 1


def test_connectivity_marker_cannot_exempt_a_valid_canonical_strategy_plan():
    plan = terms(targets=("95",))
    marker = CalculationInput(
        name="manual_connectivity_rr",
        input_value="1",
        result_value="1",
        unit="POLICY",
        formula_id=MANUAL_CONNECTIVITY_RR_POLICY,
        formula_version="1",
        precision=0,
        rounding_mode="EXACT",
        conservative_remainder="0",
    )
    plan = plan.model_copy(update={"calculation_inputs": (*plan.calculation_inputs, marker)})
    with pytest.raises(PlannedRewardRiskError) as caught:
        execution_reward_risk(plan)
    assert caught.value.reason == "planned_reward_risk_below_minimum"


def test_entire_entry_zone_uses_the_adverse_boundary_and_costs_are_separate():
    plan = terms()
    wider = plan.model_copy(
        update={"entry_zone": plan.entry_zone.model_copy(update={"lower": Decimal("99")})}
    )
    with pytest.raises(PlannedRewardRiskError, match="at least 1:1"):
        planned_reward_risk(wider)
    assert planned_reward_risk(plan).ratio == 1
    assert plan.risk_and_exits.fee_allowance.value > 0
    assert plan.risk_and_exits.maximum_loss.value > plan.risk_and_exits.risk_budget.value


@pytest.mark.parametrize(
    "defect",
    [
        "stop",
        "target",
        "underallocated",
        "overallocated",
        "unit",
        "nonfinite",
        "zero",
        "float",
        "precision",
        "inverse",
    ],
)
def test_invalid_inputs_fail_closed(defect):
    payload = terms().model_dump(mode="python")
    exits = payload["risk_and_exits"]
    if defect == "stop":
        exits["stop"]["value"] = "90"
    elif defect == "target":
        exits["targets"][0]["price"]["value"] = "101"
    elif defect in {"underallocated", "overallocated"}:
        exits["targets"][0]["quantity_fraction"] = "0.5" if defect == "underallocated" else "1.1"
    elif defect == "unit":
        exits["targets"][0]["price"]["unit"] = "BTC"
    elif defect in {"nonfinite", "zero", "float", "precision"}:
        exits["targets"][0]["price"]["value"] = {
            "nonfinite": "NaN",
            "zero": "0",
            "float": 90.0,
            "precision": "1e-1000",
        }[defect]
    else:
        payload["instrument_rules"]["contract_type"] = "INVERSE"
    with pytest.raises(ValueError):
        planned_reward_risk(TradePlanRevisionCreate.model_validate(payload))


def test_known_short_remains_readable_but_new_plan_is_refused_without_moving_targets():
    world = make_world()
    source = terms(entry="85111.40", stop="85720.80", targets=("84714.10",))
    assert measure_planned_reward_risk(source).ratio.quantize(Decimal("0.0001")) == Decimal(
        "0.6520"
    )
    before = canonical_sha256(source)
    with pytest.raises(
        CanonicalTradePlanNotEligibleError, match="planned_reward_risk_below_minimum"
    ):
        world.plans.create(plan_command(world, terms=source))
    assert canonical_sha256(source) == before
    assert (
        world.plans._store.get_by_candidate_scope(
            organization_id=world.candidate.organization_id,
            user_id=world.evaluation.eligibility.user_id,
            account_id=world.evaluation.eligibility.account_id,
            candidate_id=world.candidate.candidate_id,
        )
        is None
    )


def test_final_demo_dispatch_refuses_a_historical_low_reward_plan_before_any_io(monkeypatch):
    from uuid import uuid4

    from app.core.errors import TradingPolicyError
    from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
    from app.services.venue_submit_dispatcher import VenueSubmitDispatcher
    from tests.test_governed_blofin_demo import demo_settings

    world = make_world()
    with monkeypatch.context() as historical:
        historical.setattr(
            "app.services.canonical_trade_plan.planned_reward_risk", lambda _terms: None
        )
        plan = world.plans.create(
            plan_command(
                world, terms=terms(entry="85111.40", stop="85720.80", targets=("84714.10",))
            )
        ).plan
    plan = plan.model_copy(
        update={
            "execution_venue": "BLOFIN_DEMO",
            "execution_policy_version": "governed-blofin-demo/v1",
        }
    )
    # No provider constructor/network or DB read should be reached by this refusal.
    provider = object.__new__(GovernedBloFinDemoProvider)

    def forbid_io(_session):
        pytest.fail("Below-minimum plan reached dispatch IO boundary")

    monkeypatch.setattr(
        "app.services.venue_submit_dispatcher.require_idle_session_for_provider_io", forbid_io
    )
    settings = demo_settings(str(plan.account_id))
    dispatcher = VenueSubmitDispatcher(None, SimpleNamespace(_settings=settings))
    with pytest.raises(TradingPolicyError) as error:
        dispatcher.attempt_governed_demo_send(
            command_id=uuid4(),
            owner="worker",
            fencing_token=1,
            provider=provider,
            settings=settings,
            plan=plan,
        )
    assert error.value.details["reason"] == "planned_reward_risk_below_minimum"
