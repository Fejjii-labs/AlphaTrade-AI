"""LLM cost table and configured model coverage."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.config import Environment, Settings
from app.core.errors import ValidationAppError
from app.services.cost_estimator import (
    configured_llm_models,
    estimate_model_cost,
    model_has_cost_entry,
)
from app.services.llm_cost_startup import validate_llm_cost_configuration


def test_configured_models_have_cost_table_entries() -> None:
    settings = Settings(_env_file=None)
    models = configured_llm_models(settings)
    assert models == ("gpt-6-luna", "gpt-6.1-sol")
    for model in models:
        assert model_has_cost_entry(model)


def test_reasoning_tokens_do_not_change_estimated_cost() -> None:
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
    assert with_reasoning == without


def test_cached_input_tokens_billed_at_cached_rate() -> None:
    full_price = estimate_model_cost(
        model="gpt-6.1-sol",
        input_tokens=1_000_000,
        output_tokens=0,
        cached_input_tokens=0,
    )
    half_cached = estimate_model_cost(
        model="gpt-6.1-sol",
        input_tokens=1_000_000,
        output_tokens=0,
        cached_input_tokens=500_000,
    )
    assert full_price is not None and half_cached is not None
    assert half_cached < full_price
    expected_delta = (Decimal(500_000) / Decimal(1_000_000)) * (Decimal("2.00") - Decimal("0.10"))
    assert (full_price - half_cached).quantize(Decimal("0.00000001")) == expected_delta.quantize(
        Decimal("0.00000001")
    )


def test_staging_startup_fails_when_configured_model_missing_cost_table() -> None:
    settings = Settings(_env_file=None).model_copy(
        update={
            "environment": Environment.STAGING,
            "llm_model": "gpt-9-unknown-eval",
            "llm_tier_a_model": "gpt-6.1-sol",
            "llm_tier_b_model": "gpt-6-luna",
        }
    )
    with pytest.raises(ValidationAppError, match="missing cost table"):
        validate_llm_cost_configuration(settings)
