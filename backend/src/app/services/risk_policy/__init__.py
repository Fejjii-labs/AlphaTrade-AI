"""Grade-based risk policy. Real-money trading stays blocked elsewhere.

The exchange margin writer in this package is unwired. See AT-ADR-107.
"""

from app.services.risk_policy.ceilings import (
    HARD_MAX_AGGREGATE_OPEN_RISK_PERCENT,
    HARD_MAX_DAILY_LOSS_PERCENT,
    HARD_MAX_LEVERAGE,
    HARD_MAX_RISK_PER_TRADE_PERCENT,
    POLICY_ID,
)

__all__ = [
    "HARD_MAX_AGGREGATE_OPEN_RISK_PERCENT",
    "HARD_MAX_DAILY_LOSS_PERCENT",
    "HARD_MAX_LEVERAGE",
    "HARD_MAX_RISK_PER_TRADE_PERCENT",
    "POLICY_ID",
]
