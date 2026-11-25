"""Risk-ledger symbol maps carry the same 8-dp amounts as the numeric columns.

A claim whose exposure had more than 8 decimal places used to be written to
``symbol_reserved`` unrounded while ``reserved_notional`` (Numeric(20, 8))
stored it rounded. A verified demo exit then compared the two and refused the
release ("Demo exit risk accounting is inconsistent").
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from app.schemas.execution_protocol import RiskReservationReleaseState
from app.schemas.trade_plan import ContractType, QuantityUnit
from app.services.execution_claim import charge_reservation, conservative_reservation
from app.services.execution_exposure import stored_money
from app.services.execution_fills import (
    adjust_symbol_map,
    convert_reservation_for_fill,
    fill_quote_exposure,
)

INSTRUMENT = "ZEC-USDT"


def _money(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=Decimal(value), unit="USDT")


def _plan(*, price: str, quantity: str, multiplier: str) -> SimpleNamespace:
    return SimpleNamespace(
        quantity=SimpleNamespace(value=Decimal(quantity), unit=QuantityUnit.CONTRACTS.value),
        instrument_rules=SimpleNamespace(
            contract_multiplier=Decimal(multiplier),
            contract_type=ContractType.LINEAR,
        ),
        limit_price=None,
        basis_policy=SimpleNamespace(execution_price=_money(price)),
        entry_zone=SimpleNamespace(upper=Decimal(price)),
        risk_and_exits=SimpleNamespace(
            fee_allowance=_money("0.012345678"),
            slippage_allowance=_money("0.000000004"),
            risk_budget=_money("1"),
            maximum_loss=_money("1"),
            margin_assumption_version="2",
        ),
        execution_venue="BLOFIN_DEMO",
        execution_instrument=INSTRUMENT,
    )


def _accounting() -> SimpleNamespace:
    return SimpleNamespace(
        reserved_notional=Decimal("0"),
        reserved_daily_loss=Decimal("0"),
        reserved_trade_slots=0,
        actual_notional=Decimal("0"),
        actual_trade_count=0,
        symbol_reserved={},
        symbol_actual={},
        version=1,
    )


def test_stored_money_matches_numeric_column_rounding() -> None:
    assert stored_money(Decimal("1.000000005")) == Decimal("1.00000001")
    assert stored_money(Decimal("1.000000004999")) == Decimal("1.00000000")
    assert stored_money(Decimal("2.5")) == Decimal("2.50000000")


def test_claim_notional_is_rounded_to_ledger_storage() -> None:
    # 0.37 * 0.01 * 451.237 = 1.66957690 (+ fee/slippage with 9 dp) -> 9-dp raw sum.
    plan = _plan(price="451.237", quantity="0.37", multiplier="0.01")
    raw = (
        Decimal("0.37") * Decimal("0.01") * Decimal("451.237")
        + Decimal("0.012345678")
        + Decimal("0.000000004")
    )
    assert raw.as_tuple().exponent < -8
    intent = conservative_reservation(plan)  # type: ignore[arg-type]
    assert intent.pending_notional == stored_money(raw)
    assert intent.pending_notional.as_tuple().exponent >= -8


def test_charge_writes_the_same_amount_to_column_and_symbol_map() -> None:
    plan = _plan(price="451.237", quantity="0.37", multiplier="0.01")
    intent = conservative_reservation(plan)  # type: ignore[arg-type]
    accounting = _accounting()
    accounting.symbol_reserved = {INSTRUMENT: "1.000000004"}  # legacy unrounded entry
    accounting.reserved_notional = Decimal("1.00000000")
    charge_reservation(accounting, intent)  # type: ignore[arg-type]
    assert Decimal(accounting.symbol_reserved[INSTRUMENT]) == stored_money(
        accounting.reserved_notional
    )
    assert accounting.reserved_notional == Decimal(accounting.symbol_reserved[INSTRUMENT])


def test_fill_conversion_keeps_column_and_symbol_maps_equal() -> None:
    reservation = SimpleNamespace(
        contract_type=ContractType.LINEAR.value,
        quantity_unit=QuantityUnit.CONTRACTS.value,
        contract_multiplier=Decimal("0.01"),
        instrument=INSTRUMENT,
        remaining_reserved_notional=Decimal("1.68192258"),
        release_state=RiskReservationReleaseState.CHARGED,
        release_reason=None,
        updated_at=None,
        daily_trade_allocation=1,
        converted_trade_slots=0,
    )
    exposure = fill_quote_exposure(
        reservation=reservation,  # type: ignore[arg-type]
        fill_quantity=Decimal("0.37"),
        fill_price=Decimal("451.2371"),
    )
    assert exposure == stored_money(Decimal("0.37") * Decimal("0.01") * Decimal("451.2371"))
    accounting = _accounting()
    accounting.reserved_notional = Decimal("1.68192258")
    accounting.symbol_reserved = {INSTRUMENT: "1.68192258"}
    convert_reservation_for_fill(
        reservation=reservation,  # type: ignore[arg-type]
        accounting=accounting,  # type: ignore[arg-type]
        fill_quantity=Decimal("0.37"),
        fill_price=Decimal("451.2371"),
        now=None,  # type: ignore[arg-type]
    )
    assert Decimal(accounting.symbol_reserved[INSTRUMENT]) == accounting.reserved_notional
    assert Decimal(accounting.symbol_actual[INSTRUMENT]) == accounting.actual_notional


def test_adjust_symbol_map_normalizes_legacy_unrounded_entries() -> None:
    updated = adjust_symbol_map({INSTRUMENT: "1.000000005"}, INSTRUMENT, Decimal("-0.5"))
    assert updated[INSTRUMENT] == "0.50000001"
