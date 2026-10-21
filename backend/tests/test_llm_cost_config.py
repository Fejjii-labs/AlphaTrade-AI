"""LLM cost table and configured model coverage."""

from __future__ import annotations

from decimal import Decimal

from app.core.config import Settings
from app.services.cost_estimator import (
    configured_llm_models,
    estimate_model_cost,
    model_has_cost_entry,
)


def test_configured_models_have_cost_table_entries() -> None:
    settings = Settings(_env_file=None)
    models = configured_llm_models(settings)
    assert models == ("gpt-6-luna", "gpt-6.1-sol")
    for model in models:
        assert model_has_cost_entry(model)


def test_reasoning_tokens_billed_at_output_rate() -> None:
    without = estimate_model_cost(
        model="gpt-6.1-sol",
        input_tokens=0,
        output_tokens=1_000_000,
        reasoning_tokens=0,
    )
    with_reasoning = estimate_model_cost(
        model="gpt-6.1-sol",
        input_tokens=0,
        output_tokens=1_000_000,
        reasoning_tokens=500_000,
    )
    assert without is not None and with_reasoning is not None
    assert with_reasoning > without
    delta = with_reasoning - without
    assert delta == (Decimal(500_000) / Decimal(1_000_000)) * Decimal("10.00")
