"""Static model cost table for observability — not billing-grade.

Rates are informational placeholders until provider-reported costs are wired.
Provider ``output_tokens`` already include reasoning; ``reasoning_tokens`` are
telemetry-only and must not be added again for cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.config import Settings

_PRICING_SOURCE = "OpenAI pricing"
_PRICING_CHECKED = "2026-10-19"


@dataclass(frozen=True)
class ModelCostRates:
    """Per-1M token USD rates."""

    input_usd_per_million: Decimal
    cached_input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    source: str = _PRICING_SOURCE
    checked_date: str = _PRICING_CHECKED


# Historical entries retained for legacy telemetry and migration comparisons.
MODEL_COST_TABLE: dict[str, ModelCostRates] = {
    "gpt-4o-mini": ModelCostRates(
        Decimal("0.15"),
        Decimal("0.075"),
        Decimal("0.60"),
    ),
    "gpt-4o": ModelCostRates(
        Decimal("2.50"),
        Decimal("1.25"),
        Decimal("10.00"),
    ),
    "gpt-6.1-sol": ModelCostRates(
        Decimal("2.00"),
        Decimal("0.10"),
        Decimal("10.00"),
    ),
    "gpt-6-luna": ModelCostRates(
        Decimal("0.10"),
        Decimal("0.01"),
        Decimal("0.50"),
    ),
    "gpt-5.6-terra": ModelCostRates(
        Decimal("2.00"),
        Decimal("0.20"),
        Decimal("12.00"),
    ),
    # Voice / capture frontier model — estimate for guardrails only; not a router default.
    "gpt-6-astra": ModelCostRates(
        Decimal("2.00"),
        Decimal("0.10"),
        Decimal("10.00"),
    ),
}


def model_has_cost_entry(model: str | None) -> bool:
    name = (model or "").strip()
    return bool(name) and name in MODEL_COST_TABLE


def configured_llm_models(settings: Settings) -> tuple[str, ...]:
    """Models referenced by router and base provider settings (deduped, ordered)."""
    names = [
        str(settings.llm_model or "").strip(),
        str(settings.llm_tier_a_model or "").strip(),
        str(settings.llm_tier_b_model or "").strip(),
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return tuple(ordered)


def estimate_model_cost(
    *,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
    cached_input_tokens: int = 0,
) -> Decimal | None:
    """Return estimated USD cost or ``None`` when the model is unknown."""
    name = (model or "").strip()
    rates = MODEL_COST_TABLE.get(name)
    if rates is None:
        return None
    billable_input = max(input_tokens - max(cached_input_tokens, 0), 0)
    cached = min(max(cached_input_tokens, 0), input_tokens)
    input_cost = (Decimal(billable_input) / Decimal(1_000_000)) * rates.input_usd_per_million
    cached_cost = (Decimal(cached) / Decimal(1_000_000)) * rates.cached_input_usd_per_million
    del reasoning_tokens  # telemetry only; output_tokens already include reasoning
    output_cost = (
        Decimal(max(output_tokens, 0)) / Decimal(1_000_000)
    ) * rates.output_usd_per_million
    return (input_cost + cached_cost + output_cost).quantize(Decimal("0.00000001"))


def estimate_placeholder_cost(
    *,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int = 0,
    cached_input_tokens: int = 0,
) -> Decimal:
    """Backward-compatible wrapper; unknown models return zero estimated cost."""
    estimated = estimate_model_cost(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_input_tokens=cached_input_tokens,
    )
    if estimated is not None:
        return estimated
    return Decimal("0")


def rates_metadata(model: str | None) -> dict[str, Any] | None:
    name = (model or "").strip()
    rates = MODEL_COST_TABLE.get(name)
    if rates is None:
        return None
    return {
        "source": rates.source,
        "checked_date": rates.checked_date,
        "input_usd_per_million": str(rates.input_usd_per_million),
        "cached_input_usd_per_million": str(rates.cached_input_usd_per_million),
        "output_usd_per_million": str(rates.output_usd_per_million),
    }
