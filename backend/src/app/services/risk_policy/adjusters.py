"""Risk adjusters. They can only reduce. A missing input or a bad value refuses."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.risk_policy.errors import RiskPolicyRejected

_VOLATILITY: dict[str, Decimal] = {
    "low": Decimal("1"),
    "normal": Decimal("1"),
    "high": Decimal("0.5"),
    "extreme": Decimal("0.25"),
}
_MISALIGNED = Decimal("0.5")


@dataclass(frozen=True)
class AdjusterInputs:
    """None means the input was not supplied. That refuses the trade."""

    volatility_regime: str | None
    trend_aligned: bool | None
    momentum_aligned: bool | None


@dataclass(frozen=True)
class AdjusterResult:
    adjusted_risk_percent: Decimal
    volatility_multiplier: Decimal
    trend_multiplier: Decimal
    momentum_multiplier: Decimal
    product: Decimal

    def as_record(self) -> dict[str, str]:
        return {
            "adjusted_risk_percent": format(self.adjusted_risk_percent, "f"),
            "volatility_multiplier": format(self.volatility_multiplier, "f"),
            "trend_multiplier": format(self.trend_multiplier, "f"),
            "momentum_multiplier": format(self.momentum_multiplier, "f"),
            "product": format(self.product, "f"),
        }


def apply_adjusters(risk_percent: Decimal, inputs: AdjusterInputs) -> AdjusterResult:
    """Multiply risk by factors that are each at most 1."""

    if (
        inputs.volatility_regime is None
        or inputs.trend_aligned is None
        or inputs.momentum_aligned is None
    ):
        raise RiskPolicyRejected("adjuster_input_missing")
    if not isinstance(inputs.trend_aligned, bool) or not isinstance(inputs.momentum_aligned, bool):
        raise RiskPolicyRejected("adjuster_input_invalid")
    if not isinstance(risk_percent, Decimal) or not risk_percent.is_finite() or risk_percent < 0:
        raise RiskPolicyRejected("adjuster_input_invalid")
    try:
        volatility = _VOLATILITY[inputs.volatility_regime]
    except KeyError as exc:
        raise RiskPolicyRejected("adjuster_input_invalid") from exc
    trend = Decimal("1") if inputs.trend_aligned else _MISALIGNED
    momentum = Decimal("1") if inputs.momentum_aligned else _MISALIGNED
    for factor in (volatility, trend, momentum):
        if factor > 1:
            raise RiskPolicyRejected("adjuster_would_increase_risk")
    product = volatility * trend * momentum
    if product > 1:
        raise RiskPolicyRejected("adjuster_would_increase_risk")
    return AdjusterResult(
        adjusted_risk_percent=risk_percent * product,
        volatility_multiplier=volatility,
        trend_multiplier=trend,
        momentum_multiplier=momentum,
        product=product,
    )
