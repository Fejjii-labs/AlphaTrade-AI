"""Unknown strategy specs are refused on paper and demo (Griller condition 7).

The paper ``_plan_terms`` else-branch used to accept any spec whose
evaluation params looked like first-slice params, and the direction gate only
skipped non-short candidates, so an unknown short slipped through. Both must
refuse. A multi-target plan must never be coerced into the demo's single
full-size target.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.schemas.common import TradeDirection
from app.schemas.nested_continuation import NestedContinuationSpec
from app.schemas.strategy_pattern_spec import FirstSliceAuthoredPatternSpec
from app.services.automated_paper_loop import _plan_terms, paper_spec_refusal
from app.signal_fusion.first_slice_adapter import default_first_slice_evaluation_params
from app.strategy_brain.sfp.contracts import SfpSpec
from app.strategy_brain.trendpulse_1r.contracts import TrendPulseSpec

FIRST_SLICE_PARAMS = default_first_slice_evaluation_params()


class UnknownFamilySpec:
    """Stands in for a family the paper path does not know (e.g. D-line)."""


@pytest.mark.parametrize("direction", [TradeDirection.LONG, TradeDirection.SHORT])
@pytest.mark.parametrize(
    "spec",
    [UnknownFamilySpec(), TrendPulseSpec.model_construct()],
    ids=["unknown", "trendpulse"],
)
def test_unknown_specs_are_refused_in_both_directions(direction: TradeDirection, spec: object) -> None:
    assert (
        paper_spec_refusal(spec, FIRST_SLICE_PARAMS, direction) == "spec_not_paper_executable"
    )


def test_first_slice_stays_short_only() -> None:
    spec = FirstSliceAuthoredPatternSpec.model_construct()
    assert paper_spec_refusal(spec, FIRST_SLICE_PARAMS, TradeDirection.SHORT) is None
    assert (
        paper_spec_refusal(spec, FIRST_SLICE_PARAMS, TradeDirection.LONG)
        == "direction_not_paper_executable"
    )
    assert (
        paper_spec_refusal(spec, object(), TradeDirection.SHORT) == "execution_policy_unsupported"
    )


@pytest.mark.parametrize("direction", [TradeDirection.LONG, TradeDirection.SHORT])
@pytest.mark.parametrize(
    "spec",
    [NestedContinuationSpec.model_construct(), SfpSpec.model_construct()],
    ids=["nested", "sfp"],
)
def test_nested_and_sfp_keep_both_directions(direction: TradeDirection, spec: object) -> None:
    assert paper_spec_refusal(spec, object(), direction) is None


def test_plan_terms_else_branch_no_longer_coerces_an_unknown_short_to_first_slice() -> None:
    candidate = SimpleNamespace(direction=TradeDirection.SHORT)
    policy = SimpleNamespace(authored_spec=UnknownFamilySpec(), evaluation_params=FIRST_SLICE_PARAMS)
    # Everything after the spec gate is unreachable: a quote access would raise.
    assembled = SimpleNamespace()
    result = _plan_terms(
        candidate=candidate,  # type: ignore[arg-type]
        assembled=assembled,  # type: ignore[arg-type]
        policy=policy,  # type: ignore[arg-type]
        account_id=uuid4(),
        equity=Decimal("1000"),
        now=datetime(2026, 11, 16),
        eligibility_valid_until=datetime(2026, 11, 16),
        eligibility_id=uuid4(),
    )
    assert result == "spec_not_paper_executable"


def _multi_target(plan: object, *, runner: bool) -> object:
    risk = plan.risk_and_exits  # type: ignore[attr-defined]
    first = risk.targets[0]
    price = first.price.value
    long = plan.side.value == "BUY"  # type: ignore[attr-defined]
    second_price = price + (price - plan.risk_and_exits.stop.value) * Decimal("0.5")  # type: ignore[attr-defined]
    if not long:
        second_price = price - (plan.risk_and_exits.stop.value - price) * Decimal("0.5")  # type: ignore[attr-defined]
    targets = (
        first.model_copy(update={"quantity_fraction": Decimal("0.5")}),
        first.model_copy(
            update={
                "order": 2,
                "price": first.price.model_copy(update={"value": second_price}),
                "quantity_fraction": Decimal("0.3") if runner else Decimal("0.5"),
            }
        ),
    )
    runner_rules = risk.runner.model_copy(
        update={
            "enabled": runner,
            "activation_target_order": 2 if runner else None,
            "remaining_quantity_fraction": Decimal("0.2") if runner else Decimal("0"),
        }
    )
    return plan.model_copy(  # type: ignore[attr-defined]
        update={"risk_and_exits": risk.model_copy(update={"targets": targets, "runner": runner_rules})}
    )


@pytest.mark.parametrize("runner", [False, True])
def test_demo_submit_refuses_a_ladder_instead_of_a_full_size_tp1(runner: bool) -> None:
    from tests.test_demo_preflight_diagnostics import provider
    from tests.test_governed_blofin_demo import Venue
    from tests.test_governed_blofin_quote_freshness import _plan

    venue = Venue(Decimal("100000"))
    plan = _multi_target(_plan(valid_for_ms=6000), runner=runner)
    before_post = Mock()
    with pytest.raises(ValueError, match="one full-size target"):
        provider(venue.handle).submit(
            plan=plan, client_order_id="never-ladder", before_post=before_post
        )
    before_post.assert_not_called()
    assert venue.post_count == 0
