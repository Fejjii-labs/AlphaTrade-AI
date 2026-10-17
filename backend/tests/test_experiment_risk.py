"""Account-wide limits and deterministic instrument rounding; no execution."""

from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest

from app.experiments.risk import (
    AccountExposure,
    ExperimentRiskPlan,
    ExperimentRiskState,
    preview_admission,
)
from app.schemas.experiments import ExperimentSource
from tests.support.experiment_fixtures import (
    experiment_engine as _experiment_engine,  # noqa: F401
)
from tests.support.experiment_fixtures import (
    experiment_world as _experiment_world,  # noqa: F401
)


def test_precision_is_bounded_before_decimal_arithmetic(experiment_world):
    w = experiment_world
    v = w.running()
    plan, state = inputs(w)
    extreme = plan.model_copy(update={"entry": Decimal("1e9999999")})
    assert preview_admission(v, extreme, state, now=w.now).reason == "unsupported_numeric_precision"
    microscopic = plan.instrument_rules.model_copy(update={"tick_size": Decimal("1e-80")})
    result = preview_admission(
        v, plan.model_copy(update={"instrument_rules": microscopic}), state, now=w.now
    )
    assert result.reason == "unsupported_numeric_precision"


def inputs(w, *, contracts=False):
    plan = ExperimentRiskPlan.model_validate(
        {
            "entry": "100",
            "structural_stop": "97",
            "target": "106",
            "direction": "LONG",
            "leverage": "2",
            "quantity_unit": "CONTRACTS" if contracts else "BASE",
            "instrument_rules": {
                "contract_multiplier": "0.1",
                "contract_type": "LINEAR",
                "base_currency": "BTC",
                "quote_currency": "USDT",
                "settlement_currency": "USDT",
                "tick_size": "0.1",
                "lot_size": "0.3",
                "minimum_quantity": "0.3",
                "minimum_notional": "1",
                "rules_version": "fixture/v1",
            },
        }
    )
    state = ExperimentRiskState.model_validate(
        {
            "organization_id": w.tenant.organization_id,
            "execution_account_id": w.account.id,
            "native_uid": None,
            "source": "internal_simulation",
            "observed_at": w.now,
            "identity_verified": True,
            "coverage_complete": True,
            "canonical_plan_verified": True,
            "canonical_risk_decision": "ALLOW",
            "kill_switch_active": False,
            "available_margin": "1000",
            "positions": [],
            "reserved_notional": "0",
            "reserved_positions": 0,
            "daily_loss": "0",
            "weekly_loss": "0",
            "drawdown": "0",
            "trades_today": 0,
            "trades_total": 0,
        }
    )
    return plan, state


@pytest.mark.parametrize("contracts,expected", [(False, "3.0"), (True, "30.0")])
def test_decimal_flooring_matches_quantity_units_and_ambient_context(
    experiment_world, contracts, expected
):
    w = experiment_world
    v = w.running()
    plan, state = inputs(w, contracts=contracts)
    results = []
    for precision in (8, 28, 100):
        with localcontext() as context:
            context.prec = precision
            results.append(preview_admission(v, plan, state, now=w.now))
    assert results[0] == results[1] == results[2]
    admitted = results[0]
    assert admitted.admissible and admitted.quantity == Decimal(expected)
    assert admitted.quantity % plan.instrument_rules.lot_size == 0
    assert admitted.quote_exposure == Decimal("300")
    assert admitted.maximum_loss == Decimal("10")
    assert admitted.management_authority is False and admitted.runtime_activated is False


