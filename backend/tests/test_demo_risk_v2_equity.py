from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.demo_risk_v2 import demo_v2_claim_refusal, valid_demo_equity


@pytest.mark.parametrize(
    "equity",
    [
        Decimal("0"),
        Decimal("-0"),
        Decimal("-5"),
        Decimal("NaN"),
        Decimal("-NaN"),
        Decimal("Infinity"),
        None,
    ],
)
def test_v2_equity_check_refuses_unusable_equity(equity: Decimal | None) -> None:
    assert valid_demo_equity(equity) is False
    # Refused before the settings row, accounting or plan are read.
    assert (
        demo_v2_claim_refusal(None, None, None, equity)  # type: ignore[arg-type]
        == "demo_equity_unverified"
    )


def test_v2_equity_check_accepts_positive_equity() -> None:
    assert valid_demo_equity(Decimal("0.01")) is True
