"""Daily loss budget: fee sign and the day a loss is charged to (audit M-11, M-12)."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.providers.exchange.governed_blofin_exit import DemoExitFill
from app.services.demo_lifecycle_resolution import demo_day_realized_pnl, demo_fill_fee_cost

FIXTURE = Path(__file__).parent / "fixtures" / "blofin_demo_recorded" / "fills_history.json"


def _recorded() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text())
    return data


def _fill(at: datetime, price: str, qty: str, fee: str | None) -> DemoExitFill:
    return DemoExitFill(
        order_id="o",
        trade_id="t",
        quantity=Decimal(qty),
        price=Decimal(price),
        occurred_at=at,
        fee=Decimal(fee) if fee is not None else None,
        reported_pnl=None,
        category="stop",
    )


def _btc_rows() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    data = _recorded()
    btc = [r for r in data["fills_history"]["data"] if r["instId"] == "BTC-USDT"]
    close = next(r for r in btc if r["side"] == "buy")
    entry = next(r for r in btc if r["side"] == "sell")
    position = data["positions_history"]["data"][0]
    return entry, close, position


def test_recorded_fills_report_paid_fees_as_positive_and_positions_as_negative() -> None:
    entry, close, position = _btc_rows()
    assert Decimal(entry["fee"]) > 0 and Decimal(close["fee"]) > 0
    assert Decimal(position["fee"]) < 0
    # The two endpoints agree once the sign is flipped.
    assert Decimal(entry["fee"]) + Decimal(close["fee"]) == -Decimal(position["fee"])


def test_fee_cost_keeps_a_rebate_and_ignores_missing_figures() -> None:
    assert demo_fill_fee_cost(Decimal("40.2684585")) == Decimal("40.2684585")
    assert demo_fill_fee_cost(Decimal("-0.5")) == Decimal("-0.5")
    assert demo_fill_fee_cost(None) == 0
    assert demo_fill_fee_cost(Decimal("NaN")) == 0


def test_recorded_btc_stop_loss_charges_gross_loss_plus_both_fees() -> None:
    entry, close, _ = _btc_rows()
    multiplier = Decimal("0.001")
    at = datetime.fromtimestamp(int(close["ts"]) / 1000, tz=UTC)
    pnl = demo_day_realized_pnl(
        day=at.date(),
        close_time=at,
        is_long=False,
        entry_price=Decimal(entry["fillPrice"]),
        entry_quantity=Decimal(entry["fillSize"]),
        contract_multiplier=multiplier,
        exit_fills=[_fill(at, close["fillPrice"], close["fillSize"], close["fee"])],
        entry_fees=[Decimal(entry["fee"])],
        funding=None,
    )
    gross = Decimal(close["fillPnl"])  # venue gross for the closing fill
    assert pnl == gross - Decimal(entry["fee"]) - Decimal(close["fee"])
    assert pnl < gross < 0  # fees make the loss bigger, never smaller


def _late_proof(day: date, fills: list[DemoExitFill], close_time: datetime) -> Decimal:
    return demo_day_realized_pnl(
        day=day,
        close_time=close_time,
        is_long=True,
        entry_price=Decimal("100"),
        entry_quantity=Decimal("2"),
        contract_multiplier=Decimal("1"),
        exit_fills=fills,
        entry_fees=[Decimal("0.1")],
        funding=Decimal("0.05"),
    )


def test_stop_filled_before_midnight_and_proven_after_is_charged_to_the_recording_day() -> None:
    stop_at = datetime(2026, 11, 26, 23, 59, tzinfo=UTC)
    pnl = _late_proof(date(2026, 11, 27), [_fill(stop_at, "95", "2", "0.1")], stop_at)
    assert pnl == Decimal("-10") - Decimal("0.1") - Decimal("0.1") - Decimal("0.05")


def test_partial_exit_on_an_earlier_day_is_not_charged_again() -> None:
    tp_at = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
    stop_at = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
    fills = [_fill(tp_at, "110", "1", "0.05"), _fill(stop_at, "95", "1", "0.05")]
    pnl = _late_proof(date(2026, 11, 27), fills, stop_at)
    # Only the stop leg (and half the entry fee) counts today.
    assert pnl == Decimal("-5") - Decimal("0.05") - Decimal("0.05") - Decimal("0.05")


def test_unverified_fees_are_left_out() -> None:
    at = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
    pnl = demo_day_realized_pnl(
        day=at.date(),
        close_time=at,
        is_long=True,
        entry_price=Decimal("100"),
        entry_quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
        exit_fills=[_fill(at, "99", "1", "0.3")],
        entry_fees=None,
        funding=None,
    )
    assert pnl == Decimal("-1")
