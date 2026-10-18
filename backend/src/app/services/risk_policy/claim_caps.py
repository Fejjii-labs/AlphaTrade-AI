"""Bind an active paper or demo profile onto the claim ledger.

The cash-balance fallback cap is the unverified epoch default. A profile-sized
plan uses the profile notional and daily-loss amounts instead, so a grade that
cleared ``decide_trade`` is not refused by a tighter cash ceiling. This does
not call an exchange.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import AccountRiskAccountingState
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.ledger import LedgerCaps, caps_for_ledger

PROFILE_MARGIN_ASSUMPTIONS = frozenset(
    {
        "paper-profile-isolated",
        "verified-demo-read-leverage",
    }
)


def preview_profile_claim_caps(
    session: Session,
    plan: object,
    *,
    organization_id: UUID,
    user_id: UUID,
    now: datetime,
) -> LedgerCaps | str | None:
    """Profile ceilings for a claim check. Does not write the accounting row.

    None means this plan is not profile-sized. A string is a refusal code.
    """

    exits = getattr(plan, "risk_and_exits", None)
    assumption = getattr(exits, "margin_assumption_id", None)
    if assumption not in PROFILE_MARGIN_ASSUMPTIONS:
        return None
    from app.services.audit_service import AuditService
    from app.services.risk.daily_risk_accounting import DailyRiskAccounting
    from app.services.risk.settings_service import RiskSettingsService
    from app.services.risk_policy.persistence import RiskPolicyService
    from app.services.risk_policy.profile import PolicyKind

    venue = getattr(plan, "execution_venue", "")
    kind = PolicyKind.DEMO if venue == "BLOFIN_DEMO" else PolicyKind.INTERNAL_PAPER
    profile, row, _scheduled = RiskPolicyService(session, AuditService(session)).read(
        organization_id=organization_id,
        kind=kind,
        now=now,
    )
    if row is None:
        return "risk_profile_unavailable"
    snapshot = DailyRiskAccounting(
        session,
        RiskSettingsService(session, AuditService(session)),
        clock=lambda: now,
    ).sync_from_portfolio(organization_id=organization_id, user_id=user_id)
    try:
        return caps_for_ledger(profile, snapshot.account_equity)
    except RiskPolicyRejected as exc:
        return exc.code


def accounting_view(accounting: object, caps: LedgerCaps | None) -> SimpleNamespace:
    """Predicate view. Profile caps replace the three ceilings without writing the row."""

    def field(name: str) -> object:
        if caps is not None and name in {
            "max_notional",
            "max_symbol_notional",
            "max_daily_loss",
        }:
            return getattr(caps, name)
        return getattr(accounting, name)

    return SimpleNamespace(
        daily_locked=field("daily_locked"),
        max_notional=field("max_notional"),
        reserved_notional=field("reserved_notional"),
        actual_notional=field("actual_notional"),
        max_daily_loss=field("max_daily_loss"),
        reserved_daily_loss=field("reserved_daily_loss"),
        actual_daily_loss=field("actual_daily_loss"),
        max_trade_slots=field("max_trade_slots"),
        reserved_trade_slots=field("reserved_trade_slots"),
        actual_trade_count=field("actual_trade_count"),
        max_symbol_notional=field("max_symbol_notional"),
        symbol_reserved=field("symbol_reserved"),
        symbol_actual=field("symbol_actual"),
    )


def assign_profile_claim_caps(accounting: AccountRiskAccountingState, caps: LedgerCaps) -> None:
    """Persist profile ceilings. Call only after the claim outcome is ALLOW."""

    accounting.max_notional = caps.max_notional
    accounting.max_symbol_notional = caps.max_symbol_notional
    accounting.max_daily_loss = caps.max_daily_loss
