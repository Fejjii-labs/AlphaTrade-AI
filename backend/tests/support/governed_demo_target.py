"""Governed demo suites: a first-slice target with room for the entry-zone allowance.

The shared Watcher fixture is a first-slice short. Its production target is
exactly 1R at the scan quote (``target = entry - distance``) with a stop about
0.145% away. The governed demo plan measures planned R:R at the worst entry its
zone authorizes (the quote less 10 bps), where that target is below 1R, so every
demo plan built from the fixture would be refused before the code under test.

These suites test the demo worker, not first-slice targets, so they move only
an exactly-1R first-slice target to ``REWARD_RISK`` at the quote (about 2R at
the widened edge). Stops, entries, other families, targets a test already set,
and production code are unchanged.
"""

from decimal import Decimal

import pytest

REWARD_RISK = Decimal("4")


def wide_first_slice_target(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import governed_blofin_demo

    original = governed_blofin_demo._plan_terms

    def plan_terms(**kwargs: object) -> object:
        plan = original(**kwargs)
        if isinstance(plan, str):
            return plan
        exits = plan.risk_and_exits
        first = exits.targets[0]
        entry = plan.basis_policy.execution_price.value
        if (
            len(exits.targets) != 1
            or first.derivation.formula_id != "paper-internal-1r"
            or first.price.value != entry + (entry - exits.stop.value)
        ):
            return plan
        target = entry + (entry - exits.stop.value) * REWARD_RISK
        price = first.price.model_copy(update={"value": target})
        return plan.model_copy(
            update={
                "risk_and_exits": exits.model_copy(
                    update={"targets": (first.model_copy(update={"price": price}),)}
                )
            }
        )

    monkeypatch.setattr(governed_blofin_demo, "_plan_terms", plan_terms)
