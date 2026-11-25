"""Phase 1 LINEAR quote-exposure conversion.

Reservation claim and fill conversion MUST use this formula. Callers pass
immutable plan or reservation instrument semantics only. Caller-supplied
conversion results are not accepted.

LINEAR CONTRACTS:
    base quantity = contract quantity * contract_multiplier
    quote exposure = base quantity * price
    therefore quote exposure = quantity * contract_multiplier * price

LINEAR BASE:
    the quantity is already base quantity
    quote exposure = quantity * price
    contract_multiplier is not applied again

INVERSE is fail-closed. QUOTE and unknown units fail closed.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.schemas.trade_plan import ContractType, QuantityUnit


class QuoteExposureError(ValueError):
    """Fail-closed conversion error with a stable reason code."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        extra: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.details: dict[str, str] = {"reason": reason, **(extra or {})}


STORED_MONEY_QUANTUM = Decimal("0.00000001")


def stored_money(value: Decimal) -> Decimal:
    """Round a ledger amount exactly as the 8-dp ``Numeric(20, 8)`` columns store it.

    PostgreSQL rounds numeric half away from zero, which is ROUND_HALF_UP here.
    Risk-ledger deltas pass through this so the per-symbol JSON maps and the
    numeric columns hold the same value after commit.
    """

    return value.quantize(STORED_MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _as_decimal(value: Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    raise QuoteExposureError(
        "Quote exposure conversion requires Decimal inputs.",
        reason="non_decimal_exposure_input",
    )


def linear_quote_exposure(
    *,
    quantity: Decimal,
    price: Decimal,
    quantity_unit: str,
    contract_multiplier: Decimal,
    contract_type: str,
) -> Decimal:
    """Return LINEAR quote exposure for one quantity/price using bound semantics."""

    qty = _as_decimal(quantity)
    px = _as_decimal(price)
    multiplier = _as_decimal(contract_multiplier)
    unit = str(quantity_unit)
    ctype = str(contract_type)

    if qty <= 0 or px <= 0:
        raise QuoteExposureError(
            "Quantity and price must be positive Decimals.",
            reason="non_positive_exposure_input",
        )
    if multiplier <= 0:
        raise QuoteExposureError(
            "contract_multiplier must be a positive Decimal.",
            reason="non_positive_contract_multiplier",
        )
    if ctype == ContractType.INVERSE.value:
        raise QuoteExposureError(
            "Inverse contract exposure conversion is not defined for Phase 1.",
            reason="inverse_contract_undefined",
        )
    if ctype != ContractType.LINEAR.value:
        raise QuoteExposureError(
            "Unknown contract type for exposure conversion.",
            reason="unsupported_contract_type",
            extra={"contract_type": ctype},
        )
    if unit == QuantityUnit.CONTRACTS.value:
        return qty * multiplier * px
    if unit == QuantityUnit.BASE.value:
        return qty * px
    raise QuoteExposureError(
        "Phase 1 exposure conversion requires CONTRACTS or BASE quantity units.",
        reason="unsupported_quantity_unit",
        extra={"quantity_unit": unit},
    )
