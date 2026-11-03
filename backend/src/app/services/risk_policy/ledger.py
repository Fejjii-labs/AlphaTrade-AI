"""Tie a risk profile to the PR 258 account ledger.

The ledger's unverified fallback sets ``max_symbol_notional`` equal to
``max_notional``. Demo auto-approval refuses that row. A bound profile must
produce a symbol cap (per-trade notional) that is strictly below aggregate
notional, and a daily-loss amount that is also strictly below aggregate
notional. Leverage does not enter these caps.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.profile import RiskPolicyProfile, validate_profile

_QUANTUM = Decimal("0.00000001")


@dataclass(frozen=True)
class LedgerCaps:
    """Absolute caps a profile would write onto an account ledger. Not written here."""

    max_symbol_notional: Decimal
    max_notional: Decimal
    max_daily_loss: Decimal

    @property
    def symbol_cap_independent(self) -> bool:
        return (
            self.max_symbol_notional < self.max_notional and self.max_daily_loss < self.max_notional
        )


def caps_for_ledger(profile: RiskPolicyProfile, equity: Decimal) -> LedgerCaps:
    """Convert profile percents into ledger money amounts."""

    validate_profile(profile)
    if not isinstance(equity, Decimal) or not equity.is_finite() or equity <= 0:
        raise RiskPolicyRejected("equity_invalid")
    symbol = (equity * profile.max_notional_per_trade_percent / Decimal("100")).quantize(
        _QUANTUM, rounding=ROUND_DOWN
    )
    aggregate = (equity * profile.max_aggregate_notional_percent / Decimal("100")).quantize(
        _QUANTUM, rounding=ROUND_DOWN
    )
    daily = (equity * profile.daily_loss_limit_percent / Decimal("100")).quantize(
        _QUANTUM, rounding=ROUND_DOWN
    )
    caps = LedgerCaps(max_symbol_notional=symbol, max_notional=aggregate, max_daily_loss=daily)
    if not caps.symbol_cap_independent:
        raise RiskPolicyRejected("symbol_cap_not_independent_of_notional")
    return caps


def ledger_symbol_cap_equals_notional(max_symbol_notional: Decimal, max_notional: Decimal) -> bool:
    """True when the 258 row still has the unverified equal-cap fallback."""

    if (
        not isinstance(max_symbol_notional, Decimal)
        or not isinstance(max_notional, Decimal)
        or not max_symbol_notional.is_finite()
        or not max_notional.is_finite()
    ):
        raise RiskPolicyRejected("ledger_caps_unreadable")
    return max_symbol_notional >= max_notional