def test_manual_native_exposure_counts_without_management_authority(experiment_world):
    w = experiment_world
    v = w.running()
    # Trusted preview fixtures do not claim that a native connection was queried.
    account = v.configuration.account.model_copy(
        update={"source": ExperimentSource.BLOFIN_DEMO, "native_uid": "uid-a"}
    )
    v = v.model_copy(
        update={"configuration": v.configuration.model_copy(update={"account": account})}
    )
    plan, state = inputs(w)
    state = state.model_copy(
        update={
            "source": ExperimentSource.BLOFIN_DEMO,
            "native_uid": "uid-a",
            "positions": (
                AccountExposure(
                    source="blofin_demo",
                    execution_account_id=None,
                    native_uid="uid-a",
                    quote_exposure="900",
                    origin="manual",
                ),
            ),
        }
    )
    result = preview_admission(v, plan, state, now=w.now)
    assert result.admissible and result.quantity == Decimal("0.9")
    assert result.quote_exposure + Decimal("900") <= Decimal("1000")
    assert result.management_authority is False
    exhausted = state.model_copy(update={"reserved_notional": Decimal("100")})
    assert preview_admission(v, plan, exhausted, now=w.now).reason == "exposure_limit"
    mismatched = state.model_copy(update={"native_uid": "uid-b"})
    assert preview_admission(v, plan, mismatched, now=w.now).reason == "account_mismatch"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"daily_loss": Decimal("50")}, "loss_limit"),
        ({"weekly_loss": Decimal("100")}, "loss_limit"),
        ({"drawdown": Decimal("100")}, "loss_limit"),
        ({"reserved_positions": 5}, "open_position_limit"),
        ({"trades_today": 10}, "trade_limit"),
        ({"trades_total": 20}, "trade_limit"),
        ({"reserved_notional": Decimal("1000")}, "exposure_limit"),
        ({"kill_switch_active": True}, "canonical_risk_or_authority_refusal"),
        ({"canonical_risk_decision": "BLOCK"}, "canonical_risk_or_authority_refusal"),
        ({"canonical_risk_decision": "WARN"}, "canonical_risk_or_authority_refusal"),
        ({"canonical_plan_verified": False}, "canonical_risk_or_authority_refusal"),
        ({"identity_verified": False}, "account_evidence_unverified_or_stale"),
        ({"coverage_complete": False}, "account_evidence_unverified_or_stale"),
        ({"organization_id": uuid4()}, "account_mismatch"),
        ({"execution_account_id": uuid4()}, "account_mismatch"),
        ({"native_uid": "spoof"}, "account_mismatch"),
    ],
)
def test_risk_context_refuses_final_gate_or_budget_failure(experiment_world, changes, reason):
    w = experiment_world
    v = w.running()
    plan, state = inputs(w)
    result = preview_admission(v, plan, state.model_copy(update=changes), now=w.now)
    assert not result.admissible and result.reason == reason


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"structural_stop": Decimal("97.01")}, "off_tick_structural_price"),
        ({"structural_stop": Decimal("100")}, "invalid_structural_geometry"),
        ({"target": Decimal("99")}, "invalid_structural_geometry"),
        ({"leverage": Decimal("3")}, "leverage_limit"),
        ({"quantity_unit": "QUOTE"}, "unsupported_instrument_semantics"),
    ],
)
def test_structural_prices_and_existing_semantics_are_preserved(experiment_world, changes, reason):
    w = experiment_world
    v = w.running()
    plan, state = inputs(w)
    result = preview_admission(v, plan.model_copy(update=changes), state, now=w.now)
    assert not result.admissible and result.reason == reason


def test_freshness_expiry_minimum_and_foreign_position_limits(experiment_world):
    w = experiment_world
    v = w.running()
    plan, state = inputs(w)
    assert (
        preview_admission(v, plan, state, now=w.now + timedelta(seconds=31)).reason
        == "account_evidence_unverified_or_stale"
    )
    assert preview_admission(v, plan, state, now=v.authorized_until).reason == "approval_inactive"
    rules = plan.instrument_rules.model_copy(update={"minimum_notional": Decimal("1000")})
    assert (
        preview_admission(
            v, plan.model_copy(update={"instrument_rules": rules}), state, now=w.now
        ).reason
        == "instrument_minimum_or_budget"
    )
    rules = plan.instrument_rules.model_copy(update={"contract_type": "INVERSE"})
    assert (
        preview_admission(
            v, plan.model_copy(update={"instrument_rules": rules}), state, now=w.now
        ).reason
        == "unsupported_instrument_semantics"
    )
    rules = plan.instrument_rules.model_copy(update={"settlement_currency": "BTC"})
    assert (
        preview_admission(
            v, plan.model_copy(update={"instrument_rules": rules}), state, now=w.now
        ).reason
        == "currency_mismatch"
    )
    foreign = AccountExposure(
        source="internal_simulation",
        execution_account_id=uuid4(),
        native_uid=None,
        quote_exposure="1",
        origin="manual",
    )
    assert (
        preview_admission(
            v, plan, state.model_copy(update={"positions": (foreign,)}), now=w.now
        ).reason
        == "position_identity_mismatch"
    )
