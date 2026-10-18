"""Hard code ceilings for the grade-based risk policy.

These are not settings. A profile that asks for more is rejected at load time.
Nothing in this package clamps a number down to the ceiling and continues.
"""

from __future__ import annotations

from decimal import Decimal

# Percent points: 2 means 2% of equity. Matches RiskCheckRequest.risk_percent.
# Live and every non-paper kind stay at 2%. AT-ADR-110 raises only paper and demo.
HARD_MAX_RISK_PER_TRADE_PERCENT = Decimal("2")
PAPER_DEMO_MAX_RISK_PER_TRADE_PERCENT = Decimal("5")
HARD_MAX_DAILY_LOSS_PERCENT = Decimal("6")
HARD_MAX_AGGREGATE_OPEN_RISK_PERCENT = Decimal("6")
HARD_MAX_LEVERAGE = Decimal("20")

# Griller's cost band is about 20-25% of R. 25% is the code ceiling.
HARD_MAX_COST_SHARE_OF_R = Decimal("0.25")

# Notional caps are percent of equity. Paper and demo may express margin times
# leverage (20x * 100% of equity). Other kinds stay at 100% of equity.
HARD_MAX_NOTIONAL_PERCENT = Decimal("100")
PAPER_DEMO_MAX_NOTIONAL_PERCENT = Decimal("2000")

POLICY_ID = "at-adr-107/v1"
PAPER_DEMO_KINDS = frozenset({"internal_paper", "demo"})


def _kind_value(kind: object) -> str:
    value = getattr(kind, "value", kind)
    return str(value)


def risk_ceiling_for(kind: object) -> Decimal:
    """Per-trade code ceiling. 5% is paper and demo only. Live stays at 2%."""

    if _kind_value(kind) in PAPER_DEMO_KINDS:
        return PAPER_DEMO_MAX_RISK_PER_TRADE_PERCENT
    return HARD_MAX_RISK_PER_TRADE_PERCENT


def notional_ceiling_for(kind: object) -> Decimal:
    """Notional code ceiling as a percent of equity."""

    if _kind_value(kind) in PAPER_DEMO_KINDS:
        return PAPER_DEMO_MAX_NOTIONAL_PERCENT
    return HARD_MAX_NOTIONAL_PERCENT
