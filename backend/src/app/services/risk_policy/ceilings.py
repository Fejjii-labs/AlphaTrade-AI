"""Hard code ceilings for the grade-based risk policy.

These are not settings. A profile that asks for more is rejected at load time.
Nothing in this package clamps a number down to the ceiling and continues.
"""

from __future__ import annotations

from decimal import Decimal

# Percent points: 2 means 2% of equity. Matches RiskCheckRequest.risk_percent.
HARD_MAX_RISK_PER_TRADE_PERCENT = Decimal("2")
HARD_MAX_DAILY_LOSS_PERCENT = Decimal("6")
HARD_MAX_AGGREGATE_OPEN_RISK_PERCENT = Decimal("6")
HARD_MAX_LEVERAGE = Decimal("20")

# Griller's cost band is about 20-25% of R. 25% is the code ceiling.
HARD_MAX_COST_SHARE_OF_R = Decimal("0.25")

# Notional caps are independent of leverage and are percent of equity.
HARD_MAX_NOTIONAL_PERCENT = Decimal("100")

POLICY_ID = "at-adr-107/v1"
